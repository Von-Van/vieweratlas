import argparse
import os
import sys
import time
from typing import List

import requests
from dotenv import load_dotenv

load_dotenv()

CHANNEL_FILE = os.getenv("CHANNELS_FILE", "channels.txt")
TWITCH_STREAMS_URL = "https://api.twitch.tv/helix/streams"


class ChannelDiscoveryError(RuntimeError):
    """Raised when top-channel discovery cannot complete safely."""


def _get_twitch_headers() -> dict:
    client_id = os.getenv("TWITCH_CLIENT_ID")
    oauth_token = os.getenv("TWITCH_OAUTH_TOKEN")

    if not client_id:
        raise ChannelDiscoveryError("TWITCH_CLIENT_ID is not set")
    if not oauth_token:
        raise ChannelDiscoveryError("TWITCH_OAUTH_TOKEN is not set")

    return {
        "Client-ID": client_id,
        "Authorization": f"Bearer {oauth_token}",
    }


def fetch_top_channels(
    limit: int = 5000,
    timeout_s: float = 10.0,
    max_retries: int = 3,
    backoff_base_s: float = 1.0,
    session: requests.Session | None = None,
) -> List[str]:
    """Fetch top Twitch channels with timeout and bounded retries."""
    if limit <= 0:
        return []

    headers = _get_twitch_headers()
    client = session or requests.Session()

    channels: List[str] = []
    cursor = None
    total_fetched = 0

    while total_fetched < limit:
        params = {"first": min(100, limit - total_fetched)}
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
                        f"Twitch API request rejected with HTTP {status}. Check TWITCH_CLIENT_ID and TWITCH_OAUTH_TOKEN."
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
                sleep_for = backoff_base_s * (2 ** (attempt - 1))
                time.sleep(sleep_for)

        if payload is None:
            raise ChannelDiscoveryError(
                f"Failed to fetch channels after {max_retries + 1} attempts"
            ) from last_error

        streams = payload.get("data", [])
        if not streams:
            break

        channels.extend(stream["user_login"].lower() for stream in streams)
        total_fetched += len(streams)

        cursor = payload.get("pagination", {}).get("cursor")
        if not cursor:
            break

    return channels


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
