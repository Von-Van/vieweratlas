#!/usr/bin/env python3
"""Friendly one-time Twitch authorization setup for ViewerAtlas.

The script opens Twitch in the user's browser, receives the authorization-code
callback on localhost, verifies that the selected account and permission are
correct, and writes one atomic JSON credential to AWS Secrets Manager.  It
never prints an access token, refresh token, client secret, or authorization
code.
"""

from __future__ import annotations

import argparse
import getpass
import hmac
import json
import os
import secrets
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urlparse


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from twitch_credentials import TwitchCredentials  # noqa: E402


AUTHORIZE_URL = "https://id.twitch.tv/oauth2/authorize"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"
REQUIRED_SCOPE = "user:read:chat"
DEFAULT_SECRET_ID = "vieweratlas/twitch/credentials"
DEFAULT_PORT = 17653


def _nonempty(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _safe_json_response(response: Any, context: str) -> Mapping[str, Any]:
    try:
        payload = response.json()
    except Exception:
        raise RuntimeError(f"Twitch returned an invalid response while {context}") from None
    if not isinstance(payload, Mapping):
        raise RuntimeError(f"Twitch returned an invalid response while {context}")
    return payload


def _existing_secret(client: Any, secret_id: str) -> tuple[dict[str, Any], bool]:
    try:
        response = client.get_secret_value(SecretId=secret_id)
    except Exception as exc:
        code = (
            getattr(exc, "response", {})
            .get("Error", {})
            .get("Code", "")
        )
        if code == "ResourceNotFoundException":
            return {}, False
        raise RuntimeError(f"Could not read AWS secret {secret_id!r}") from None

    secret_string = response.get("SecretString")
    if not isinstance(secret_string, str):
        raise RuntimeError(f"AWS secret {secret_id!r} must contain JSON text")
    try:
        payload = json.loads(secret_string)
    except (TypeError, ValueError):
        raise RuntimeError(f"AWS secret {secret_id!r} does not contain valid JSON") from None
    if not isinstance(payload, dict):
        raise RuntimeError(f"AWS secret {secret_id!r} must contain a JSON object")
    return payload, True


def _prompt_value(label: str, current: str = "", *, hidden: bool = False) -> str:
    if current:
        return current
    prompt = f"{label}: "
    value = getpass.getpass(prompt) if hidden else input(prompt)
    value = value.strip()
    if not value:
        raise RuntimeError(f"{label} cannot be blank")
    return value


def _receive_authorization_code(
    client_id: str,
    redirect_uri: str,
    state: str,
    *,
    port: int,
    open_browser: bool,
    timeout: int,
) -> str:
    result: dict[str, str] = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
            parsed = urlparse(self.path)
            if parsed.path != "/callback":
                self.send_error(404)
                return

            query = parse_qs(parsed.query)
            returned_state = query.get("state", [""])[0]
            if not returned_state or not hmac.compare_digest(returned_state, state):
                result["error"] = "Twitch returned an invalid security check. Please retry."
                status = 400
            elif query.get("error"):
                result["error"] = "Twitch authorization was cancelled or denied."
                status = 400
            else:
                code = query.get("code", [""])[0]
                if code:
                    result["code"] = code
                    status = 200
                else:
                    result["error"] = "Twitch did not return an authorization code."
                    status = 400

            body = (
                "<html><body><h2>ViewerAtlas Twitch setup</h2>"
                + (
                    "<p>Authorization received. You can close this tab and return "
                    "to Terminal.</p>"
                    if status == 200
                    else "<p>Authorization was not completed. Return to Terminal and retry.</p>"
                )
                + "</body></html>"
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            # The default log includes the callback URL and its sensitive code.
            return

    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": REQUIRED_SCOPE,
            "state": state,
            "force_verify": "true",
        }
    )
    authorization_url = f"{AUTHORIZE_URL}?{query}"

    try:
        server = HTTPServer(("127.0.0.1", port), CallbackHandler)
    except OSError:
        raise RuntimeError(
            f"Could not start the temporary local callback on port {port}; "
            "close the program using that port or choose another --port"
        ) from None

    with server:
        server.timeout = timeout
        print("\nA Twitch sign-in page should open in your browser.")
        print("Sign in as the dedicated ViewerAtlas bot account and choose Authorize.")
        if not open_browser or not webbrowser.open(authorization_url):
            print("Your browser did not open automatically. Open this one-time link:")
            print(authorization_url)
        print(f"Waiting up to {timeout // 60} minutes for Twitch…")
        server.handle_request()

    if "error" in result:
        raise RuntimeError(result["error"])
    if "code" not in result:
        raise RuntimeError("Timed out waiting for Twitch authorization; run the setup again")
    return result["code"]


def _exchange_code(
    http: Any,
    *,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
) -> tuple[str, str]:
    try:
        response = http.post(
            TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            timeout=15,
        )
    except Exception:
        raise RuntimeError("Could not contact Twitch to finish authorization") from None

    if getattr(response, "status_code", None) != 200:
        raise RuntimeError(
            "Twitch could not finish authorization. Check the app client ID, client "
            "secret, and registered localhost redirect URL, then retry."
        )
    payload = _safe_json_response(response, "finishing authorization")
    access_token = _nonempty(payload.get("access_token"))
    refresh_token = _nonempty(payload.get("refresh_token"))
    if not access_token or not refresh_token:
        raise RuntimeError("Twitch did not issue a complete refreshable token pair")
    return access_token, refresh_token


def _validate_identity(
    http: Any,
    *,
    client_id: str,
    access_token: str,
    expected_bot_user_id: str,
) -> tuple[str, str]:
    try:
        response = http.get(
            VALIDATE_URL,
            headers={"Authorization": f"OAuth {access_token}"},
            timeout=10,
        )
    except Exception:
        raise RuntimeError("Could not contact Twitch to verify the bot account") from None
    if getattr(response, "status_code", None) != 200:
        raise RuntimeError("Twitch rejected the new access token")

    payload = _safe_json_response(response, "verifying the bot account")
    if _nonempty(payload.get("client_id")) != client_id:
        raise RuntimeError("The new token belongs to a different Twitch application")

    bot_user_id = _nonempty(payload.get("user_id"))
    bot_login = _nonempty(payload.get("login"))
    if not bot_user_id or not bot_login:
        raise RuntimeError("Twitch did not identify the authorized bot account")
    if expected_bot_user_id and expected_bot_user_id != bot_user_id:
        raise RuntimeError(
            "You signed in as a different Twitch account than the configured bot. "
            "Run setup again and choose the dedicated ViewerAtlas bot account."
        )

    scopes = payload.get("scopes")
    if not isinstance(scopes, list) or set(scopes) != {REQUIRED_SCOPE}:
        raise RuntimeError(
            "The token must contain only the user:read:chat permission; run setup again"
        )
    return bot_user_id, bot_login


def _write_credentials(
    client: Any,
    *,
    secret_id: str,
    existing: dict[str, Any],
    secret_exists: bool,
    credentials: TwitchCredentials,
) -> None:
    payload = dict(existing)
    payload.update(
        {
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "bot_user_id": credentials.bot_user_id,
            "access_token": credentials.access_token,
            "refresh_token": credentials.refresh_token,
        }
    )
    secret_string = json.dumps(payload, separators=(",", ":"))
    try:
        if secret_exists:
            client.put_secret_value(SecretId=secret_id, SecretString=secret_string)
        else:
            client.create_secret(Name=secret_id, SecretString=secret_string)
    except Exception:
        raise RuntimeError(f"Could not write AWS secret {secret_id!r}") from None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Authorize the ViewerAtlas bot and save one refreshable AWS secret."
    )
    parser.add_argument(
        "--secret-id",
        default=os.getenv("TWITCH_CREDENTIALS_SECRET_ID", DEFAULT_SECRET_ID),
    )
    parser.add_argument(
        "--region",
        default=os.getenv("AWS_REGION")
        or os.getenv("AWS_DEFAULT_REGION")
        or "us-east-1",
    )
    parser.add_argument("--client-id", default=os.getenv("TWITCH_CLIENT_ID", ""))
    parser.add_argument("--bot-user-id", default=os.getenv("TWITCH_BOT_USER_ID", ""))
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--no-browser", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if not 1 <= args.port <= 65535:
        print("Setup stopped: --port must be between 1 and 65535.", file=sys.stderr)
        return 1

    try:
        import boto3
        import requests

        client = boto3.client("secretsmanager", region_name=args.region)
        existing, secret_exists = _existing_secret(client, args.secret_id)

        print("ViewerAtlas Twitch authorization")
        print("This creates one refreshable bot credential in AWS Secrets Manager.")
        print("It requests only permission to read chat messages.")
        print(f"AWS region: {args.region}")
        print(f"AWS secret: {args.secret_id}\n")

        client_id = _prompt_value(
            "Twitch application Client ID",
            _nonempty(args.client_id) or _nonempty(existing.get("client_id")),
        )
        client_secret = _prompt_value(
            "Twitch application Client Secret",
            _nonempty(os.getenv("TWITCH_CLIENT_SECRET"))
            or _nonempty(existing.get("client_secret")),
            hidden=True,
        )
        expected_bot_user_id = (
            _nonempty(args.bot_user_id) or _nonempty(existing.get("bot_user_id"))
        )

        redirect_uri = f"http://localhost:{args.port}/callback"
        print("In the Twitch developer console, the application's OAuth Redirect URL must be:")
        print(redirect_uri)
        input("Press Return after confirming that URL is registered… ")

        code = _receive_authorization_code(
            client_id,
            redirect_uri,
            secrets.token_urlsafe(32),
            port=args.port,
            open_browser=not args.no_browser,
            timeout=args.timeout,
        )
        access_token, refresh_token = _exchange_code(
            requests,
            client_id=client_id,
            client_secret=client_secret,
            code=code,
            redirect_uri=redirect_uri,
        )
        bot_user_id, bot_login = _validate_identity(
            requests,
            client_id=client_id,
            access_token=access_token,
            expected_bot_user_id=expected_bot_user_id,
        )
        print(f"Twitch confirmed the bot account: {bot_login} (ID {bot_user_id})")
        confirmation = input("Save this bot credential to AWS? [Y/n] ").strip().lower()
        if confirmation not in {"", "y", "yes"}:
            raise RuntimeError("Setup cancelled; AWS was not changed")

        credentials = TwitchCredentials(
            client_id=client_id,
            client_secret=client_secret,
            bot_user_id=bot_user_id,
            access_token=access_token,
            refresh_token=refresh_token,
        )
        _write_credentials(
            client,
            secret_id=args.secret_id,
            existing=existing,
            secret_exists=secret_exists,
            credentials=credentials,
        )
        print("\nDone. The ViewerAtlas Twitch credential is ready in AWS Secrets Manager.")
        print("No token or client secret was displayed or written to a local file.")
        return 0
    except KeyboardInterrupt:
        print("\nSetup cancelled; AWS was not changed.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Setup stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
