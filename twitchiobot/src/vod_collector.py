"""
VOD Chatter Collection Module

Extends ViewerAtlas to ingest historical VOD chat data and emit canonical
PresenceSnapshot records compatible with the existing live collection pipeline.

This module converts VOD chat messages into time-bucketed presence snapshots,
allowing historical viewer data to supplement live collections.

Design based on: vodintegr.txt specification
"""

import json
import subprocess
import logging
import requests
from pathlib import Path
from typing import Dict, List, Set, Optional, Tuple
from datetime import datetime
from dataclasses import dataclass, asdict
from collections import defaultdict
import os

try:
    from storage import get_storage, BaseStorage
    HAS_STORAGE = True
except ImportError:
    HAS_STORAGE = False

logger = logging.getLogger(__name__)


@dataclass
class PresenceSnapshot:
    """
    Canonical presence record compatible with live collection format.
    
    Invariants:
    - chatters must be unique (enforced via set conversion)
    - bucket_len_s must be consistent system-wide
    - exactly one of bucket_start_ts or offset_s must be non-null
    - usernames must be normalized (lowercase)
    """
    platform: str  # "twitch"
    source: str  # "live" or "vod"
    channel_login: str
    channel_id: Optional[str]
    content_id: str  # "live:<timestamp>" or "vod:<vod_id>"
    bucket_start_ts: Optional[str]  # ISO timestamp or None
    offset_s: Optional[int]  # Offset from VOD start in seconds or None
    bucket_len_s: int  # Bucket window size in seconds (default 60)
    chatters: List[str]  # Lowercase, unique usernames
    
    def __post_init__(self):
        """Validate invariants"""
        # Exactly one of bucket_start_ts or offset_s must be set
        if (self.bucket_start_ts is None) == (self.offset_s is None):
            raise ValueError("Exactly one of bucket_start_ts or offset_s must be non-null")
        
        # Ensure chatters are unique and lowercase
        self.chatters = list(set(u.lower() for u in self.chatters))
    
    def to_live_snapshot_format(self) -> dict:
        """
        Convert PresenceSnapshot to the live collection JSON format.
        
        Returns dict compatible with DataAggregator.load_json_snapshots()
        """
        # Calculate timestamp
        if self.bucket_start_ts:
            timestamp = self.bucket_start_ts
        else:
            # For VOD offset, we don't have absolute timestamp
            timestamp = f"{self.content_id}_offset_{self.offset_s}"
        
        return {
            "channel": self.channel_login,
            "timestamp": timestamp,
            "viewer_count": len(self.chatters),
            "game_name": "Unknown",  # Not available from VOD chat
            "title": "VOD Replay",
            "started_at": self.bucket_start_ts or "Unknown",
            "chatters": self.chatters,
            # VOD-specific metadata
            "platform": self.platform,
            "source": self.source,
            "content_id": self.content_id,
            "bucket_len_s": self.bucket_len_s,
            "bucket_start_ts": self.bucket_start_ts,
            "offset_s": self.offset_s,
            # Legacy underscore keys for backward compatibility
            "_source": self.source,
            "_content_id": self.content_id,
            "_bucket_len_s": self.bucket_len_s,
            "_offset_s": self.offset_s
        }


class VODChatDownloader:
    """
    Downloads VOD chat using TwitchDownloaderCLI.
    
    Requires TwitchDownloaderCLI to be installed:
    https://github.com/lay295/TwitchDownloader
    """
    
    def __init__(self, cli_path: str = "TwitchDownloaderCLI"):
        """
        Initialize downloader.
        
        Args:
            cli_path: Path to TwitchDownloaderCLI executable
        """
        self.cli_path = cli_path
    
    def download_vod_chat(
        self, 
        vod_id: str, 
        output_path: str,
        compression: str = "None"
    ) -> bool:
        """
        Download VOD chat replay to JSON file.
        
        Args:
            vod_id: Twitch VOD ID (numeric ID without 'v' prefix)
            output_path: Path to save JSON output
            compression: Compression format ("None", "Gzip")
            
        Returns:
            True if successful, False otherwise
        """
        try:
            cmd = [
                self.cli_path,
                "chatdownload",
                "--id", str(vod_id),
                "--output", output_path,
                "--compression", compression,
                "--embed-images", "false",  # Reduce file size
                "--chat-connections", "4"   # Parallel downloads
            ]
            
            logger.info(f"Downloading VOD {vod_id} chat to {output_path}")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout
            )
            
            if result.returncode == 0:
                logger.info(f"Successfully downloaded VOD {vod_id} chat")
                return True
            else:
                logger.error(f"TwitchDownloaderCLI failed: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            logger.error(f"Timeout downloading VOD {vod_id}")
            return False
        except FileNotFoundError:
            logger.error(f"TwitchDownloaderCLI not found at {self.cli_path}")
            logger.error("Install from: https://github.com/lay295/TwitchDownloader")
            return False
        except Exception as e:
            logger.error(f"Error downloading VOD {vod_id}: {e}")
            return False


class VODChatParser:
    """
    Parses TwitchDownloader JSON and bucketizes messages into presence snapshots.
    """
    
    def __init__(self, bucket_len_s: int = 60):
        """
        Initialize parser.
        
        Args:
            bucket_len_s: Time window size in seconds (default 60)
        """
        self.bucket_len_s = bucket_len_s
    
    def parse_and_bucketize(
        self, 
        json_path: str,
        channel_login: str,
        vod_id: str,
        channel_id: Optional[str] = None
    ) -> List[PresenceSnapshot]:
        """
        Parse VOD chat JSON and create bucketed presence snapshots.
        
        Args:
            json_path: Path to TwitchDownloader JSON output
            channel_login: Channel login name
            vod_id: VOD ID
            channel_id: Optional channel ID
            
        Returns:
            List of PresenceSnapshot objects
        """
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            comments = data.get('comments', [])
            if not comments:
                logger.warning(f"No comments found in {json_path}")
                return []
            
            logger.info(f"Parsing {len(comments)} messages from VOD {vod_id}")
            
            # Group messages by time bucket
            buckets: Dict[int, Set[str]] = defaultdict(set)
            
            for comment in comments:
                # Extract username and offset
                username = comment.get('commenter', {}).get('login', '').lower()
                offset_s = int(comment.get('content_offset_seconds', 0))
                
                if not username:
                    continue
                
                # Assign to bucket
                bucket_id = offset_s // self.bucket_len_s
                buckets[bucket_id].add(username)
            
            # Create PresenceSnapshot for each bucket
            snapshots = []
            for bucket_id in sorted(buckets.keys()):
                bucket_start = bucket_id * self.bucket_len_s
                chatters = list(buckets[bucket_id])
                
                snapshot = PresenceSnapshot(
                    platform="twitch",
                    source="vod",
                    channel_login=channel_login,
                    channel_id=channel_id,
                    content_id=f"vod:{vod_id}",
                    bucket_start_ts=None,  # VOD uses offset instead
                    offset_s=bucket_start,
                    bucket_len_s=self.bucket_len_s,
                    chatters=chatters
                )
                snapshots.append(snapshot)
            
            logger.info(f"Created {len(snapshots)} presence snapshots from {len(buckets)} buckets")
            return snapshots
            
        except Exception as e:
            logger.error(f"Error parsing {json_path}: {e}")
            return []


class VODQueue:
    """
    Manages queue of VODs to process.
    
    Queue stored as JSON with fields:
    - vod_id: Twitch VOD ID
    - channel_login: Channel name
    - status: pending | processing | completed | failed
    - attempt_count: Number of processing attempts
    - next_attempt_at: ISO timestamp for backoff
    - lease_expires_at: ISO timestamp while processing
    - processing_by: identifier for worker holding lease
    - created_at: ISO timestamp
    - updated_at: ISO timestamp
    """
    
    def __init__(self, queue_file: str = "vod_queue.json"):
        """
        Initialize VOD queue.
        
        Args:
            queue_file: Path to queue JSON file
        """
        self.queue_file = Path(queue_file)
        self.queue: List[dict] = []
        self.max_attempts = 5
        self.default_lease_seconds = 900  # 15 minutes
        self.load()
    
    def load(self):
        """Load queue from file"""
        if self.queue_file.exists():
            try:
                with open(self.queue_file, 'r') as f:
                    self.queue = json.load(f)
            except Exception as e:
                logger.error(f"Error loading queue: {e}")
                self.queue = []
    
    def save(self):
        """Save queue to file"""
        try:
            with open(self.queue_file, 'w') as f:
                json.dump(self.queue, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving queue: {e}")
    
    def add_vod(self, vod_id: str, channel_login: str):
        """Add VOD to queue"""
        # Check if already exists
        for item in self.queue:
            if item['vod_id'] == vod_id:
                logger.warning(f"VOD {vod_id} already in queue")
                return
        
        now = datetime.now().isoformat()
        self.queue.append({
            'vod_id': vod_id,
            'channel_login': channel_login.lower(),
            'status': 'pending',
            'attempt_count': 0,
            'next_attempt_at': now,
            'lease_expires_at': None,
            'processing_by': None,
            'created_at': now,
            'updated_at': now
        })
        self.save()
        logger.info(f"Added VOD {vod_id} ({channel_login}) to queue")
    
    def _now_iso(self) -> str:
        return datetime.now().isoformat()

    def _take_lease(self, item: dict) -> dict:
        lease_until = datetime.now().timestamp() + self.default_lease_seconds
        item['status'] = 'processing'
        item['lease_expires_at'] = datetime.fromtimestamp(lease_until).isoformat()
        item['processing_by'] = os.getenv("HOSTNAME", "vod-worker")
        item['attempt_count'] += 1
        item['updated_at'] = self._now_iso()
        self.save()
        return item

    def get_next_pending(self) -> Optional[dict]:
        """Get next pending VOD with backoff and lease handling."""
        now_ts = datetime.now().timestamp()

        # Release stale processing leases
        for item in self.queue:
            lease_expires_at = item.get('lease_expires_at')
            if item.get('status') == 'processing' and lease_expires_at:
                try:
                    lease_ts = datetime.fromisoformat(lease_expires_at).timestamp()
                    if now_ts > lease_ts:
                        item['status'] = 'pending'
                        item['processing_by'] = None
                        item['lease_expires_at'] = None
                        item['updated_at'] = self._now_iso()
                except ValueError:
                    item['status'] = 'pending'
                    item['processing_by'] = None
                    item['lease_expires_at'] = None
                    item['updated_at'] = self._now_iso()

        # Find next eligible pending item honoring backoff
        eligible = []
        for item in self.queue:
            if item.get('status') != 'pending':
                continue
            if item.get('attempt_count', 0) >= self.max_attempts:
                continue
            next_at = item.get('next_attempt_at')
            if next_at:
                try:
                    next_ts = datetime.fromisoformat(next_at).timestamp()
                    if now_ts < next_ts:
                        continue
                except ValueError:
                    pass
            eligible.append(item)

        # Sort by created_at to preserve FIFO
        eligible.sort(key=lambda x: x.get('created_at', ''))
        if not eligible:
            self.save()
            return None

        item = eligible[0]
        return self._take_lease(item)
    
    def update_status(self, vod_id: str, status: str, error: Optional[str] = None):
        """Update VOD status and schedule backoff if failed."""
        for item in self.queue:
            if item['vod_id'] != vod_id:
                continue

            item['status'] = status
            item['processing_by'] = None
            item['lease_expires_at'] = None

            if status == 'failed':
                attempts = item.get('attempt_count', 0)
                backoff_seconds = min(3600, 30 * (2 ** attempts))
                next_attempt = datetime.now().timestamp() + backoff_seconds
                item['next_attempt_at'] = datetime.fromtimestamp(next_attempt).isoformat()
                logger.warning(f"VOD {vod_id} failed (attempt {attempts}); next attempt after {backoff_seconds}s")
                if error:
                    logger.warning(f"Reason: {error}")
            elif status in ('completed', 'pending'):
                item['next_attempt_at'] = self._now_iso()

            item['updated_at'] = self._now_iso()
            self.save()
            logger.info(f"VOD {vod_id} status: {status} (attempts: {item.get('attempt_count', 0)})")
            return
    
    def get_stats(self) -> dict:
        """Get queue statistics"""
        stats = {
            'total': len(self.queue),
            'pending': 0,
            'processing': 0,
            'completed': 0,
            'failed': 0
        }
        for item in self.queue:
            status = item['status']
            if status in stats:
                stats[status] += 1
        return stats


def get_recent_vods(
    channel_login: str, 
    limit: int = 5, 
    max_age_days: int = 14,
    min_views: int = 0
) -> List[Tuple[str, str]]:
    """
    Fetch recent VODs for a channel using Twitch Helix API.
    
    Args:
        channel_login: Channel name
        limit: Number of recent VODs to fetch (max 100)
        max_age_days: Maximum age of VODs in days (default 14)
        min_views: Minimum view count filter (default 0)
        
    Returns:
        List of (vod_id, channel_login) tuples
    """
    client_id = os.getenv("TWITCH_CLIENT_ID")
    oauth_token = os.getenv("TWITCH_OAUTH_TOKEN")
    
    if not client_id or not oauth_token:
        logger.error("TWITCH_CLIENT_ID and TWITCH_OAUTH_TOKEN must be set")
        return []
    
    # Calculate cutoff date
    from datetime import timedelta
    cutoff_date = datetime.now() - timedelta(days=max_age_days)
    
    # First get user ID from login
    user_url = "https://api.twitch.tv/helix/users"
    headers = {
        "Client-ID": client_id,
        "Authorization": f"Bearer {oauth_token}"
    }
    
    try:
        response = requests.get(
            user_url,
            headers=headers,
            params={"login": channel_login},
            timeout=10
        )
        response.raise_for_status()
        
        users = response.json().get("data", [])
        if not users:
            logger.error(f"Channel {channel_login} not found")
            return []
        
        user_id = users[0]["id"]
        
        # Now get VODs
        videos_url = "https://api.twitch.tv/helix/videos"
        response = requests.get(
            videos_url,
            headers=headers,
            params={
                "user_id": user_id,
                "first": min(limit, 100),
                "type": "archive"  # Only archived streams
            },
            timeout=10
        )
        response.raise_for_status()
        
        videos = response.json().get("data", [])
        
        # Filter by age and view count
        vod_list = []
        for video in videos:
            # Parse created_at timestamp
            try:
                created_at = datetime.fromisoformat(video["created_at"].replace("Z", "+00:00"))
                if created_at < cutoff_date:
                    continue
            except (ValueError, KeyError):
                logger.warning(f"Could not parse created_at for VOD {video.get('id')}")
                continue
            
            # Check view count
            view_count = video.get("view_count", 0)
            if view_count < min_views:
                continue
            
            vod_list.append((video["id"], channel_login))
        
        logger.info(f"Found {len(vod_list)} recent VODs for {channel_login} (filtered from {len(videos)})") 
        return vod_list
        
    except Exception as e:
        logger.error(f"Error fetching VODs for {channel_login}: {e}")
        return []


def get_recent_vods_batch(
    channels: List[str],
    limit_per_channel: int = 5,
    max_age_days: int = 14,
    min_views: int = 0
) -> List[Tuple[str, str]]:
    """
    Fetch recent VODs for multiple channels efficiently.
    
    Args:
        channels: List of channel names
        limit_per_channel: Number of VODs per channel
        max_age_days: Maximum age of VODs in days
        min_views: Minimum view count filter
        
    Returns:
        List of (vod_id, channel_login) tuples
    """
    all_vods = []
    for channel in channels:
        vods = get_recent_vods(
            channel,
            limit=limit_per_channel,
            max_age_days=max_age_days,
            min_views=min_views
        )
        all_vods.extend(vods)
    
    logger.info(f"Discovered {len(all_vods)} total VODs across {len(channels)} channels")
    return all_vods


class VODCollector:
    """
    Main VOD collection orchestrator.
    
    Pipeline: VOD Queue → Download → Parse → Bucketize → Write Snapshots
    """
    
    def __init__(
        self,
        storage: Optional[BaseStorage] = None,
        queue_file: str = "vod_queue.json",
        raw_dir: str = "vod_raw",
        bucket_len_s: int = 60,
        cli_path: str = "TwitchDownloaderCLI",
        max_age_days: int = 14,
        min_views: int = 0
    ):
        """
        Initialize VOD collector.
        
        Args:
            storage: Storage backend (auto-detects if None)
            queue_file: Path to VOD queue file
            raw_dir: Directory for raw VOD chat JSON files
            bucket_len_s: Bucket window size in seconds
            cli_path: Path to TwitchDownloaderCLI
            max_age_days: Maximum VOD age in days (default 14)
            min_views: Minimum view count filter (default 0)
        """
        # Initialize storage backend
        if storage is not None:
            self.storage = storage
        elif HAS_STORAGE:
            self.storage = get_storage()
        else:
            self.storage = None
        
        self.queue = VODQueue(queue_file)
        self.raw_dir = Path(raw_dir)
        self.raw_dir.mkdir(exist_ok=True)
        
        self.downloader = VODChatDownloader(cli_path)
        self.parser = VODChatParser(bucket_len_s)
        self.max_age_days = max_age_days
        self.min_views = min_views
    
    def add_vods_for_channels(self, channels: List[str], vod_limit: int = 5):
        """
        Discover and add recent VODs for channels to queue.
        
        Args:
            channels: List of channel names
            vod_limit: Number of recent VODs per channel to add
        """
        logger.info(f"Discovering VODs for {len(channels)} channels (max age: {self.max_age_days} days, min views: {self.min_views})")
        
        vods = get_recent_vods_batch(
            channels,
            limit_per_channel=vod_limit,
            max_age_days=self.max_age_days,
            min_views=self.min_views
        )
        
        for vod_id, channel_login in vods:
            self.queue.add_vod(vod_id, channel_login)
        
        stats = self.queue.get_stats()
        logger.info(f"VOD discovery complete. Queue stats: {stats}")
    
    def process_next_vod(self) -> bool:
        """
        Process the next pending VOD in the queue.
        
        Returns:
            True if a VOD was processed, False if queue is empty
        """
        vod = self.queue.get_next_pending()
        if not vod:
            logger.info("No pending VODs in queue")
            return False
        
        vod_id = vod['vod_id']
        channel = vod['channel_login']
        
        logger.info(f"Processing VOD {vod_id} ({channel})")
        # Lease already taken in get_next_pending()
        
        try:
            # Step 1: Download VOD chat
            raw_path = self.raw_dir / f"{channel}_{vod_id}.json"
            success = self.downloader.download_vod_chat(vod_id, str(raw_path))
            
            if not success:
                self.queue.update_status(vod_id, 'failed', error="download failed")
                return False
            
            # Step 2: Store raw JSON (if using S3)
            if self.storage:
                raw_key = f"raw/vod_chat/channel={channel}/vod_id={vod_id}/chat.json"
                with open(raw_path, 'r') as f:
                    raw_data = json.load(f)
                self.storage.upload_json(raw_key, raw_data)
                logger.info(f"Uploaded raw chat: {self.storage.get_uri(raw_key)}")
            
            # Step 3: Parse and bucketize
            snapshots = self.parser.parse_and_bucketize(
                str(raw_path),
                channel,
                vod_id
            )
            
            if not snapshots:
                logger.warning(f"No snapshots generated for VOD {vod_id}")
                self.queue.update_status(vod_id, 'failed', error="no snapshots generated")
                return False
            
            # Step 4: Write presence snapshots
            self._write_snapshots(snapshots, channel, vod_id)
            
            # Success!
            self.queue.update_status(vod_id, 'completed')
            logger.info(f"✓ Successfully processed VOD {vod_id}: {len(snapshots)} snapshots")
            return True
            
        except Exception as e:
            logger.error(f"Error processing VOD {vod_id}: {e}")
            self.queue.update_status(vod_id, 'failed', error=str(e))
            return False
    
    def _write_snapshots(
        self, 
        snapshots: List[PresenceSnapshot], 
        channel: str, 
        vod_id: str
    ):
        """Write presence snapshots to storage (Parquet preferred, JSON fallback for local debug)."""
        records = [snap.to_live_snapshot_format() for snap in snapshots]

        # Parquet batch write (spec-preferred)
        try:
            import pandas as pd
            import tempfile

            df = pd.DataFrame(records)
            if self.storage:
                parquet_key = (
                    f"curated/presence_snapshots/source=vod/"
                    f"channel={channel}/vod={vod_id}/part-0000.parquet"
                )
                with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
                    df.to_parquet(tmp.name, index=False)
                    self.storage.upload_file(parquet_key, tmp.name, content_type="application/octet-stream")
                logger.info(f"Wrote Parquet snapshots to {self.storage.get_uri(parquet_key)}")
            else:
                output_dir = Path("logs/vod_snapshots") / channel / vod_id
                output_dir.mkdir(parents=True, exist_ok=True)
                parquet_path = output_dir / "part-0000.parquet"
                df.to_parquet(parquet_path, index=False)
                logger.info(f"Wrote Parquet snapshots to {parquet_path}")
        except Exception as e:
            logger.error(f"Failed to write Parquet snapshots for VOD {vod_id}: {e}")

        # Local JSON fallback for debugging convenience
        if not self.storage:
            output_dir = Path("logs/vod_snapshots") / channel / vod_id
            output_dir.mkdir(parents=True, exist_ok=True)
            for i, record in enumerate(records):
                output_file = output_dir / f"snapshot_{i:04d}.json"
                with open(output_file, 'w') as f:
                    json.dump(record, f, indent=2)
            logger.info(f"Wrote {len(records)} JSON snapshots to {output_dir}")
    
    def process_all_pending(self, max_vods: Optional[int] = None):
        """
        Process all pending VODs in queue.
        
        Args:
            max_vods: Maximum number of VODs to process (None = all)
        """
        processed = 0
        while True:
            if max_vods and processed >= max_vods:
                break
            
            if not self.process_next_vod():
                break
            
            processed += 1
        
        stats = self.queue.get_stats()
        logger.info(f"VOD processing complete. Processed: {processed}")
        logger.info(f"Queue stats: {stats}")


# CLI Interface
if __name__ == "__main__":
    import argparse
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    parser = argparse.ArgumentParser(description="VOD Chatter Collection")
    parser.add_argument('command', choices=['add', 'discover', 'process', 'stats'],
                       help='Command to execute')
    parser.add_argument('--vod-id', help='VOD ID to add')
    parser.add_argument('--channel', help='Channel name')
    parser.add_argument('--channels-file', default='channels.txt',
                       help='File with channel list for discovery')
    parser.add_argument('--vod-limit', type=int, default=5,
                       help='VODs per channel to discover')
    parser.add_argument('--max-vods', type=int, help='Max VODs to process')
    
    args = parser.parse_args()
    
    collector = VODCollector()
    
    if args.command == 'add':
        if not args.vod_id or not args.channel:
            print("Error: --vod-id and --channel required for 'add'")
            exit(1)
        collector.queue.add_vod(args.vod_id, args.channel)
    
    elif args.command == 'discover':
        if Path(args.channels_file).exists():
            with open(args.channels_file, 'r') as f:
                channels = [line.strip() for line in f if line.strip()]
        elif args.channel:
            channels = [args.channel]
        else:
            print("Error: --channel or --channels-file required for 'discover'")
            exit(1)
        
        collector.add_vods_for_channels(channels, vod_limit=args.vod_limit)
    
    elif args.command == 'process':
        collector.process_all_pending(max_vods=args.max_vods)
    
    elif args.command == 'stats':
        stats = collector.queue.get_stats()
        print("\nVOD Queue Statistics:")
        for key, value in stats.items():
            print(f"  {key}: {value}")
