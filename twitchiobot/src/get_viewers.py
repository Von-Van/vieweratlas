import os
import csv
import json
import logging
import requests
import asyncio
from datetime import datetime
from io import BytesIO
from time import sleep
from twitchio.ext import commands
from dotenv import load_dotenv
from daily_collection_state import DailyCollectionState

# Import storage abstraction
try:
    from storage import get_storage, BaseStorage
    HAS_STORAGE = True
except ImportError:
    HAS_STORAGE = False

load_dotenv()

logger = logging.getLogger(__name__)

CHANNELS_FILE = os.getenv("CHANNELS_FILE", "channels.txt")


def load_channels_from_file(path=None):
    if path is None:
        path = CHANNELS_FILE
    if os.path.exists(path):
        with open(path, "r") as f:
            return [line.strip().lower() for line in f if line.strip()]
    return []

def load_channels():
    channels = load_channels_from_file()
    if channels:
        return channels
    env_channels = os.getenv("TWITCH_CHANNELS", "")
    return [c.strip().lower() for c in env_channels.split(",") if c.strip()]

class ChatLogger(commands.Bot):
    def __init__(self, token, channels, output_dir="logs", storage=None):
        super().__init__(
            token=token,
            prefix="!",
            initial_channels=channels
        )
        self.output_dir = output_dir
        
        # Initialize storage backend
        if storage is not None:
            self.storage = storage
        elif HAS_STORAGE:
            # Auto-detect from environment
            self.storage = get_storage()
        else:
            # Fallback to local files (legacy)
            self.storage = None
            os.makedirs(self.output_dir, exist_ok=True)
        
        self.chatters = {channel: set() for channel in channels}
        self.start_time = None
        self.stream_data = {}
        self.failed_channels = {}  # Track failed channels
        if self.storage:
            self.daily_state = DailyCollectionState(storage=self.storage)
        else:
            local_state_path = os.path.join(self.output_dir, "state", "daily_collection_state.json")
            self.daily_state = DailyCollectionState(local_state_path=local_state_path)
        self.collection_stats = {
            "successful": 0,
            "failed": 0,
            "skipped": 0
        }

        self.client_id = os.getenv("TWITCH_CLIENT_ID")
        self.oauth_token = os.getenv("TWITCH_OAUTH_TOKEN")

    async def event_ready(self):
        print(f"✅ Bot ready. Logged in as: {self.nick}")

    async def event_message(self, message):
        if message.echo:
            return
        user = message.author.name.lower()
        channel = message.channel.name.lower()
        self.chatters[channel].add(user)

    def fetch_stream_info(self, channel_name):
        """
        Fetch stream info from Twitch API with retry logic.
        
        Args:
            channel_name: Twitch channel name
            
        Returns:
            Stream info dict or None if failed after retries
        """
        url = "https://api.twitch.tv/helix/streams"
        headers = {
            "Client-ID": self.client_id,
            "Authorization": f"Bearer {self.oauth_token}"
        }
        params = {"user_login": channel_name}
        
        max_retries = 3
        backoff_factor = 2  # exponential backoff: 1s, 2s, 4s
        
        for attempt in range(max_retries):
            try:
                response = requests.get(
                    url, 
                    headers=headers, 
                    params=params,
                    timeout=10
                )
                response.raise_for_status()
                data = response.json().get("data", [])
                
                if data:
                    logger.debug(f"[{channel_name}] Stream info fetched successfully")
                    stream_info = data[0]
                    # Extract broadcaster_language for community tagging
                    if "language" in stream_info:
                        stream_info["broadcaster_language"] = stream_info["language"]
                    return stream_info
                else:
                    logger.warning(f"[{channel_name}] Stream offline or not found")
                    return None
                    
            except requests.exceptions.Timeout:
                wait_time = backoff_factor ** attempt
                logger.warning(
                    f"[{channel_name}] Timeout (attempt {attempt + 1}/{max_retries}). "
                    f"Retrying in {wait_time}s..."
                )
                if attempt < max_retries - 1:
                    sleep(wait_time)
                    
            except requests.exceptions.ConnectionError:
                wait_time = backoff_factor ** attempt
                logger.warning(
                    f"[{channel_name}] Connection error (attempt {attempt + 1}/{max_retries}). "
                    f"Retrying in {wait_time}s..."
                )
                if attempt < max_retries - 1:
                    sleep(wait_time)
                    
            except requests.exceptions.HTTPError as e:
                if response.status_code == 401:
                    logger.error(f"[{channel_name}] Auth failed. Check TWITCH_OAUTH_TOKEN")
                    self.failed_channels[channel_name] = "AUTH_ERROR"
                    return None
                elif response.status_code == 404:
                    logger.warning(f"[{channel_name}] Channel not found (404)")
                    self.failed_channels[channel_name] = "NOT_FOUND"
                    return None
                else:
                    logger.warning(
                        f"[{channel_name}] HTTP error {response.status_code} "
                        f"(attempt {attempt + 1}/{max_retries})"
                    )
                    if attempt < max_retries - 1:
                        sleep(backoff_factor ** attempt)
                        
            except Exception as e:
                logger.error(f"[{channel_name}] Unexpected error: {e}")
                self.failed_channels[channel_name] = f"ERROR: {str(e)[:50]}"
                return None
        
        # Max retries exhausted
        logger.error(f"[{channel_name}] Failed after {max_retries} retries")
        self.failed_channels[channel_name] = "MAX_RETRIES_EXHAUSTED"
        return None

    async def log_results(self):
        """Log collection results for all channels, gracefully handling failures."""
        timestamp = datetime.now().isoformat(timespec="seconds")
        self.collection_stats = {
            "successful": 0,
            "failed": 0,
            "skipped": 0
        }

        # Collect successful channel data for batch Parquet write
        parquet_rows = []

        for channel, users in self.chatters.items():
            if channel in self.failed_channels:
                logger.warning(f"[{channel}] Skipping (previous failure): {self.failed_channels[channel]}")
                self.collection_stats["skipped"] += 1
                continue

            if self.daily_state.has_collected("live", channel):
                logger.info(f"[{channel}] Skipping live snapshot: already collected today (UTC)")
                self.collection_stats["skipped"] += 1
                continue

            stream_info = self.fetch_stream_info(channel)

            if not stream_info:
                logger.error(f"[{channel}] Failed to fetch stream info, skipping log")
                self.collection_stats["failed"] += 1
                continue

            try:
                viewer_count = stream_info.get("viewer_count", 0)
                game_name = stream_info.get("game_name", "Unknown")
                title = stream_info.get("title", "")
                started_at = stream_info.get("started_at", "")
                language = stream_info.get("language", stream_info.get("broadcaster_language", ""))

                # Store in-memory summary for optional reuse
                self.stream_data[channel] = {
                    "channel": channel,
                    "timestamp": timestamp,
                    "viewer_count": viewer_count,
                    "game_name": game_name,
                    "language": language,
                    "title": title,
                    "started_at": started_at,
                    "chatters": list(users)
                }

                print(f"\n#{channel} Stream Info:")
                print(f"  Title       : {title}")
                print(f"  Game        : {game_name}")
                print(f"  Viewers     : {viewer_count}")
                print(f"  Start Time  : {started_at}")
                print(f"  Chatters    : {len(users)}")

                parquet_rows.append({
                    "channel": channel,
                    "timestamp": timestamp,
                    "viewer_count": viewer_count,
                    "game_name": game_name,
                    "title": title,
                    "started_at": started_at,
                    "language": language,
                    "chatters_json": json.dumps(sorted(users)),
                })

                self.daily_state.mark_collected("live", channel)
                self.collection_stats["successful"] += 1

            except Exception as e:
                logger.error(f"[{channel}] Error processing {channel}: {e}")
                self.collection_stats["failed"] += 1

        # Batch write: one Parquet file per cycle with all channels
        if parquet_rows and self.storage:
            self._flush_cycle_parquet(parquet_rows)
        elif parquet_rows:
            self._flush_cycle_parquet_local(parquet_rows)

        # Print summary statistics
        self.print_collection_stats()

    def _flush_cycle_parquet(self, rows: list):
        """Write all channel data for this cycle as a single Parquet file to storage."""
        try:
            import pandas as pd
            df = pd.DataFrame(rows)
            buf = BytesIO()
            df.to_parquet(buf, index=False, engine="pyarrow")
            parquet_bytes = buf.getvalue()

            date_partition = datetime.now().strftime('%Y/%m/%d')
            cycle_ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            key = f"raw/snapshots/{date_partition}/cycle_{cycle_ts}.parquet"

            self.storage.upload_parquet(key, parquet_bytes)
            logger.info(f"Cycle Parquet saved ({len(rows)} channels): {self.storage.get_uri(key)}")
        except Exception as e:
            logger.error(f"Failed to write cycle Parquet: {e}")

    def _flush_cycle_parquet_local(self, rows: list):
        """Fallback: write Parquet to local filesystem without storage abstraction."""
        try:
            import pandas as pd
            df = pd.DataFrame(rows)
            date_dir = os.path.join(self.output_dir, "raw", "snapshots",
                                    datetime.now().strftime('%Y/%m/%d'))
            os.makedirs(date_dir, exist_ok=True)
            cycle_ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            path = os.path.join(date_dir, f"cycle_{cycle_ts}.parquet")
            df.to_parquet(path, index=False, engine="pyarrow")
            logger.info(f"Cycle Parquet saved locally ({len(rows)} channels): {path}")
        except Exception as e:
            logger.error(f"Failed to write local cycle Parquet: {e}")
    
    def print_collection_stats(self):
        """Print collection statistics."""
        total = sum(self.collection_stats.values())
        print(f"\n{'='*60}")
        print(f"Collection Statistics (Total: {total} channels)")
        print(f"  ✓ Successful:  {self.collection_stats['successful']}")
        print(f"  ✗ Failed:      {self.collection_stats['failed']}")
        print(f"  ⊘ Skipped:     {self.collection_stats['skipped']}")
        
        if self.failed_channels:
            print(f"\nFailed Channels:")
            for channel, reason in sorted(self.failed_channels.items()):
                print(f"  - {channel}: {reason}")
        print(f"{'='*60}\n")
