import asyncio
import json
import sys
import threading
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from eventsub_survey import (
    EventSubSurveyRunner,
    JoinRateLimiter,
    SurveyTimeoutError,
    TwitchEventSubClient,
    extract_active_author,
)
import eventsub_survey
from twitch_credentials import TwitchCredentials
from update_channels import StreamTarget


UTC = timezone.utc


def target(index: int) -> StreamTarget:
    return StreamTarget(
        broadcaster_user_id=f"channel-{index}",
        broadcaster_login=f"streamer{index}",
        rank=index,
        viewer_count=1000 - index,
        game_id="game-1",
        game_name="A Game",
        language="en",
        title="A title",
        started_at="2026-08-12T10:00:00Z",
        discovered_at="2026-08-12T12:00:00+00:00",
    )


class MemoryStorage:
    def __init__(self):
        self.json = {}
        self.parquet = {}

    def exists(self, key):
        return key in self.json or key in self.parquet

    def upload_json(self, key, data, **kwargs):
        self.json[key] = json.loads(json.dumps(data))
        return True

    def download_json(self, key):
        return self.json.get(key)

    def upload_parquet(self, key, data, **kwargs):
        self.parquet[key] = data
        return True


class Provider:
    def __init__(self, targets):
        self.targets = targets
        self.access_token = "stale"
        self.called = False

    def set_access_token(self, token):
        self.access_token = token

    def get_targets(self, limit):
        self.called = True
        assert self.access_token == "refreshed"
        return self.targets[:limit]


class FakeClient:
    def __init__(self, *, failures=None, lose_attempts=0, messages=None):
        self.connection_lost = asyncio.Event()
        self.message_callback = lambda *_: None
        self.failures = dict(failures or {})
        self.lose_attempts = lose_attempts
        self.messages = messages or {}
        self.opened = False
        self.closed = False
        self.force_aborted = False
        self.accepting = False
        self.batch_attempt = 0
        self.subscribe_calls = {}
        self.teardowns = []

    async def open(self):
        self.opened = True

    def current_access_token(self):
        assert self.opened
        return "refreshed"

    def prepare_batch(self, channel_ids):
        self.batch_attempt += 1
        self.connection_lost.clear()

    async def subscribe(self, stream):
        channel_id = stream.broadcaster_user_id
        self.subscribe_calls[channel_id] = self.subscribe_calls.get(channel_id, 0) + 1
        remaining = self.failures.get(channel_id, 0)
        if remaining:
            self.failures[channel_id] = remaining - 1
            raise RuntimeError("subscription rejected secret-token")
        return f"subscription-{channel_id}-{self.batch_attempt}"

    def set_accepting_messages(self, value):
        self.accepting = value
        if not value:
            return
        for channel_id, authors in self.messages.get(self.batch_attempt, {}).items():
            for chatter_id, login in authors:
                self.message_callback(channel_id, chatter_id, login)
        if self.batch_attempt <= self.lose_attempts:
            self.connection_lost.set()

    async def teardown(self, subscription_ids):
        self.teardowns.append(list(subscription_ids))

    async def close(self):
        self.closed = True

    def force_abort(self):
        self.force_aborted = True


def run(coro):
    return asyncio.run(coro)


def test_join_limiter_enforces_twenty_per_rolling_ten_seconds():
    clock = [0.0]
    sleeps = []

    async def advance(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    limiter = JoinRateLimiter(
        monotonic=lambda: clock[0],
        sleep=advance,
    )

    async def collect():
        for _ in range(21):
            await limiter.acquire()

    run(collect())
    assert sleeps == [pytest.approx(10.0)]


def test_author_filter_excludes_bot_shared_chat_and_inactive_rooms():
    def message(channel="one", chatter="person", login="SomeName", source=None):
        return SimpleNamespace(
            broadcaster=SimpleNamespace(id=channel),
            chatter=SimpleNamespace(id=chatter, name=login),
            source_broadcaster=(SimpleNamespace(id=source) if source else None),
            # An exploding body proves the privacy filter never accesses text.
            text=property(lambda _: (_ for _ in ()).throw(AssertionError())),
        )

    assert extract_active_author(
        message(), active_channel_ids={"one"}, bot_user_id="bot"
    ) == ("one", "person", "somename")
    assert extract_active_author(
        message(chatter="bot"), active_channel_ids={"one"}, bot_user_id="bot"
    ) is None
    assert extract_active_author(
        message(source="other"), active_channel_ids={"one"}, bot_user_id="bot"
    ) is None
    assert extract_active_author(
        message(channel="two"), active_channel_ids={"one"}, bot_user_id="bot"
    ) is None


def test_survey_writes_aligned_unique_authors_and_empty_success():
    storage = MemoryStorage()
    client = FakeClient(
        messages={
            1: {
                "channel-1": [
                    ("user-2", "BOB"),
                    ("user-1", "Alice"),
                    ("user-1", "AliceRenamed"),
                ]
            }
        }
    )
    provider = Provider([target(1), target(2)])
    runner = EventSubSurveyRunner(
        storage=storage,
        target_provider=provider,
        client=client,
        top_channels_limit=2,
        batch_size=2,
        window_seconds=300,
        timeout_seconds=10,
        sleep=lambda _: asyncio.sleep(0),
    )

    manifest = run(runner.run("session_one"))

    assert manifest["status"] == "complete"
    assert manifest["planned"] == 2
    assert manifest["completed"] == 2
    assert manifest["zero_authors"] == 1
    assert client.closed
    assert len(client.teardowns) == 1

    key = next(iter(storage.parquet))
    assert key.endswith("session=session_one/batch=01.parquet")
    rows = pd.read_parquet(BytesIO(storage.parquet[key])).to_dict(orient="records")
    first, second = rows
    assert json.loads(first["chatter_ids_json"]) == ["user-1", "user-2"]
    assert json.loads(first["chatters_json"]) == ["alicerenamed", "bob"]
    assert first["unique_author_count"] == 2
    assert first["sample_duration_seconds"] == 300
    assert second["collection_status"] == "completed"
    assert json.loads(second["chatters_json"]) == []
    assert not any("message" in column for column in rows[0])


def test_subscription_failure_is_retried_without_rendering_exception_text():
    storage = MemoryStorage()
    client = FakeClient(failures={"channel-1": 3})
    runner = EventSubSurveyRunner(
        storage=storage,
        target_provider=Provider([target(1), target(2)]),
        client=client,
        top_channels_limit=2,
        batch_size=2,
        window_seconds=1,
        timeout_seconds=10,
        subscription_retries=2,
        sleep=lambda _: asyncio.sleep(0),
        secrets=("secret-token",),
    )

    manifest = run(runner.run("subscription_failure"))
    assert manifest["status"] == "complete_with_errors"
    assert manifest["completed"] == 1
    assert manifest["failed"] == 1
    assert client.subscribe_calls["channel-1"] == 3

    frame = pd.read_parquet(BytesIO(next(iter(storage.parquet.values()))))
    failed = frame.loc[frame["channel_id"] == "channel-1"].iloc[0]
    assert failed["collection_status"] == "subscription_failed"
    assert "secret-token" not in failed["failure_reason"]
    assert failed["failure_reason"] == "runtime_error"


def test_safe_error_never_renders_exception_and_only_keeps_numeric_status():
    sentinel = "rotated-access-token-must-never-be-rendered"

    class DangerousError(RuntimeError):
        def __str__(self):
            raise AssertionError("_safe_error must not render exceptions")

    error = DangerousError()
    error.status = 401
    error.extra = {"message": sentinel}

    assert eventsub_survey._safe_error(error) == "runtime_error;http_status=401"

    error.status = sentinel
    assert eventsub_survey._safe_error(error) == "runtime_error"


def test_post_rotation_secret_cannot_reach_batch_or_manifest():
    sentinel = "newly-rotated-token-not-known-at-runner-start"

    class RotatingFailureClient(FakeClient):
        async def subscribe(self, stream):
            self.subscribe_calls[stream.broadcaster_user_id] = 1
            raise RuntimeError(f"Twitch rejected rotated token {sentinel}")

    storage = MemoryStorage()
    runner = EventSubSurveyRunner(
        storage=storage,
        target_provider=Provider([target(1)]),
        client=RotatingFailureClient(),
        top_channels_limit=1,
        batch_size=1,
        window_seconds=1,
        timeout_seconds=10,
        subscription_retries=0,
        sleep=lambda _: asyncio.sleep(0),
        secrets=("old-token",),
    )

    manifest = run(runner.run("rotated_token_failure"))
    frame = pd.read_parquet(BytesIO(next(iter(storage.parquet.values()))))
    persisted = json.dumps(manifest, sort_keys=True) + frame.to_json()
    assert sentinel not in persisted
    assert frame.iloc[0]["failure_reason"] == "runtime_error"


def test_websocket_loss_restarts_batch_and_discards_interrupted_data():
    storage = MemoryStorage()
    client = FakeClient(
        lose_attempts=1,
        messages={
            1: {"channel-1": [("discard-me", "Old")]},
            2: {"channel-1": [("keep-me", "New")]},
        },
    )
    runner = EventSubSurveyRunner(
        storage=storage,
        target_provider=Provider([target(1)]),
        client=client,
        top_channels_limit=1,
        batch_size=1,
        window_seconds=1,
        timeout_seconds=10,
        batch_retries=2,
        sleep=lambda _: asyncio.sleep(0),
    )

    manifest = run(runner.run("retry_batch"))
    assert manifest["status"] == "complete"
    assert client.subscribe_calls["channel-1"] == 2
    row = pd.read_parquet(BytesIO(next(iter(storage.parquet.values())))).iloc[0]
    assert json.loads(row["chatter_ids_json"]) == ["keep-me"]
    assert json.loads(row["chatters_json"]) == ["new"]


def test_subscription_revocation_restarts_batch_and_discards_partial_data():
    storage = MemoryStorage()

    class RevocationSignalClient(FakeClient):
        def set_accepting_messages(self, value):
            self.accepting = value
            if not value:
                return
            if self.batch_attempt == 1:
                self.message_callback("channel-1", "discard-me", "Old")
                # TwitchEventSubClient.event_subscription_revoked translates
                # an active room revocation into this conservative signal.
                self.connection_lost.set()
            else:
                self.message_callback("channel-1", "keep-me", "New")

    client = RevocationSignalClient()
    runner = EventSubSurveyRunner(
        storage=storage,
        target_provider=Provider([target(1)]),
        client=client,
        top_channels_limit=1,
        batch_size=1,
        window_seconds=1,
        timeout_seconds=10,
        batch_retries=2,
        sleep=lambda _: asyncio.sleep(0),
    )

    manifest = run(runner.run("revoked_subscription"))

    assert manifest["status"] == "complete"
    assert client.subscribe_calls["channel-1"] == 2
    row = pd.read_parquet(BytesIO(next(iter(storage.parquet.values())))).iloc[0]
    assert json.loads(row["chatter_ids_json"]) == ["keep-me"]


def test_same_terminal_session_is_idempotent():
    storage = MemoryStorage()
    key = "raw/snapshots/v2/date=2026-08-12/session=fixed/manifest.json"
    storage.json[key] = {"status": "complete", "survey_session_id": "fixed"}
    client = FakeClient()
    runner = EventSubSurveyRunner(
        storage=storage,
        target_provider=Provider([target(1)]),
        client=client,
        now=lambda: datetime(2026, 8, 12, tzinfo=UTC),
    )

    result = run(runner.run("fixed"))
    assert result["status"] == "complete"
    assert not client.opened


def test_timeout_marks_manifest_partial_and_closes_client():
    storage = MemoryStorage()
    client = FakeClient()

    async def never(_):
        await asyncio.Event().wait()

    runner = EventSubSurveyRunner(
        storage=storage,
        target_provider=Provider([target(1)]),
        client=client,
        top_channels_limit=1,
        batch_size=1,
        window_seconds=300,
        timeout_seconds=0.01,
        sleep=never,
    )

    with pytest.raises(SurveyTimeoutError):
        run(runner.run("timeout_session"))
    manifest = next(value for key, value in storage.json.items() if key.endswith("manifest.json"))
    assert manifest["status"] == "partial"
    assert manifest["failure_reason"] == "survey_timeout"
    assert client.closed


def test_shutdown_interrupts_active_window_and_marks_partial():
    storage = MemoryStorage()
    client = FakeClient()
    stop = [False]

    async def request_stop(_):
        stop[0] = True
        await asyncio.Event().wait()

    runner = EventSubSurveyRunner(
        storage=storage,
        target_provider=Provider([target(1)]),
        client=client,
        top_channels_limit=1,
        batch_size=1,
        window_seconds=300,
        timeout_seconds=10,
        sleep=request_stop,
        should_stop=lambda: stop[0],
        stop_poll_seconds=0.001,
    )

    from eventsub_survey import SurveyInterruptedError

    with pytest.raises(SurveyInterruptedError):
        run(runner.run("stopped_session"))
    manifest = next(value for key, value in storage.json.items() if key.endswith("manifest.json"))
    assert manifest["status"] == "partial"
    assert manifest["failure_reason"] == "shutdown_requested"
    assert client.teardowns
    assert client.closed


def test_shutdown_persists_partial_before_slow_batch_teardown_finishes():
    storage = MemoryStorage()
    stop = [False]

    class SlowTeardownClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.teardown_started = asyncio.Event()
            self.release_teardown = asyncio.Event()

        async def teardown(self, subscription_ids):
            self.teardowns.append(list(subscription_ids))
            self.teardown_started.set()
            await self.release_teardown.wait()

    client = SlowTeardownClient()

    async def request_stop(_):
        stop[0] = True
        await asyncio.Event().wait()

    runner = EventSubSurveyRunner(
        storage=storage,
        target_provider=Provider([target(1)]),
        client=client,
        top_channels_limit=1,
        batch_size=1,
        window_seconds=300,
        timeout_seconds=10,
        sleep=request_stop,
        should_stop=lambda: stop[0],
        stop_poll_seconds=0.001,
        teardown_timeout_seconds=5,
    )

    async def scenario():
        task = asyncio.create_task(runner.run("stopped_before_teardown"))
        await asyncio.wait_for(client.teardown_started.wait(), timeout=0.5)
        manifest = next(
            value
            for key, value in storage.json.items()
            if key.endswith("manifest.json")
        )
        assert manifest["status"] == "partial"
        assert manifest["failure_reason"] == "shutdown_requested"
        client.release_teardown.set()
        with pytest.raises(eventsub_survey.SurveyInterruptedError):
            await asyncio.wait_for(task, timeout=0.5)

    run(scenario())


def test_sigterm_during_successful_teardown_aborts_and_marks_partial_promptly():
    storage = MemoryStorage()
    stop = [False]

    class SlowTeardownClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.teardown_started = asyncio.Event()

        async def teardown(self, subscription_ids):
            self.teardowns.append(list(subscription_ids))
            self.teardown_started.set()
            await asyncio.Event().wait()

    client = SlowTeardownClient()
    runner = EventSubSurveyRunner(
        storage=storage,
        target_provider=Provider([target(1)]),
        client=client,
        top_channels_limit=1,
        batch_size=1,
        window_seconds=1,
        timeout_seconds=10,
        sleep=lambda _: asyncio.sleep(0),
        should_stop=lambda: stop[0],
        stop_poll_seconds=0.001,
        teardown_timeout_seconds=5,
    )

    async def scenario():
        task = asyncio.create_task(runner.run("stop_during_teardown"))
        await asyncio.wait_for(client.teardown_started.wait(), timeout=0.5)
        manifest = next(
            value
            for key, value in storage.json.items()
            if key.endswith("manifest.json")
        )
        assert manifest["status"] == "running"

        # Model SIGTERM arriving only after the successful window has ended
        # and remote subscription deletion is already blocked.
        stop[0] = True
        with pytest.raises(eventsub_survey.SurveyInterruptedError):
            await asyncio.wait_for(task, timeout=0.5)

    run(scenario())

    manifest = next(
        value for key, value in storage.json.items() if key.endswith("manifest.json")
    )
    assert manifest["status"] == "partial"
    assert manifest["failure_reason"] == "shutdown_requested"
    assert client.force_aborted


def test_sigterm_during_discovery_returns_promptly_with_partial_manifest():
    storage = MemoryStorage()
    client = FakeClient()
    stop = [False]
    discovery_started = threading.Event()
    release_discovery = threading.Event()

    class BlockingProvider(Provider):
        def get_targets(self, limit):
            discovery_started.set()
            release_discovery.wait()
            return self.targets[:limit]

    runner = EventSubSurveyRunner(
        storage=storage,
        target_provider=BlockingProvider([target(1)]),
        client=client,
        top_channels_limit=1,
        timeout_seconds=10,
        should_stop=lambda: stop[0],
        stop_poll_seconds=0.001,
        operation_timeout_seconds=10,
        cleanup_timeout_seconds=0.05,
    )

    async def scenario():
        task = asyncio.create_task(runner.run("stop_during_discovery"))
        while not discovery_started.is_set():
            await asyncio.sleep(0)
        stop[0] = True
        with pytest.raises(eventsub_survey.SurveyInterruptedError):
            await asyncio.wait_for(task, timeout=0.5)

    try:
        run(scenario())
    finally:
        release_discovery.set()

    manifest = next(
        value for key, value in storage.json.items() if key.endswith("manifest.json")
    )
    assert manifest["status"] == "partial"
    assert manifest["failure_reason"] == "shutdown_requested"
    assert client.closed


def test_sigterm_during_subscribe_returns_promptly_with_partial_manifest():
    storage = MemoryStorage()
    stop = [False]

    class BlockingSubscribeClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.subscribe_started = asyncio.Event()

        async def subscribe(self, stream):
            self.subscribe_started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    client = BlockingSubscribeClient()
    runner = EventSubSurveyRunner(
        storage=storage,
        target_provider=Provider([target(1)]),
        client=client,
        top_channels_limit=1,
        batch_size=1,
        timeout_seconds=10,
        should_stop=lambda: stop[0],
        stop_poll_seconds=0.001,
        operation_timeout_seconds=10,
        cleanup_timeout_seconds=0.05,
    )

    async def scenario():
        task = asyncio.create_task(runner.run("stop_during_subscribe"))
        await asyncio.wait_for(client.subscribe_started.wait(), timeout=0.5)
        stop[0] = True
        with pytest.raises(eventsub_survey.SurveyInterruptedError):
            await asyncio.wait_for(task, timeout=0.5)

    run(scenario())

    manifest = next(
        value for key, value in storage.json.items() if key.endswith("manifest.json")
    )
    assert manifest["status"] == "partial"
    assert manifest["failure_reason"] == "shutdown_requested"
    assert client.teardowns == [[]]
    assert client.closed


def test_global_timeout_persists_partial_before_bounded_hanging_close():
    storage = MemoryStorage()

    class HangingCloseClient(FakeClient):
        async def close(self):
            self.closed = True
            await asyncio.Event().wait()

    client = HangingCloseClient()

    async def never(_seconds):
        await asyncio.Event().wait()

    runner = EventSubSurveyRunner(
        storage=storage,
        target_provider=Provider([target(1)]),
        client=client,
        top_channels_limit=1,
        batch_size=1,
        window_seconds=300,
        timeout_seconds=0.01,
        sleep=never,
        cleanup_timeout_seconds=0.01,
    )

    with pytest.raises(SurveyTimeoutError):
        run(asyncio.wait_for(runner.run("timeout_hanging_close"), timeout=0.5))

    manifest = next(
        value for key, value in storage.json.items() if key.endswith("manifest.json")
    )
    assert manifest["status"] == "partial"
    assert manifest["failure_reason"] == "survey_timeout"
    assert client.closed
    assert client.force_aborted


def test_close_token_flush_failure_overwrites_complete_manifest_and_fails_task():
    storage = MemoryStorage()
    sentinel = "last-moment-rotated-secret"

    class FlushFailureClient(FakeClient):
        def __init__(self):
            super().__init__()
            self._tokens_flushed_for_close = False

        async def flush_tokens(self):
            return None

        async def close(self):
            self.closed = True
            raise RuntimeError(f"Secrets Manager rejected {sentinel}")

    client = FlushFailureClient()
    runner = EventSubSurveyRunner(
        storage=storage,
        target_provider=Provider([target(1)]),
        client=client,
        top_channels_limit=1,
        batch_size=1,
        window_seconds=1,
        timeout_seconds=10,
        sleep=lambda _: asyncio.sleep(0),
        cleanup_timeout_seconds=0.05,
    )

    with pytest.raises(eventsub_survey.SurveyError, match="credentials"):
        run(runner.run("close_token_flush_failure"))

    manifest = next(
        value for key, value in storage.json.items() if key.endswith("manifest.json")
    )
    assert manifest["status"] == "partial"
    assert manifest["failure_reason"] == "credential_persist_failed"
    assert manifest["error"] == "runtime_error"
    assert sentinel not in json.dumps(manifest)


def test_transport_only_close_timeout_keeps_completed_manifest():
    storage = MemoryStorage()

    class HangingTransportClient(FakeClient):
        def __init__(self):
            super().__init__()
            self._tokens_flushed_for_close = False

        async def flush_tokens(self):
            return None

        async def close(self):
            self.closed = True
            self._tokens_flushed_for_close = True
            await asyncio.Event().wait()

    client = HangingTransportClient()
    runner = EventSubSurveyRunner(
        storage=storage,
        target_provider=Provider([target(1)]),
        client=client,
        top_channels_limit=1,
        batch_size=1,
        window_seconds=1,
        timeout_seconds=10,
        sleep=lambda _: asyncio.sleep(0),
        cleanup_timeout_seconds=0.01,
    )

    manifest = run(runner.run("transport_close_timeout"))

    assert manifest["status"] == "complete"
    assert client.force_aborted


def test_hanging_batch_teardown_is_bounded_and_forces_local_abort():
    storage = MemoryStorage()

    class HangingTeardownClient(FakeClient):
        async def teardown(self, subscription_ids):
            self.teardowns.append(list(subscription_ids))
            await asyncio.Event().wait()

    client = HangingTeardownClient()
    runner = EventSubSurveyRunner(
        storage=storage,
        target_provider=Provider([target(1)]),
        client=client,
        top_channels_limit=1,
        batch_size=1,
        window_seconds=1,
        timeout_seconds=10,
        sleep=lambda _: asyncio.sleep(0),
        cleanup_timeout_seconds=0.01,
        teardown_timeout_seconds=0.01,
    )

    with pytest.raises(eventsub_survey.SurveyError, match="cleanup timed out"):
        run(asyncio.wait_for(runner.run("hanging_teardown"), timeout=0.5))

    manifest = next(
        value for key, value in storage.json.items() if key.endswith("manifest.json")
    )
    assert manifest["status"] == "partial"
    assert client.force_aborted


def test_batch_size_over_100_is_rejected():
    with pytest.raises(ValueError, match="between 1 and 100"):
        EventSubSurveyRunner(
            storage=MemoryStorage(),
            target_provider=Provider([]),
            client=FakeClient(),
            batch_size=101,
        )


@pytest.mark.parametrize(
    ("validated", "message"),
    [
        (
            SimpleNamespace(
                client_id="other-app", user_id="bot-id", scopes=["user:read:chat"]
            ),
            "different application",
        ),
        (
            SimpleNamespace(
                client_id="app-id", user_id="other-user", scopes=["user:read:chat"]
            ),
            "different bot user",
        ),
        (
            SimpleNamespace(
                client_id="app-id",
                user_id="bot-id",
                scopes=["user:read:chat", "user:write:chat"],
            ),
            "only the user:read:chat scope",
        ),
    ],
)
def test_twitch_client_rejects_wrong_identity_or_scope_before_survey(
    monkeypatch, validated, message
):
    credentials = TwitchCredentials(
        client_id="app-id",
        client_secret="client-secret",
        bot_user_id="bot-id",
        access_token="access-token",
        refresh_token="refresh-token",
    )
    store = SimpleNamespace(save_tokens=lambda *_: None)
    client = TwitchEventSubClient(credentials, store)

    async def login(**_kwargs):
        return None

    async def add_token(*_args):
        return validated

    monkeypatch.setattr(client, "login", login)
    monkeypatch.setattr(client, "add_token", add_token)

    with pytest.raises(ValueError, match=message):
        run(client.open())


def test_twitch_client_close_flushes_refresh_that_happened_immediately_before_close(
    monkeypatch,
):
    credentials = TwitchCredentials(
        client_id="app-id",
        client_secret="client-secret",
        bot_user_id="bot-id",
        access_token="old-access",
        refresh_token="old-refresh",
    )
    calls = []

    def save_tokens(access_token, refresh_token):
        calls.append(("persist", access_token, refresh_token))

    client = TwitchEventSubClient(
        credentials, SimpleNamespace(save_tokens=save_tokens)
    )
    # Model TwitchIO rotating its managed pair immediately before ECS asks the
    # client to close, before event_token_refreshed has persisted it.
    client._http._tokens["bot-id"] = {
        "token": "latest-access",
        "refresh": "latest-refresh",
    }

    async def parent_close(_self, **options):
        calls.append(("close", options))

    monkeypatch.setattr(eventsub_survey._TwitchClientBase, "close", parent_close)

    run(client.close())

    assert calls == [
        ("persist", "latest-access", "latest-refresh"),
        ("close", {"save_tokens": False}),
    ]


def test_survey_cli_never_logs_raw_exception_or_token(monkeypatch, capsys, tmp_path):
    import main as app_main

    sentinel = "secret-access-token-that-must-never-reach-logs"

    async def fail_survey(_config):
        raise RuntimeError(f'Invalid or expired token: "{sentinel}"')

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(app_main, "mode_survey", fail_survey)
    monkeypatch.setattr(sys, "argv", ["main.py", "survey", "default"])

    assert app_main.main() == 1
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "SURVEY_TASK_FAILED" in combined
    assert sentinel not in combined
    assert "Invalid or expired token" not in combined

    log_text = (tmp_path / "logs" / "pipeline.log").read_text(encoding="utf-8")
    assert "SURVEY_TASK_FAILED" in log_text
    assert sentinel not in log_text


def test_analysis_cli_returns_nonzero_when_scheduled_analysis_fails(
    monkeypatch, tmp_path
):
    import main as app_main

    async def fail_analysis(_config):
        return False

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(app_main, "mode_analyze", fail_analysis)
    monkeypatch.setattr(sys, "argv", ["main.py", "analyze", "default"])

    assert app_main.main() == 1


def test_twitch_client_creates_zero_retry_socket_before_subscribing(monkeypatch):
    credentials = TwitchCredentials(
        client_id="app-id",
        client_secret="client-secret",
        bot_user_id="bot-id",
        access_token="access-token",
        refresh_token="refresh-token",
    )
    client = TwitchEventSubClient(
        credentials, SimpleNamespace(save_tokens=lambda *_: None)
    )
    created = []

    class ZeroRetrySocket:
        def __init__(self, **kwargs):
            created.append(kwargs)
            self.session_id = "survey-socket"
            self._reconnect_attempts = kwargs["reconnect_attempts"]

        async def connect(self, **kwargs):
            assert kwargs == {"fail_once": True}

        async def close(self):
            return None

    subscribe_calls = []

    async def subscribe_websocket(_payload, **kwargs):
        subscribe_calls.append(kwargs)
        return {"data": [{"id": "subscription-1"}]}

    monkeypatch.setattr(
        eventsub_survey, "_TwitchEventSubWebsocket", ZeroRetrySocket
    )
    monkeypatch.setattr(client, "subscribe_websocket", subscribe_websocket)

    subscription_id = run(client.subscribe(target(1)))

    assert subscription_id == "subscription-1"
    assert len(created) == 1
    assert created[0]["reconnect_attempts"] == 0
    assert created[0]["client"] is client
    assert created[0]["token_for"] == "bot-id"
    assert subscribe_calls == [
        {"as_bot": True, "socket_id": "survey-socket"}
    ]


def test_active_chat_subscription_revocation_signals_batch_loss():
    credentials = TwitchCredentials(
        client_id="app-id",
        client_secret="client-secret",
        bot_user_id="bot-id",
        access_token="access-token",
        refresh_token="refresh-token",
    )
    client = TwitchEventSubClient(
        credentials, SimpleNamespace(save_tokens=lambda *_: None)
    )
    client.prepare_batch({"channel-1"})

    revoked = SimpleNamespace(
        type="channel.chat.message",
        raw={
            "condition": {
                "broadcaster_user_id": "channel-1",
                "user_id": "bot-id",
            }
        },
    )
    run(client.event_subscription_revoked(revoked))

    assert client.connection_lost.is_set()

    client.prepare_batch({"channel-1"})
    revoked.raw["condition"]["broadcaster_user_id"] = "another-channel"
    run(client.event_subscription_revoked(revoked))
    assert not client.connection_lost.is_set()


def test_teardown_deletes_every_active_batch_chat_subscription(monkeypatch):
    credentials = TwitchCredentials(
        client_id="app-id",
        client_secret="client-secret",
        bot_user_id="bot-id",
        access_token="access-token",
        refresh_token="refresh-token",
    )
    client = TwitchEventSubClient(
        credentials, SimpleNamespace(save_tokens=lambda *_: None)
    )
    client.prepare_batch({"channel-1", "channel-2"})

    def subscription(channel_id, type_="channel.chat.message"):
        return SimpleNamespace(
            type=SimpleNamespace(value=type_),
            condition={
                "broadcaster_user_id": channel_id,
                "user_id": "bot-id",
            },
        )

    monkeypatch.setattr(
        client,
        "websocket_subscriptions",
        lambda: {
            # The originally returned ID has already been replaced.
            "replacement-1": subscription("channel-1"),
            "replacement-2": subscription("channel-2"),
            "another-event": subscription("channel-1", "channel.follow"),
            "another-room": subscription("channel-999"),
        },
    )
    deleted = []

    async def delete_websocket_subscription(subscription_id, *, force=False):
        deleted.append((subscription_id, force))
        if subscription_id == "replacement-2":
            # TwitchIO dispatches this when deletion empties and closes the
            # socket. It must remain an intentional close, not a batch loss.
            await client.event_websocket_closed(SimpleNamespace())

    monkeypatch.setattr(
        client, "delete_websocket_subscription", delete_websocket_subscription
    )

    run(client.teardown(["original-id"]))

    assert deleted == [
        ("original-id", False),
        ("replacement-1", False),
        ("replacement-2", False),
    ]
    assert not client.connection_lost.is_set()
    assert client._active_channel_ids == set()
