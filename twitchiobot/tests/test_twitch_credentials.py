"""Tests for the atomic ViewerAtlas Twitch credential store."""

import json
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import twitch_credentials
from twitch_credentials import TwitchCredentials, get_credential_store


COMPLETE_ENV = {
    "TWITCH_CLIENT_ID": "client-id",
    "TWITCH_CLIENT_SECRET": "client-secret",
    "TWITCH_BOT_USER_ID": "12345",
    "TWITCH_OAUTH_TOKEN": "access-token",
    "TWITCH_REFRESH_TOKEN": "refresh-token",
}


class FakeSecretsManager:
    def __init__(self, payload, *, get_error=None, put_error=None):
        self.payload = dict(payload)
        self.get_error = get_error
        self.put_error = put_error
        self.get_calls = []
        self.put_calls = []

    def get_secret_value(self, **kwargs):
        self.get_calls.append(kwargs)
        if self.get_error:
            raise self.get_error
        return {"SecretString": json.dumps(self.payload)}

    def put_secret_value(self, **kwargs):
        self.put_calls.append(kwargs)
        if self.put_error:
            raise self.put_error
        self.payload = json.loads(kwargs["SecretString"])


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class FakeHTTP:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def secret_payload(**overrides):
    payload = {
        "client_id": "client-id",
        "client_secret": "client-secret",
        "bot_user_id": "12345",
        "access_token": "access-token",
        "refresh_token": "refresh-token",
    }
    payload.update(overrides)
    return payload


def test_public_api_is_intentionally_small():
    assert twitch_credentials.__all__ == ["TwitchCredentials", "get_credential_store"]


def test_environment_store_loads_complete_credentials():
    credentials = get_credential_store(environ=dict(COMPLETE_ENV)).load()

    assert credentials == TwitchCredentials(
        client_id="client-id",
        client_secret="client-secret",
        bot_user_id="12345",
        access_token="access-token",
        refresh_token="refresh-token",
    )


def test_environment_store_reports_all_missing_fields_without_values():
    with pytest.raises(ValueError) as exc_info:
        get_credential_store(environ={"TWITCH_OAUTH_TOKEN": "do-not-print"}).load()

    message = str(exc_info.value)
    assert "client_id" in message
    assert "client_secret" in message
    assert "bot_user_id" in message
    assert "refresh_token" in message
    assert "do-not-print" not in message


@pytest.mark.parametrize("token_field", ["access_token", "refresh_token"])
def test_credentials_reject_legacy_oauth_prefix(token_field):
    payload = secret_payload(**{token_field: "oauth:do-not-print"})

    with pytest.raises(ValueError) as exc_info:
        TwitchCredentials(**payload)

    assert "oauth:" in str(exc_info.value)
    assert "do-not-print" not in str(exc_info.value)


def test_environment_store_rotates_token_pair_together():
    environ = dict(COMPLETE_ENV)
    store = get_credential_store(environ=environ)

    store.save_tokens("next-access", "next-refresh")

    credentials = store.load()
    assert credentials.access_token == "next-access"
    assert credentials.refresh_token == "next-refresh"


def test_secrets_manager_store_loads_json_record():
    client = FakeSecretsManager(secret_payload())
    store = get_credential_store(
        environ={"TWITCH_CREDENTIALS_SECRET_ID": "vieweratlas/twitch/credentials"},
        secrets_client=client,
    )

    credentials = store.load()

    assert credentials.bot_user_id == "12345"
    assert credentials.access_token == "access-token"
    assert client.get_calls == [
        {"SecretId": "vieweratlas/twitch/credentials"}
    ]


def test_secrets_manager_rotation_is_one_atomic_merge_and_preserves_metadata():
    client = FakeSecretsManager(secret_payload(note="keep me"))
    store = get_credential_store(
        environ={"TWITCH_CREDENTIALS_SECRET_ID": "vieweratlas/twitch/credentials"},
        secrets_client=client,
    )

    store.save_tokens("next-access", "next-refresh")

    assert len(client.put_calls) == 1
    written = json.loads(client.put_calls[0]["SecretString"])
    assert written["access_token"] == "next-access"
    assert written["refresh_token"] == "next-refresh"
    assert written["client_id"] == "client-id"
    assert written["client_secret"] == "client-secret"
    assert written["bot_user_id"] == "12345"
    assert written["note"] == "keep me"


def test_secret_read_error_does_not_leak_underlying_token_text():
    client = FakeSecretsManager(
        secret_payload(), get_error=RuntimeError("server echoed do-not-print")
    )
    store = get_credential_store(
        environ={"TWITCH_CREDENTIALS_SECRET_ID": "safe-secret-id"},
        secrets_client=client,
    )

    with pytest.raises(RuntimeError) as exc_info:
        store.load()

    assert "safe-secret-id" in str(exc_info.value)
    assert "do-not-print" not in str(exc_info.value)


def test_secret_write_error_does_not_leak_tokens():
    client = FakeSecretsManager(
        secret_payload(), put_error=RuntimeError("server echoed next-access")
    )
    store = get_credential_store(
        environ={"TWITCH_CREDENTIALS_SECRET_ID": "safe-secret-id"},
        secrets_client=client,
    )

    with pytest.raises(RuntimeError) as exc_info:
        store.save_tokens("next-access", "next-refresh")

    assert "safe-secret-id" in str(exc_info.value)
    assert "next-access" not in str(exc_info.value)
    assert "next-refresh" not in str(exc_info.value)


def test_validate_access_token_confirms_identity_and_chat_scope():
    credentials = TwitchCredentials(**secret_payload())
    http = FakeHTTP(
        FakeResponse(
            payload={
                "client_id": "client-id",
                "user_id": "12345",
                "login": "vieweratlas_bot",
                "scopes": ["user:read:chat"],
            }
        )
    )

    result = twitch_credentials._validate_access_token(
        credentials, http=http, require_only_chat_scope=True
    )

    assert result["user_id"] == "12345"
    assert len(http.calls) == 1
    assert http.calls[0][1]["headers"]["Authorization"] == "OAuth access-token"


@pytest.mark.parametrize(
    ("payload", "expected_message"),
    [
        (
            {
                "client_id": "other-client",
                "user_id": "12345",
                "scopes": ["user:read:chat"],
            },
            "different application",
        ),
        (
            {
                "client_id": "client-id",
                "user_id": "other-user",
                "scopes": ["user:read:chat"],
            },
            "different bot account",
        ),
        (
            {"client_id": "client-id", "user_id": "12345", "scopes": []},
            "missing the user:read:chat",
        ),
        (
            {
                "client_id": "client-id",
                "user_id": "12345",
                "scopes": ["user:read:chat", "channel:read:subscriptions"],
            },
            "permissions other than",
        ),
    ],
)
def test_validate_access_token_rejects_wrong_identity_or_scope(
    payload, expected_message
):
    credentials = TwitchCredentials(**secret_payload(access_token="do-not-print"))
    http = FakeHTTP(FakeResponse(payload=payload))

    with pytest.raises(ValueError) as exc_info:
        twitch_credentials._validate_access_token(
            credentials, http=http, require_only_chat_scope=True
        )

    assert expected_message in str(exc_info.value)
    assert "do-not-print" not in str(exc_info.value)


def test_validate_access_token_hides_network_exception_details():
    class ExplodingHTTP:
        def get(self, *args, **kwargs):
            raise RuntimeError("socket error contains do-not-print")

    credentials = TwitchCredentials(**secret_payload(access_token="do-not-print"))
    with pytest.raises(RuntimeError) as exc_info:
        twitch_credentials._validate_access_token(credentials, http=ExplodingHTTP())

    assert "do-not-print" not in str(exc_info.value)
