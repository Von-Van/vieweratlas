import argparse
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable, List, Protocol, Sequence

import requests
from dotenv import load_dotenv

load_dotenv()

CHANNEL_FILE = os.getenv("CHANNELS_FILE", "channels.txt")
TWITCH_STREAMS_URL = "https://api.twitch.tv/helix/streams"


class ChannelDiscoveryError(RuntimeError):
    """Raised when top-channel discovery cannot complete safely."""


@dataclass(frozen=True, slots=True)
class StreamTarget:
    """A ranked live channel frozen at the beginning of a survey.

    Twitch user IDs are the durable identity.  The login is retained for
    display and backwards-compatible analysis, but must not be used as the
    subscription or de-duplication key because users can rename accounts.
    """

    broadcaster_user_id: str
    broadcaster_login: str
    rank: int
    viewer_count: int
    game_id: str
    game_name: str
    language: str
    title: str
    started_at: str
    discovered_at: str
    selection_source: str = "top_ranked"

    def to_dict(self) -> dict:
        return asdict(self)


class ChannelTargetProvider(Protocol):
    """Source of the fixed channel cohort for a survey session."""

    def get_targets(self, limit: int) -> Sequence[StreamTarget]: ...


def _get_twitch_headers(
    *, client_id: str | None = None, access_token: str | None = None
) -> dict:
    client_id = client_id or os.getenv("TWITCH_CLIENT_ID")
    oauth_token = access_token or os.getenv("TWITCH_OAUTH_TOKEN")

    if not client_id:
        raise ChannelDiscoveryError("TWITCH_CLIENT_ID is not set")
    if not oauth_token:
        raise ChannelDiscoveryError("TWITCH_OAUTH_TOKEN is not set")

    return {
        "Client-ID": client_id,
        "Authorization": f"Bearer {oauth_token}",
    }


def fetch_top_streams(
    limit: int = 1200,
    timeout_s: float = 10.0,
    max_retries: int = 3,
    backoff_base_s: float = 1.0,
    session: requests.Session | None = None,
    *,
    client_id: str | None = None,
    access_token: str | None = None,
    now: Callable[[], datetime] | None = None,
) -> List[StreamTarget]:
    """Fetch and freeze ranked, structured stream records from Helix.

    Helix pages are not a transactional snapshot, so this is intentionally an
    *approximate* top-N ranking.  Duplicate broadcasters encountered while the
    ranking changes between pages are discarded without consuming a rank.
    """
    if limit <= 0:
        return []

    headers = _get_twitch_headers(client_id=client_id, access_token=access_token)
    client = session or requests.Session()
    discovered_at = (now or (lambda: datetime.now(timezone.utc)))()
    if discovered_at.tzinfo is None:
        discovered_at = discovered_at.replace(tzinfo=timezone.utc)
    discovered_at_text = discovered_at.astimezone(timezone.utc).isoformat()

    targets: List[StreamTarget] = []
    seen_broadcaster_ids: set[str] = set()
    cursor = None

    # Pages can contain a duplicate when ranks move during pagination.  Keep
    # paging until N unique channels are frozen or Twitch has no next page.
    while len(targets) < limit:
        params = {"first": min(100, limit - len(targets))}
        if cursor:
            params["after"] = cursor

        last_error: Exception | None = None
        payload = None

        for attempt in range(1, max_retries + 2):
            try:
                response = client.get(
                    TWITCH_STREAMS_URL,
                    headers=headers,
                    params=params,
                    timeout=timeout_s,
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ChannelDiscoveryError("Twitch API returned an invalid response")
                break
            except requests.exceptions.Timeout as exc:
                last_error = exc
                print(
                    f"Timeout fetching channels (attempt {attempt}/{max_retries + 1})",
                    file=sys.stderr,
                )
            except requests.exceptions.HTTPError as exc:
                last_error = exc
                status = exc.response.status_code if exc.response is not None else None
                if status in (400, 401, 403):
                    raise ChannelDiscoveryError(
                        f"Twitch API request rejected with HTTP {status}. Check the configured Twitch credentials."
                    ) from exc
                if status is not None and status < 500 and status != 429:
                    raise ChannelDiscoveryError(
                        f"Twitch API request failed with non-retriable HTTP {status}."
                    ) from exc
                print(
                    f"HTTP error {status} fetching channels (attempt {attempt}/{max_retries + 1})",
                    file=sys.stderr,
                )
            except requests.exceptions.RequestException as exc:
                last_error = exc
                print(
                    f"Network error fetching channels (attempt {attempt}/{max_retries + 1}): {exc}",
                    file=sys.stderr,
                )

            if attempt <= max_retries:
                time.sleep(backoff_base_s * (2 ** (attempt - 1)))

        if payload is None:
            raise ChannelDiscoveryError(
                f"Failed to fetch channels after {max_retries + 1} attempts"
            ) from last_error

        streams = payload.get("data", [])
        if not isinstance(streams, list):
            raise ChannelDiscoveryError("Twitch API returned an invalid stream list")
        if not streams:
            break

        for stream in streams:
            broadcaster_id = str(stream.get("user_id", "")).strip()
            login = str(stream.get("user_login", "")).strip().lower()
            if not broadcaster_id or not login or broadcaster_id in seen_broadcaster_ids:
                continue

            seen_broadcaster_ids.add(broadcaster_id)
            targets.append(
                StreamTarget(
                    broadcaster_user_id=broadcaster_id,
                    broadcaster_login=login,
                    rank=len(targets) + 1,
                    viewer_count=int(stream.get("viewer_count", 0) or 0),
                    game_id=str(stream.get("game_id", "") or ""),
                    game_name=str(stream.get("game_name", "") or ""),
                    language=str(stream.get("language", "") or "").lower(),
                    title=str(stream.get("title", "") or ""),
                    started_at=str(stream.get("started_at", "") or ""),
                    discovered_at=discovered_at_text,
                )
            )
            if len(targets) >= limit:
                break

        next_cursor = payload.get("pagination", {}).get("cursor")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor

    return targets


class TopStreamsProvider:
    """Production provider for the approximate top live streams on Twitch."""

    def __init__(
        self,
        *,
        client_id: str,
        access_token: str,
        session: requests.Session | None = None,
        timeout_s: float = 10.0,
        max_retries: int = 3,
    ) -> None:
        self.client_id = client_id
        self.access_token = access_token
        self.session = session
        self.timeout_s = timeout_s
        self.max_retries = max_retries

    def set_access_token(self, access_token: str) -> None:
        """Use the currently validated/refreshed managed user token."""
        if not access_token:
            raise ValueError("A non-empty Twitch access token is required")
        self.access_token = access_token

    def get_targets(self, limit: int) -> Sequence[StreamTarget]:
        return fetch_top_streams(
            limit=limit,
            timeout_s=self.timeout_s,
            max_retries=self.max_retries,
            session=self.session,
            client_id=self.client_id,
            access_token=self.access_token,
        )


def fetch_top_channels(
    limit: int = 5000,
    timeout_s: float = 10.0,
    max_retries: int = 3,
    backoff_base_s: float = 1.0,
    session: requests.Session | None = None,
) -> List[str]:
    """Backward-compatible login-only view of :func:`fetch_top_streams`."""
    return [
        target.broadcaster_login
        for target in fetch_top_streams(
            limit=limit,
            timeout_s=timeout_s,
            max_retries=max_retries,
            backoff_base_s=backoff_base_s,
            session=session,
        )
    ]


def update_channels_file(channels: List[str], file_path: str | None = None) -> None:
    if file_path is None:
        file_path = CHANNEL_FILE
    try:
        with open(file_path, "w", encoding="utf-8") as file_handle:
            for channel in channels:
                file_handle.write(f"{channel}\n")
    except OSError as exc:
        raise ChannelDiscoveryError(f"Failed to write {file_path}: {exc}") from exc


def update_channel_list(limit: int = 5000) -> List[str]:
    print(f"Fetching top {limit} Twitch channels...")
    channels = fetch_top_channels(limit=limit)

    if not channels:
        raise ChannelDiscoveryError("No channels returned by Twitch API")

    update_channels_file(channels)
    print(f"Updated {CHANNEL_FILE} with {len(channels)} channels.")
    return channels


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update channels.txt from Twitch top streams")
    parser.add_argument("--limit", type=int, default=5000, help="Maximum number of channels to fetch")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        update_channel_list(limit=args.limit)
    except ChannelDiscoveryError as exc:
        print(f"Channel update failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
