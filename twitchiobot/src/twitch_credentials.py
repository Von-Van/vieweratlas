"""Load and safely rotate the Twitch credentials used by ViewerAtlas.

Production stores the entire credential set in one AWS Secrets Manager JSON
document.  Keeping the access and refresh tokens together is important: Twitch
rotates refresh tokens and a process must never observe one half of two
different token generations.

Local development may use environment variables instead.  Environment-backed
token updates are process-local; they are intentionally not written to a
``.env`` file because silently rewriting a developer's secrets file is unsafe.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, fields
from typing import Any, Mapping, MutableMapping, Optional


_SECRET_ID_ENV = "TWITCH_CREDENTIALS_SECRET_ID"
_REQUIRED_SCOPE = "user:read:chat"
_VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"

_FIELD_TO_ENV = {
    "client_id": "TWITCH_CLIENT_ID",
    "client_secret": "TWITCH_CLIENT_SECRET",
    "bot_user_id": "TWITCH_BOT_USER_ID",
    "access_token": "TWITCH_OAUTH_TOKEN",
    "refresh_token": "TWITCH_REFRESH_TOKEN",
}


def _clean_value(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _validate_token_format(name: str, value: str) -> None:
    if not value:
        raise ValueError(f"Missing Twitch credential field: {name}")
    if value.lower().startswith("oauth:"):
        raise ValueError(
            f"{name} must be the raw token value without the 'oauth:' prefix"
        )


@dataclass(frozen=True, slots=True)
class TwitchCredentials:
    """The single, complete credential set required by the survey collector."""

    client_id: str
    client_secret: str
    bot_user_id: str
    access_token: str
    refresh_token: str

    def __post_init__(self) -> None:
        missing = []
        for field in fields(self):
            value = _clean_value(getattr(self, field.name))
            object.__setattr__(self, field.name, value)
            if not value:
                missing.append(field.name)

        if missing:
            raise ValueError(
                "Missing Twitch credential fields: " + ", ".join(sorted(missing))
            )

        _validate_token_format("access_token", self.access_token)
        _validate_token_format("refresh_token", self.refresh_token)


def _credentials_from_mapping(values: Mapping[str, Any]) -> TwitchCredentials:
    return TwitchCredentials(
        client_id=values.get("client_id", ""),
        client_secret=values.get("client_secret", ""),
        bot_user_id=values.get("bot_user_id", ""),
        access_token=values.get("access_token", ""),
        refresh_token=values.get("refresh_token", ""),
    )


def _validate_access_token(
    credentials: TwitchCredentials,
    *,
    http: Optional[Any] = None,
    timeout: float = 10.0,
    require_only_chat_scope: bool = False,
) -> Mapping[str, Any]:
    """Validate token identity and scope without including secrets in errors.

    This is deliberately private: callers normally let TwitchIO perform its
    startup validation.  The guided OAuth helper uses it before persisting a
    newly issued token.
    """

    if http is None:
        try:
            import requests
        except ImportError:
            raise RuntimeError(
                "The requests package is required to validate Twitch credentials"
            ) from None
        http = requests

    try:
        response = http.get(
            _VALIDATE_URL,
            headers={"Authorization": f"OAuth {credentials.access_token}"},
            timeout=timeout,
        )
    except Exception:
        raise RuntimeError("Could not contact Twitch to validate the access token") from None

    if getattr(response, "status_code", None) != 200:
        raise ValueError(
            "Twitch rejected the access token; run the Twitch authorization setup again"
        )

    try:
        payload = response.json()
    except Exception:
        raise ValueError("Twitch returned an invalid token-validation response") from None

    if not isinstance(payload, Mapping):
        raise ValueError("Twitch returned an invalid token-validation response")

    if _clean_value(payload.get("client_id")) != credentials.client_id:
        raise ValueError("The Twitch token was issued to a different application")
    if _clean_value(payload.get("user_id")) != credentials.bot_user_id:
        raise ValueError("The Twitch token belongs to a different bot account")

    raw_scopes = payload.get("scopes", [])
    if not isinstance(raw_scopes, list) or not all(
        isinstance(scope, str) for scope in raw_scopes
    ):
        raise ValueError("Twitch returned an invalid token scope list")

    scopes = set(raw_scopes)
    if _REQUIRED_SCOPE not in scopes:
        raise ValueError("The Twitch token is missing the user:read:chat permission")
    if require_only_chat_scope and scopes != {_REQUIRED_SCOPE}:
        raise ValueError(
            "The Twitch token has permissions other than user:read:chat; "
            "run the guided setup to create a least-privilege token"
        )

    return payload


class _EnvironmentCredentialStore:
    def __init__(self, environ: MutableMapping[str, str]) -> None:
        self._environ = environ
        self._lock = threading.RLock()

    def load(self) -> TwitchCredentials:
        with self._lock:
            values = {
                field: self._environ.get(env_name, "")
                for field, env_name in _FIELD_TO_ENV.items()
            }
        return _credentials_from_mapping(values)

    def save_tokens(self, access_token: str, refresh_token: str) -> None:
        access_token = _clean_value(access_token)
        refresh_token = _clean_value(refresh_token)
        _validate_token_format("access_token", access_token)
        _validate_token_format("refresh_token", refresh_token)

        # Store readers share this lock, so they can only observe the old pair
        # or the new pair. Roll back if a non-standard mapping rejects a write.
        with self._lock:
            old_access = self._environ.get(_FIELD_TO_ENV["access_token"])
            old_refresh = self._environ.get(_FIELD_TO_ENV["refresh_token"])
            try:
                self._environ.update(
                    {
                        _FIELD_TO_ENV["access_token"]: access_token,
                        _FIELD_TO_ENV["refresh_token"]: refresh_token,
                    }
                )
            except Exception:
                self._restore(_FIELD_TO_ENV["access_token"], old_access)
                self._restore(_FIELD_TO_ENV["refresh_token"], old_refresh)
                raise RuntimeError("Unable to update local Twitch tokens") from None

    def _restore(self, key: str, value: Optional[str]) -> None:
        try:
            if value is None:
                self._environ.pop(key, None)
            else:
                self._environ[key] = value
        except Exception:
            # Preserve the original safe error and never include token values.
            pass


class _SecretsManagerCredentialStore:
    def __init__(self, secret_id: str, client: Any) -> None:
        self._secret_id = secret_id
        self._client = client
        self._lock = threading.RLock()

    def _load_payload(self) -> dict[str, Any]:
        try:
            response = self._client.get_secret_value(SecretId=self._secret_id)
        except Exception:
            raise RuntimeError(
                f"Unable to read Twitch credentials secret {self._secret_id!r}"
            ) from None

        secret_string = response.get("SecretString")
        if not isinstance(secret_string, str):
            raise ValueError(
                f"Twitch credentials secret {self._secret_id!r} must contain JSON text"
            )

        try:
            payload = json.loads(secret_string)
        except (TypeError, ValueError):
            raise ValueError(
                f"Twitch credentials secret {self._secret_id!r} is not valid JSON"
            ) from None

        if not isinstance(payload, dict):
            raise ValueError(
                f"Twitch credentials secret {self._secret_id!r} must be a JSON object"
            )
        return payload

    def load(self) -> TwitchCredentials:
        with self._lock:
            payload = self._load_payload()
        return _credentials_from_mapping(payload)

    def save_tokens(self, access_token: str, refresh_token: str) -> None:
        access_token = _clean_value(access_token)
        refresh_token = _clean_value(refresh_token)
        _validate_token_format("access_token", access_token)
        _validate_token_format("refresh_token", refresh_token)

        with self._lock:
            payload = self._load_payload()
            payload["access_token"] = access_token
            payload["refresh_token"] = refresh_token
            # Validate the merged generation before the single Secrets Manager
            # write. One SecretString becomes AWSCURRENT atomically.
            _credentials_from_mapping(payload)
            try:
                self._client.put_secret_value(
                    SecretId=self._secret_id,
                    SecretString=json.dumps(payload, separators=(",", ":")),
                )
            except Exception:
                raise RuntimeError(
                    f"Unable to update Twitch credentials secret {self._secret_id!r}"
                ) from None


def get_credential_store(
    *,
    environ: Optional[MutableMapping[str, str]] = None,
    secrets_client: Optional[Any] = None,
) -> Any:
    """Return the production Secrets Manager store or the local env fallback."""

    env = os.environ if environ is None else environ
    secret_id = _clean_value(env.get(_SECRET_ID_ENV, ""))
    if not secret_id:
        return _EnvironmentCredentialStore(env)

    if secrets_client is None:
        try:
            import boto3
        except ImportError:
            raise RuntimeError(
                "boto3 is required when TWITCH_CREDENTIALS_SECRET_ID is set"
            ) from None

        client_kwargs = {}
        region = _clean_value(env.get("AWS_REGION", "") or env.get("AWS_DEFAULT_REGION", ""))
        if region:
            client_kwargs["region_name"] = region
        secrets_client = boto3.client("secretsmanager", **client_kwargs)

    return _SecretsManagerCredentialStore(secret_id, secrets_client)


__all__ = ["TwitchCredentials", "get_credential_store"]
