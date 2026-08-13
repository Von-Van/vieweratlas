"""One-shot, equal-window Twitch EventSub survey collection.

This module deliberately stores *presence*, not chat: message text, fragments,
message IDs, and per-message timestamps never enter the survey state or output.
The only author data retained is one Twitch user ID and normalized login per
channel and survey session.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence

import pandas as pd

from update_channels import ChannelTargetProvider, StreamTarget

try:  # Keep analysis/import-only workflows usable before dependencies install.
    import twitchio
    from twitchio import eventsub
    from twitchio.eventsub.websockets import Websocket as _TwitchEventSubWebsocket
except (ImportError, AttributeError):  # TwitchIO 2 has no EventSub namespace.
    twitchio = None
    eventsub = None
    _TwitchEventSubWebsocket = None


logger = logging.getLogger(__name__)
UTC = timezone.utc
_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_TERMINAL_MANIFEST_STATES = {"complete", "complete_with_errors", "partial"}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def make_survey_session_id(now: datetime | None = None) -> str:
    """Return a path-safe ID precise enough for independently started tasks."""
    value = (now or _utc_now()).astimezone(UTC)
    return value.strftime("%Y%m%dT%H%M%S%fZ")


def _safe_error(error: BaseException, secrets: Sequence[str] = ()) -> str:
    """Return a credential-safe error category without rendering ``error``.

    Twitch API exceptions may embed access and refresh tokens in their message,
    route, or extra response fields.  Redacting the credentials known when the
    runner started is insufficient because TwitchIO can rotate them later.  The
    privacy boundary is therefore structural: this function never calls
    ``str(error)`` or includes arbitrary exception attributes.  Only a fixed
    category and, when directly available as a number, an HTTP status survive.

    ``secrets`` remains in the signature for backwards compatibility.  It is
    intentionally unused because no dynamic exception text is emitted.
    """
    del secrets

    if isinstance(error, (asyncio.TimeoutError, TimeoutError)):
        category = "timeout"
    elif isinstance(error, PermissionError):
        category = "permission_error"
    elif isinstance(error, ConnectionError):
        category = "connection_error"
    elif isinstance(error, ValueError):
        category = "validation_error"
    elif isinstance(error, OSError):
        category = "io_error"
    elif type(error).__module__.split(".", 1)[0] == "twitchio":
        category = "twitch_api_error"
    elif isinstance(error, RuntimeError):
        category = "runtime_error"
    else:
        category = "operational_error"

    # Read the instance dictionary rather than a property/getattr hook.  Only
    # digits in the valid HTTP response range are allowed into persisted data.
    try:
        error_state = object.__getattribute__(error, "__dict__")
    except Exception:
        error_state = {}
    raw_status = error_state.get("status")
    if isinstance(raw_status, int) and not isinstance(raw_status, bool):
        status = raw_status
    elif isinstance(raw_status, str) and raw_status.isascii() and raw_status.isdigit():
        status = int(raw_status)
    else:
        status = 0

    if 100 <= status <= 599:
        return f"{category};http_status={status}"
    return category


async def _run_in_daemon_thread(
    function: Callable[..., Any], *args: Any, thread_name: str
) -> Any:
    """Await sync I/O without adding a blocking default-executor shutdown."""
    loop = asyncio.get_running_loop()
    result: asyncio.Future[Any] = loop.create_future()

    def publish(value: Any = None, error: BaseException | None = None) -> None:
        if result.done():
            return
        if error is None:
            result.set_result(value)
        else:
            result.set_exception(error)

    def invoke() -> None:
        try:
            value = function(*args)
        except BaseException as exc:
            callback = lambda error=exc: publish(error=error)
        else:
            callback = lambda returned=value: publish(value=returned)
        try:
            loop.call_soon_threadsafe(callback)
        except RuntimeError:
            # The task timed out and its event loop has already exited.
            pass

    threading.Thread(target=invoke, name=thread_name, daemon=True).start()
    return await result


def extract_active_author(
    payload: Any, *, active_channel_ids: set[str], bot_user_id: str
) -> tuple[str, str, str] | None:
    """Return the minimum presence tuple for an eligible chat event.

    Keeping this extraction pure makes the privacy boundary and Shared Chat
    behavior independently testable. No message/body attribute is accessed.
    """
    broadcaster_id = str(payload.broadcaster.id)
    if broadcaster_id not in active_channel_ids:
        return None

    source = getattr(payload, "source_broadcaster", None)
    if source is not None and str(source.id) != broadcaster_id:
        return None

    chatter_id = str(payload.chatter.id)
    if not chatter_id or chatter_id == bot_user_id:
        return None

    login = str(getattr(payload.chatter, "name", "") or "").strip().lower()
    return broadcaster_id, chatter_id, login


class SurveyError(RuntimeError):
    """Base error raised by a survey that could not complete."""


class SurveyTimeoutError(SurveyError):
    """The two-hour survey safety limit was reached."""


class SurveyInterruptedError(SurveyError):
    """A graceful shutdown was requested between batches."""


class JoinRateLimiter:
    """Sliding-window limiter for Twitch's 20 joins per 10 seconds rule."""

    def __init__(
        self,
        max_joins: int = 20,
        period_seconds: float = 10.0,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if max_joins <= 0 or period_seconds <= 0:
            raise ValueError("Join limiter values must be positive")
        self.max_joins = max_joins
        self.period_seconds = period_seconds
        self._monotonic = monotonic
        self._sleep = sleep
        self._joins: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = self._monotonic()
                while self._joins and now - self._joins[0] >= self.period_seconds:
                    self._joins.popleft()

                if len(self._joins) < self.max_joins:
                    self._joins.append(now)
                    return

                wait_for = max(0.0, self.period_seconds - (now - self._joins[0]))
                await self._sleep(wait_for)


class EventSubClient(Protocol):
    """Narrow transport seam used by the survey orchestrator and its tests."""

    connection_lost: asyncio.Event
    message_callback: Callable[[str, str, str], None]

    async def open(self) -> None: ...

    async def subscribe(self, target: StreamTarget) -> str: ...

    async def teardown(self, subscription_ids: Sequence[str]) -> None: ...

    async def close(self) -> None: ...

    async def flush_tokens(self) -> None: ...

    def force_abort(self) -> None: ...

    def current_access_token(self) -> str: ...

    def prepare_batch(self, channel_ids: set[str]) -> None: ...

    def set_accepting_messages(self, value: bool) -> None: ...


if twitchio is not None and eventsub is not None:
    _TwitchClientBase = twitchio.Client
else:  # pragma: no cover - exercised only in a pre-upgrade environment.
    _TwitchClientBase = object


class TwitchEventSubClient(_TwitchClientBase):
    """TwitchIO 3.2.2 WebSocket adapter for ``channel.chat.message``."""

    def __init__(self, credentials: Any, credential_store: Any) -> None:
        if twitchio is None or eventsub is None:
            raise RuntimeError("TwitchIO 3.2.2 is required for EventSub surveys")

        self.credentials = credentials
        self.credential_store = credential_store
        self.bot_user_id = credentials.bot_user_id
        self.message_callback: Callable[[str, str, str], None] = lambda *_: None
        self.connection_lost = asyncio.Event()
        self._active_channel_ids: set[str] = set()
        self._accepting_messages = False
        self._intentional_teardown = False
        self._token_persist_lock = asyncio.Lock()
        self._persisted_token_pair = (
            credentials.access_token,
            credentials.refresh_token,
        )
        self._survey_socket_id: str | None = None
        self._tokens_flushed_for_close = False

        super().__init__(
            client_id=credentials.client_id,
            client_secret=credentials.client_secret,
            bot_id=credentials.bot_user_id,
            fetch_client_user=False,
        )

    async def open(self) -> None:
        # Login creates the app token used for general Helix work.  The managed
        # user token added immediately afterward is what WebSocket chat
        # subscriptions select via ``as_bot=True``.
        await self.login(load_tokens=False, save_tokens=False)
        validated = await self.add_token(
            self.credentials.access_token, self.credentials.refresh_token
        )
        validated_client_id = str(getattr(validated, "client_id", "") or "")
        validated_user_id = str(getattr(validated, "user_id", "") or "")
        validated_scopes = set(getattr(validated, "scopes", ()) or ())
        if validated_client_id != self.credentials.client_id:
            raise ValueError("The managed Twitch token belongs to a different application")
        if validated_user_id != self.bot_user_id:
            raise ValueError("The managed Twitch token belongs to a different bot user")
        if validated_scopes != {"user:read:chat"}:
            raise ValueError(
                "The managed Twitch token must contain only the user:read:chat scope"
            )
        managed = self.tokens.get(self.bot_user_id)
        if managed is None:
            raise RuntimeError("TwitchIO did not retain the bot user token")
        if (managed["token"], managed["refresh"]) != self._persisted_token_pair:
            # add_token refreshes expired and soon-to-expire tokens before it
            # returns. Persist that pair now rather than racing the async event
            # callback before discovery starts.
            await self._persist_tokens(managed["token"], managed["refresh"])

    def current_access_token(self) -> str:
        managed = self.tokens.get(self.bot_user_id)
        if managed is None:
            raise RuntimeError("The bot user token is not loaded")
        return managed["token"]

    async def event_token_refreshed(self, payload: Any) -> None:
        if str(payload.user_id) != self.bot_user_id:
            return
        await self._persist_tokens(payload.token, payload.refresh_token)

    async def _persist_tokens(self, access_token: str, refresh_token: str) -> None:
        pair = (access_token, refresh_token)
        async with self._token_persist_lock:
            if pair == self._persisted_token_pair:
                return
            await _run_in_daemon_thread(
                self.credential_store.save_tokens,
                access_token,
                refresh_token,
                thread_name="vieweratlas-token-persist",
            )
            self._persisted_token_pair = pair

    async def flush_tokens(self) -> None:
        """Synchronously flush the latest TwitchIO-managed credential pair."""
        managed = self.tokens.get(self.bot_user_id)
        if managed is not None:
            await self._persist_tokens(managed["token"], managed["refresh"])

    async def close(self) -> None:
        """Flush the latest managed pair before TwitchIO destroys local state."""
        # This await is deliberately ordered before the parent close.  A
        # refresh event can occur immediately before task shutdown, and the
        # Secrets Manager write must finish before the in-memory token map and
        # HTTP client are cleaned up.
        self._tokens_flushed_for_close = False
        await self.flush_tokens()
        self._tokens_flushed_for_close = True
        await super().close(save_tokens=False)

    def force_abort(self) -> None:
        """Best-effort local/socket abort used after bounded cleanup expires.

        TwitchIO's graceful close awaits each aiohttp WebSocket.  During ECS
        shutdown an unresponsive network must not consume the whole stop
        timeout and prevent a partial manifest from being retained.  This path
        cancels TwitchIO's local tasks, aborts any reachable transport, and
        removes the socket associations without awaiting remote I/O.
        """
        self._accepting_messages = False
        self._intentional_teardown = True
        self._active_channel_ids.clear()

        sockets_by_user = getattr(self, "_websockets", {})
        sockets = [
            socket
            for user_sockets in list(sockets_by_user.values())
            for socket in list(user_sockets.values())
        ]
        for socket in sockets:
            for attribute in ("_keep_alive_task", "_listen_task"):
                task = getattr(socket, attribute, None)
                if task is not None:
                    task.cancel()
                    setattr(socket, attribute, None)

            for task in list(getattr(socket, "_connection_tasks", ())):
                task.cancel()
            connection_tasks = getattr(socket, "_connection_tasks", None)
            if connection_tasks is not None:
                connection_tasks.clear()

            raw_socket = getattr(socket, "_socket", None)
            if raw_socket is not None:
                response = getattr(raw_socket, "_response", None)
                connection = getattr(response, "connection", None)
                transport = getattr(connection, "transport", None)
                if transport is None:
                    writer = getattr(raw_socket, "_writer", None)
                    transport = getattr(writer, "transport", None)
                abort = getattr(transport, "abort", None)
                if callable(abort):
                    try:
                        abort()
                    except Exception:
                        pass
            try:
                socket._socket = None
                socket._closed = True
                socket._closing = False
                socket._subscriptions.clear()
            except (AttributeError, TypeError):
                pass

        for user_sockets in list(sockets_by_user.values()):
            user_sockets.clear()
        self._survey_socket_id = None
        self._intentional_teardown = False

    async def event_websocket_closed(self, payload: Any) -> None:
        if self._active_channel_ids and not self._intentional_teardown:
            self.connection_lost.set()

    async def event_subscription_revoked(self, payload: Any) -> None:
        """Treat an active chat revocation as an unrecoverable batch gap.

        TwitchIO removes only the revoked subscription and leaves the socket
        open while other rooms remain.  Without this hook, that one channel
        would silently receive less than the common five-minute window.  The
        runner already handles ``connection_lost`` by discarding and retrying
        the whole batch, which is the only safe option because EventSub has no
        replay for the missing interval.
        """
        if self._intentional_teardown or not self._active_channel_ids:
            return
        if str(getattr(payload, "type", "")) != "channel.chat.message":
            return

        raw = getattr(payload, "raw", None)
        condition = raw.get("condition", {}) if isinstance(raw, Mapping) else {}
        broadcaster_user_id = str(condition.get("broadcaster_user_id", ""))
        user_id = str(condition.get("user_id", ""))
        if (
            broadcaster_user_id in self._active_channel_ids
            and user_id == self.bot_user_id
        ):
            self.connection_lost.set()

    async def event_message(self, payload: Any) -> None:
        if not self._accepting_messages:
            return
        author = extract_active_author(
            payload,
            active_channel_ids=self._active_channel_ids,
            bot_user_id=self.bot_user_id,
        )
        if author is not None:
            self.message_callback(*author)

    def prepare_batch(self, channel_ids: set[str]) -> None:
        if len(channel_ids) > 100:
            raise ValueError("A survey batch cannot exceed 100 channels")
        self._active_channel_ids = set(channel_ids)
        self._accepting_messages = False
        self.connection_lost.clear()

    def set_accepting_messages(self, value: bool) -> None:
        self._accepting_messages = bool(value)

    async def _ensure_survey_socket(self) -> str:
        """Return the dedicated, non-reconnecting socket for this survey.

        TwitchIO's normal ``subscribe_websocket`` path creates ``Websocket``
        with its default reconnect setting, which is unlimited in 3.2.2. A
        hard loss would then resubscribe behind the survey runner's back,
        bypassing the join limiter and replacing subscription IDs. Survey
        batches instead own recovery, so their socket must never reconnect
        automatically.
        """
        sockets = self._websockets[self.bot_user_id]
        if self._survey_socket_id in sockets:
            return self._survey_socket_id  # type: ignore[return-value]

        # A Twitch-directed session handoff creates a replacement socket while
        # preserving the configured retry policy. Adopt that zero-retry socket
        # instead of accidentally opening a second connection for the batch.
        for socket_id, existing in sockets.items():
            if getattr(existing, "_reconnect_attempts", None) == 0:
                self._survey_socket_id = socket_id
                return socket_id

        if _TwitchEventSubWebsocket is None:  # pragma: no cover - import guard.
            raise RuntimeError("TwitchIO 3.2.2 is required for EventSub surveys")

        socket = _TwitchEventSubWebsocket(
            reconnect_attempts=0,
            client=self,
            token_for=self.bot_user_id,
            http=self._http,
        )
        await socket.connect(fail_once=True)
        if not socket.session_id:
            await socket.close()
            raise RuntimeError("EventSub WebSocket did not receive a session ID")

        self._survey_socket_id = socket.session_id
        sockets[socket.session_id] = socket
        return socket.session_id

    async def subscribe(self, target: StreamTarget) -> str:
        socket_id = await self._ensure_survey_socket()
        payload = eventsub.ChatMessageSubscription(
            broadcaster_user_id=target.broadcaster_user_id,
            user_id=self.bot_user_id,
        )
        response = await self.subscribe_websocket(
            payload, as_bot=True, socket_id=socket_id
        )
        if response and response.get("data"):
            return str(response["data"][0]["id"])

        # TwitchIO returns None for HTTP 409 (an identical subscription already
        # exists). Reuse it only when it is active on this client.
        for subscription_id, existing in self.websocket_subscriptions().items():
            condition = existing.condition
            if (
                str(condition.get("broadcaster_user_id"))
                == target.broadcaster_user_id
                and str(condition.get("user_id")) == self.bot_user_id
            ):
                return subscription_id
        raise RuntimeError("Twitch did not create an active chat subscription")

    async def teardown(self, subscription_ids: Sequence[str]) -> None:
        self._accepting_messages = False
        self._intentional_teardown = True
        try:
            # Enumerate current state as well as the originally returned IDs.
            # This closes the leak left by any ID replacement or a 409 reuse
            # and intentionally ignores subscriptions outside this batch.
            active_batch_ids: list[str] = []
            for subscription_id, existing in self.websocket_subscriptions().items():
                subscription_type = getattr(existing.type, "value", existing.type)
                condition = existing.condition
                if (
                    subscription_type == "channel.chat.message"
                    and str(condition.get("broadcaster_user_id"))
                    in self._active_channel_ids
                    and str(condition.get("user_id")) == self.bot_user_id
                ):
                    active_batch_ids.append(subscription_id)

            cleanup_ids = list(
                dict.fromkeys([*subscription_ids, *active_batch_ids])
            )
            for subscription_id in cleanup_ids:
                try:
                    await self.delete_websocket_subscription(subscription_id)
                except Exception:
                    logger.warning(
                        "EventSub subscription cleanup needed a forced local removal"
                    )
                    try:
                        await self.delete_websocket_subscription(
                            subscription_id, force=True
                        )
                    except (ValueError, KeyError):
                        # A broken socket may already have removed it locally.
                        pass
            # Allow the close event dispatched for the final empty socket to run
            # while the intentional-teardown guard is still set.
            await asyncio.sleep(0)
        finally:
            self._active_channel_ids.clear()
            sockets = self._websockets.get(self.bot_user_id, {})
            if self._survey_socket_id not in sockets:
                self._survey_socket_id = None
            self._intentional_teardown = False


@dataclass(slots=True)
class BatchResult:
    rows: list[dict[str, Any]]
    attempted_channel_ids: set[str]
    completed: int
    failed: int
    zero_authors: int
    status: str
    object_key: str = ""


class EventSubSurveyRunner:
    """Freeze a cohort, collect strict batches, and persist v2 survey data."""

    def __init__(
        self,
        *,
        storage: Any,
        target_provider: ChannelTargetProvider,
        client: EventSubClient,
        top_channels_limit: int = 1200,
        batch_size: int = 100,
        window_seconds: int = 300,
        timeout_seconds: int = 7200,
        subscription_retries: int = 2,
        batch_retries: int = 2,
        join_limiter: JoinRateLimiter | None = None,
        now: Callable[[], datetime] = _utc_now,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        should_stop: Callable[[], bool] = lambda: False,
        on_batch_complete: Callable[[], None] | None = None,
        secrets: Sequence[str] = (),
        stop_poll_seconds: float = 1.0,
        poll_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        operation_timeout_seconds: float = 60.0,
        cleanup_timeout_seconds: float = 10.0,
        teardown_timeout_seconds: float = 120.0,
    ) -> None:
        if not 1 <= batch_size <= 100:
            raise ValueError("batch_size must be between 1 and 100")
        if top_channels_limit <= 0 or window_seconds <= 0 or timeout_seconds <= 0:
            raise ValueError("Survey limits and durations must be positive")
        if subscription_retries < 0 or batch_retries < 0:
            raise ValueError("Retry counts cannot be negative")
        if stop_poll_seconds <= 0:
            raise ValueError("stop_poll_seconds must be positive")
        if (
            operation_timeout_seconds <= 0
            or cleanup_timeout_seconds <= 0
            or teardown_timeout_seconds <= 0
        ):
            raise ValueError("Operation and cleanup timeouts must be positive")

        self.storage = storage
        self.target_provider = target_provider
        self.client = client
        self.top_channels_limit = top_channels_limit
        self.batch_size = batch_size
        self.window_seconds = window_seconds
        self.timeout_seconds = timeout_seconds
        self.subscription_retries = subscription_retries
        self.batch_retries = batch_retries
        self.join_limiter = join_limiter or JoinRateLimiter()
        self._now = now
        self._sleep = sleep
        self._should_stop = should_stop
        self._on_batch_complete = on_batch_complete
        self._secrets = tuple(secrets)
        self._stop_poll_seconds = stop_poll_seconds
        self._poll_sleep = poll_sleep
        self._operation_timeout_seconds = operation_timeout_seconds
        self._cleanup_timeout_seconds = cleanup_timeout_seconds
        # A full batch can require 100 remote subscription deletes. Keep this
        # separate from the short socket/token close limit: ten seconds is not
        # a realistic budget for 100 healthy HTTP round trips, while two
        # minutes remains bounded by the survey-wide safety timeout.
        self._teardown_timeout_seconds = teardown_timeout_seconds

        self._authors: dict[str, dict[str, str]] = {}
        self._manifest: dict[str, Any] = {}
        self._session_prefix = ""
        self._close_error: BaseException | None = None
        self.client.message_callback = self._record_author

    def _record_author(
        self, broadcaster_user_id: str, chatter_user_id: str, chatter_login: str
    ) -> None:
        channel_authors = self._authors.get(broadcaster_user_id)
        if channel_authors is None:
            return
        channel_authors[chatter_user_id] = chatter_login.strip().lower()

    def _manifest_key(self) -> str:
        return f"{self._session_prefix}/manifest.json"

    def _upload_manifest(self) -> None:
        if not self.storage.upload_json(self._manifest_key(), self._manifest):
            raise IOError("Could not persist the survey manifest")

    def _upload_batch(self, batch_index: int, rows: list[dict[str, Any]]) -> str:
        frame = pd.DataFrame(rows)
        output = BytesIO()
        frame.to_parquet(output, index=False, engine="pyarrow")
        key = f"{self._session_prefix}/batch={batch_index:02d}.parquet"
        if not self.storage.upload_parquet(key, output.getvalue()):
            raise IOError(f"Could not persist survey batch {batch_index}")
        return key

    async def run(self, session_id: str | None = None) -> Mapping[str, Any]:
        started = self._now()
        session_id = session_id or make_survey_session_id(started)
        if not _SESSION_ID.fullmatch(session_id):
            raise ValueError("survey session ID must contain only letters, digits, dash, and underscore")

        date = started.astimezone(UTC).date().isoformat()
        self._session_prefix = (
            f"raw/snapshots/v2/date={date}/session={session_id}"
        )
        manifest_key = self._manifest_key()
        if self.storage.exists(manifest_key):
            existing = self.storage.download_json(manifest_key)
            if isinstance(existing, dict) and existing.get("status") in _TERMINAL_MANIFEST_STATES:
                logger.info("Survey session %s already has a terminal manifest; skipping", session_id)
                return existing

        self._manifest = {
            "schema_version": 2,
            "survey_session_id": session_id,
            "status": "running",
            "started_at": _iso_utc(started),
            "completed_at": None,
            "target_limit": self.top_channels_limit,
            "batch_size": self.batch_size,
            "window_seconds": self.window_seconds,
            "timeout_seconds": self.timeout_seconds,
            "planned": 0,
            "attempted": 0,
            "completed": 0,
            "failed": 0,
            "zero_authors": 0,
            "batches_planned": 0,
            "batches_completed": 0,
            "batches": [],
        }
        self._upload_manifest()
        logger.info("SURVEY_STARTED session=%s", session_id)

        try:
            async with asyncio.timeout(self.timeout_seconds):
                return await self._run_within_timeout(session_id)
        except TimeoutError as exc:
            self._mark_partial("survey_timeout")
            raise SurveyTimeoutError(
                f"Survey exceeded its {self.timeout_seconds}-second safety limit"
            ) from exc
        except SurveyInterruptedError:
            self._mark_partial("shutdown_requested")
            raise
        except asyncio.CancelledError:
            if self._manifest.get("status") not in {"partial"}:
                self._mark_partial("shutdown_requested")
            raise
        except Exception as exc:
            if self._manifest.get("status") not in {"partial"}:
                self._mark_partial("unexpected_error", exc)
            raise
        finally:
            # Terminal/partial state is persisted by the paths above before
            # potentially slow Twitch network cleanup begins.
            await self._bounded_client_close()
            if self._close_error is not None:
                if self._manifest.get("status") != "partial" or self._manifest.get(
                    "failure_reason"
                ) not in {"survey_timeout", "shutdown_requested"}:
                    self._mark_partial(
                        "credential_persist_failed", self._close_error
                    )
                raise SurveyError(
                    "The latest Twitch credentials could not be persisted"
                ) from self._close_error

    async def _run_within_timeout(self, session_id: str) -> Mapping[str, Any]:
        try:
            # Validate/refresh authentication before discovery. The top-streams
            # provider must never make Helix calls with the potentially stale
            # access token originally loaded from Secrets Manager.
            await self._await_interruptible(
                self.client.open(), label="EventSub client startup"
            )
            token_setter = getattr(self.target_provider, "set_access_token", None)
            token_getter = getattr(self.client, "current_access_token", None)
            if callable(token_setter) and callable(token_getter):
                token_setter(token_getter())

            targets = list(
                await self._await_interruptible(
                    self._call_provider(), label="target discovery"
                )
            )
            if not targets:
                raise SurveyError("Channel discovery returned no live streams")

            # Providers are replaceable for future opt-ins; enforce the safety
            # invariants at the orchestration boundary regardless of source.
            unique_targets: list[StreamTarget] = []
            seen_ids: set[str] = set()
            for target in targets:
                if target.broadcaster_user_id in seen_ids:
                    continue
                seen_ids.add(target.broadcaster_user_id)
                unique_targets.append(target)
                if len(unique_targets) >= self.top_channels_limit:
                    break
            targets = unique_targets

            batches = [
                targets[index : index + self.batch_size]
                for index in range(0, len(targets), self.batch_size)
            ]
            self._manifest["planned"] = len(targets)
            self._manifest["batches_planned"] = len(batches)
            self._upload_manifest()

            attempted: set[str] = set()
            for batch_index, batch in enumerate(batches, start=1):
                if self._should_stop():
                    raise SurveyInterruptedError(
                        "Shutdown requested before the next survey batch"
                    )

                result = await self._run_batch(batch_index, batch)
                result.object_key = self._upload_batch(batch_index, result.rows)
                attempted.update(result.attempted_channel_ids)

                self._manifest["attempted"] = len(attempted)
                self._manifest["completed"] += result.completed
                self._manifest["failed"] += result.failed
                self._manifest["zero_authors"] += result.zero_authors
                self._manifest["batches_completed"] += 1
                self._manifest["batches"].append(
                    {
                        "batch": batch_index,
                        "status": result.status,
                        "planned": len(batch),
                        "completed": result.completed,
                        "failed": result.failed,
                        "zero_authors": result.zero_authors,
                        "object_key": result.object_key,
                    }
                )
                self._upload_manifest()
                if self._on_batch_complete:
                    self._on_batch_complete()
                logger.info(
                    "BATCH_COMPLETED session=%s batch=%d completed=%d failed=%d",
                    session_id,
                    batch_index,
                    result.completed,
                    result.failed,
                )

            status = (
                "complete"
                if self._manifest["failed"] == 0
                and self._manifest["completed"] == self._manifest["planned"]
                else "complete_with_errors"
            )
            await self._flush_tokens_before_completion()
            self._manifest["status"] = status
            self._manifest["completed_at"] = _iso_utc(self._now())
            self._upload_manifest()
            # A survey that finished every batch but lost individual channels is
            # still analysable, so it must not share the SURVEY_PARTIAL milestone
            # used by _mark_partial for timeouts, shutdowns and unexpected errors.
            event = (
                "SURVEY_COMPLETED"
                if status == "complete"
                else "SURVEY_COMPLETED_WITH_ERRORS"
            )
            logger.info(
                "%s session=%s completed=%d failed=%d",
                event,
                session_id,
                self._manifest["completed"],
                self._manifest["failed"],
            )
            return self._manifest
        finally:
            # Final cleanup is owned by ``run`` so a global timeout or SIGTERM
            # can persist the partial manifest before any network close wait.
            pass

    def _mark_partial(self, reason: str, error: BaseException | None = None) -> None:
        self._manifest["status"] = "partial"
        self._manifest["completed_at"] = _iso_utc(self._now())
        self._manifest["failure_reason"] = reason
        if error is not None:
            self._manifest["error"] = _safe_error(error, self._secrets)
        try:
            self._upload_manifest()
        finally:
            logger.error(
                "SURVEY_PARTIAL session=%s reason=%s",
                self._manifest.get("survey_session_id", "unknown"),
                reason,
            )

    async def _await_interruptible(
        self,
        operation: Awaitable[Any],
        *,
        label: str,
        timeout_seconds: float | None = None,
    ) -> Any:
        """Race a network/setup operation against shutdown and a fixed limit."""
        operation_task = asyncio.ensure_future(operation)
        stop_task = asyncio.create_task(self._wait_for_stop_request())
        timeout_task = asyncio.create_task(
            asyncio.sleep(timeout_seconds or self._operation_timeout_seconds)
        )
        tasks = (operation_task, stop_task, timeout_task)
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if self._should_stop() or stop_task.done():
                raise SurveyInterruptedError(
                    f"Shutdown requested during {label}"
                )
            if operation_task.done():
                return await operation_task
            raise SurveyError(f"{label} timed out")
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            # Standard asyncio/TwitchIO operations respond immediately to
            # cancellation. Do not let a non-cooperative third-party awaitable
            # turn this drain itself into an unbounded shutdown wait.
            _done, pending = await asyncio.wait(
                tasks,
                timeout=min(0.25, self._cleanup_timeout_seconds),
            )
            for task in _done:
                self._consume_task_result(task)
            for task in pending:
                task.add_done_callback(self._consume_task_result)

    @staticmethod
    def _consume_task_result(task: asyncio.Future[Any]) -> None:
        try:
            task.exception()
        except BaseException:
            pass

    async def _call_provider(self) -> Sequence[StreamTarget]:
        """Run synchronous Helix discovery in a daemon thread.

        ``asyncio.to_thread`` uses the loop's default executor, whose shutdown
        waits for all worker threads. A stuck HTTP request could therefore make
        an otherwise prompt SIGTERM hang while ``asyncio.run`` closes. A
        dedicated daemon lets the survey persist and exit after its own timeout.
        """
        return await _run_in_daemon_thread(
            self.target_provider.get_targets,
            self.top_channels_limit,
            thread_name="vieweratlas-target-discovery",
        )

    def _force_abort_client(self) -> None:
        abort = getattr(self.client, "force_abort", None)
        if callable(abort):
            try:
                abort()
            except Exception:
                logger.warning("EventSub forced local abort failed")

    async def _bounded_cleanup(
        self,
        operation: Awaitable[None],
        *,
        label: str,
        timeout_seconds: float | None = None,
        stop_aware: bool = False,
    ) -> bool:
        """Attempt cleanup for a short fixed window, then force local abort."""
        task = asyncio.ensure_future(operation)
        stop_task = (
            asyncio.create_task(self._wait_for_stop_request())
            if stop_aware
            else None
        )
        timeout = timeout_seconds or self._cleanup_timeout_seconds
        try:
            done, _pending = await asyncio.wait(
                {candidate for candidate in (task, stop_task) if candidate is not None},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        except asyncio.CancelledError:
            task.cancel()
            task.add_done_callback(self._consume_task_result)
            self._force_abort_client()
            raise
        finally:
            if stop_task is not None:
                if not stop_task.done():
                    stop_task.cancel()
                    stop_task.add_done_callback(self._consume_task_result)
                else:
                    self._consume_task_result(stop_task)

        if stop_aware and (self._should_stop() or stop_task in done):
            # A stop signal may arrive only after an otherwise successful
            # listening window has entered its potentially long 100-delete
            # teardown. Persist the terminal state before aborting locally so
            # ECS cannot SIGKILL a task whose manifest still says ``running``.
            if self._manifest.get("status") != "partial":
                self._mark_partial("shutdown_requested")
            task.cancel()
            task.add_done_callback(self._consume_task_result)
            self._force_abort_client()
            raise SurveyInterruptedError(
                f"Shutdown requested during {label}"
            )

        if task not in done:
            task.cancel()
            task.add_done_callback(self._consume_task_result)
            self._force_abort_client()
            logger.warning("EventSub %s exceeded its cleanup timeout", label)
            return False

        if task.cancelled():
            self._force_abort_client()
            logger.warning("EventSub %s was cancelled", label)
            return False
        try:
            await task
        except Exception:
            self._force_abort_client()
            logger.warning("EventSub %s failed", label)
            return False
        return True

    async def _bounded_teardown(self, subscription_ids: Sequence[str]) -> bool:
        return await self._bounded_cleanup(
            self.client.teardown(subscription_ids),
            label="batch teardown",
            timeout_seconds=self._teardown_timeout_seconds,
            stop_aware=True,
        )

    async def _flush_tokens_before_completion(self) -> None:
        flush = getattr(self.client, "flush_tokens", None)
        if not callable(flush):
            return
        try:
            await self._await_interruptible(
                flush(),
                label="Twitch credential persistence",
                timeout_seconds=self._cleanup_timeout_seconds,
            )
        except SurveyInterruptedError:
            raise
        except BaseException as exc:
            raise SurveyError(
                "The latest Twitch credentials could not be persisted"
            ) from exc

    async def _bounded_client_close(self) -> None:
        self._close_error = None
        task = asyncio.ensure_future(self.client.close())
        done, _pending = await asyncio.wait(
            {task}, timeout=self._cleanup_timeout_seconds
        )
        if task not in done:
            task.cancel()
            task.add_done_callback(self._consume_task_result)
            self._force_abort_client()
            tokens_flushed = bool(
                getattr(self.client, "_tokens_flushed_for_close", False)
            )
            if not tokens_flushed:
                # A concrete Twitch client marks the phase after its Secrets
                # Manager write. Until then, fail safe rather than claim that a
                # last-moment rotation was retained. Generic test/alternate
                # clients have no credential flush phase.
                if hasattr(self.client, "flush_tokens"):
                    self._close_error = TimeoutError(
                        "credential persistence timed out"
                    )
            logger.warning("EventSub client close exceeded its cleanup timeout")
            return
        if task.cancelled():
            self._close_error = RuntimeError("client close was cancelled")
            self._force_abort_client()
            return
        try:
            await task
        except BaseException as exc:
            if not bool(
                getattr(self.client, "_tokens_flushed_for_close", False)
            ) and hasattr(self.client, "flush_tokens"):
                self._close_error = exc
            self._force_abort_client()

    async def _run_batch(
        self, batch_index: int, batch: Sequence[StreamTarget]
    ) -> BatchResult:
        if len(batch) > 100:
            raise ValueError("A survey batch cannot exceed 100 channels")

        attempted: set[str] = set()
        last_loss: BaseException | None = None
        for batch_attempt in range(self.batch_retries + 1):
            self._authors = {
                target.broadcaster_user_id: {} for target in batch
            }
            self.client.prepare_batch(set(self._authors))
            subscription_ids: list[str] = []
            subscription_failures: dict[str, str] = {}
            window_started: datetime | None = None
            window_ended: datetime | None = None
            lost = False
            body_failed = False

            try:
                for target in batch:
                    if self._should_stop():
                        raise SurveyInterruptedError(
                            "Shutdown requested during survey batch setup"
                        )
                    attempted.add(target.broadcaster_user_id)
                    error: BaseException | None = None
                    for _attempt in range(self.subscription_retries + 1):
                        try:
                            await self._await_interruptible(
                                self.join_limiter.acquire(),
                                label="join-rate-limiter wait",
                            )
                            subscription_id = await self._await_interruptible(
                                self.client.subscribe(target),
                                label="EventSub subscription setup",
                            )
                            subscription_ids.append(subscription_id)
                            error = None
                            break
                        except SurveyInterruptedError:
                            raise
                        except Exception as exc:
                            error = exc
                    if error is not None:
                        subscription_failures[target.broadcaster_user_id] = _safe_error(
                            error, self._secrets
                        )

                    if self.client.connection_lost.is_set():
                        lost = True
                        last_loss = RuntimeError(
                            "EventSub WebSocket closed during batch subscription setup"
                        )
                        break

                successful = [
                    target
                    for target in batch
                    if target.broadcaster_user_id not in subscription_failures
                ]
                if not lost and successful:
                    window_started = self._now()
                    self.client.set_accepting_messages(True)
                    lost = await self._wait_for_window_or_disconnect()
                    self.client.set_accepting_messages(False)
                    window_ended = self._now()
                    if lost:
                        last_loss = RuntimeError(
                            "EventSub WebSocket closed during the active listening window"
                        )
                elif not lost:
                    window_started = self._now()
                    window_ended = window_started
            except BaseException as exc:
                body_failed = True
                # Persist a terminal safety record *before* graceful deletion
                # of as many as 100 subscriptions. ECS may allow only 120
                # seconds after SIGTERM; a slow Twitch cleanup must never leave
                # the durable manifest stuck at ``running`` if the container is
                # killed at that boundary. The outer run handler normalizes the
                # same reason again after cleanup completes.
                if self._manifest.get("status") != "partial":
                    if isinstance(exc, SurveyInterruptedError) or self._should_stop():
                        self._mark_partial("shutdown_requested")
                    elif isinstance(exc, asyncio.CancelledError):
                        self._mark_partial("survey_timeout")
                    else:
                        self._mark_partial("unexpected_error", exc)
                raise
            finally:
                self.client.set_accepting_messages(False)
                cleanup_ok = await self._bounded_teardown(subscription_ids)
                if not cleanup_ok and not body_failed:
                    raise SurveyError("EventSub batch cleanup timed out")

            if lost:
                # No replay exists for WebSocket EventSub. Throw away all data
                # from the interrupted attempt so every retained channel has a
                # genuinely common, uninterrupted window.
                self._authors = {}
                if batch_attempt < self.batch_retries:
                    logger.warning(
                        "Restarting survey batch %d after EventSub connection loss (%d/%d)",
                        batch_index,
                        batch_attempt + 1,
                        self.batch_retries,
                    )
                    continue

                reason = _safe_error(
                    last_loss or RuntimeError("EventSub connection lost"), self._secrets
                )
                rows = [
                    self._row_for_target(
                        target,
                        batch_index,
                        status="websocket_failed",
                        authors={},
                        window_started=None,
                        window_ended=None,
                        failure_reason=reason,
                    )
                    for target in batch
                ]
                return BatchResult(
                    rows=rows,
                    attempted_channel_ids=attempted,
                    completed=0,
                    failed=len(batch),
                    zero_authors=0,
                    status="failed",
                )

            rows: list[dict[str, Any]] = []
            completed = 0
            failed = 0
            zero_authors = 0
            for target in batch:
                channel_id = target.broadcaster_user_id
                failure = subscription_failures.get(channel_id)
                if failure is not None:
                    failed += 1
                    rows.append(
                        self._row_for_target(
                            target,
                            batch_index,
                            status="subscription_failed",
                            authors={},
                            window_started=None,
                            window_ended=None,
                            failure_reason=failure,
                        )
                    )
                    continue

                authors = self._authors[channel_id]
                completed += 1
                if not authors:
                    zero_authors += 1
                rows.append(
                    self._row_for_target(
                        target,
                        batch_index,
                        status="completed",
                        authors=authors,
                        window_started=window_started,
                        window_ended=window_ended,
                    )
                )

            return BatchResult(
                rows=rows,
                attempted_channel_ids=attempted,
                completed=completed,
                failed=failed,
                zero_authors=zero_authors,
                status="complete" if failed == 0 else "complete_with_errors",
            )

        raise AssertionError("unreachable batch retry state")

    async def _wait_for_window_or_disconnect(self) -> bool:
        duration = asyncio.create_task(self._sleep(self.window_seconds))
        disconnected = asyncio.create_task(self.client.connection_lost.wait())
        stopped = asyncio.create_task(self._wait_for_stop_request())
        try:
            await asyncio.wait(
                {duration, disconnected, stopped},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if self._should_stop():
                raise SurveyInterruptedError(
                    "Shutdown requested during the active listening window"
                )
            return self.client.connection_lost.is_set()
        finally:
            for task in (duration, disconnected, stopped):
                if not task.done():
                    task.cancel()
            await asyncio.gather(
                duration, disconnected, stopped, return_exceptions=True
            )

    async def _wait_for_stop_request(self) -> None:
        while not self._should_stop():
            await self._poll_sleep(self._stop_poll_seconds)

    def _row_for_target(
        self,
        target: StreamTarget,
        batch_index: int,
        *,
        status: str,
        authors: Mapping[str, str],
        window_started: datetime | None,
        window_ended: datetime | None,
        failure_reason: str = "",
    ) -> dict[str, Any]:
        aligned = sorted((str(user_id), str(login)) for user_id, login in authors.items())
        chatter_ids = [user_id for user_id, _ in aligned]
        chatter_logins = [login for _, login in aligned]
        started_text = _iso_utc(window_started) if window_started else ""
        ended_text = _iso_utc(window_ended) if window_ended else ""

        return {
            "schema_version": 2,
            "survey_session_id": self._manifest["survey_session_id"],
            "batch": batch_index,
            "rank": target.rank,
            "selection_source": target.selection_source,
            "channel_id": target.broadcaster_user_id,
            "channel": target.broadcaster_login,
            "channel_login": target.broadcaster_login,
            "viewer_count": target.viewer_count,
            "game_id": target.game_id,
            "game_name": target.game_name,
            "language": target.language,
            "title": target.title,
            "started_at": target.started_at,
            "discovered_at": target.discovered_at,
            "timestamp": started_text,
            "sample_started_at": started_text,
            "sample_ended_at": ended_text,
            "sample_duration_seconds": self.window_seconds if status == "completed" else 0,
            "collection_status": status,
            "failure_reason": failure_reason,
            "unique_author_count": len(chatter_ids),
            "chatter_ids_json": json.dumps(chatter_ids, separators=(",", ":")),
            "chatters_json": json.dumps(chatter_logins, separators=(",", ":")),
            "_source": "live",
        }
