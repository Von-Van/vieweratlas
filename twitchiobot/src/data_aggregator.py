"""
Data Aggregator Module

Loads chat snapshots from logs/ directory or S3 and builds cumulative viewer data
structures for overlap analysis. Provides methods to aggregate viewer data
across multiple snapshots and time windows.

Supports both local filesystem and S3 storage backends.
"""

import json
import csv
import os
from collections import defaultdict
from typing import Dict, Set, List, Tuple, Optional
from pathlib import Path
from datetime import datetime

# Import storage abstraction
try:
    from storage import get_storage, BaseStorage
    HAS_STORAGE = True
except ImportError:
    HAS_STORAGE = False


class DataAggregator:
    """
    Aggregates viewer data from JSON/CSV log files.
    
    Maintains:
    - channel_viewers: Dict[channel_name -> Set[username]]
    - channel_metadata: Dict[channel_name -> metadata dict]
    - snapshots: List of raw snapshot data with timestamps
    """
    
    def __init__(self, logs_dir: str = "logs", storage: Optional[BaseStorage] = None):
        """
        Initialize aggregator with logs directory path or storage backend.
        
        Args:
            logs_dir: Path to directory containing log files (used for FileStorage)
            storage: Optional storage backend (auto-detects if None)
        """
        self.logs_dir = Path(logs_dir)
        self.channel_viewers: Dict[str, Set[str]] = defaultdict(set)
        self.channel_metadata: Dict[str, dict] = {}
        self.snapshots: List[dict] = []
        self.snapshot_source_counts: Dict[str, int] = defaultdict(int)
        
        # Initialize storage backend
        if storage is not None:
            self.storage = storage
        elif HAS_STORAGE:
            self.storage = get_storage()
        else:
            self.storage = None

    def _ingest_snapshot(self, snapshot: dict, default_source: str = "live") -> bool:
        """Normalize and ingest a single snapshot into aggregator state."""
        channel = snapshot.get("channel") or snapshot.get("channel_login") or ""
        channel = channel.lower()
        if not channel:
            return False

        chatters = snapshot.get("chatters", [])
        self.channel_viewers[channel].update(chatters)

        # Normalize metadata keys for downstream consumers
        self.channel_metadata[channel] = {
            "viewer_count": snapshot.get("viewer_count", snapshot.get("viewers", 0)),
            "game_name": snapshot.get("game_name", snapshot.get("game", "Unknown")),
            "title": snapshot.get("title", ""),
            "started_at": snapshot.get("started_at", snapshot.get("uptime", "")),
            "timestamp": snapshot.get("timestamp", "")
        }

        self.snapshots.append(snapshot)
        source = snapshot.get("_source") or snapshot.get("source") or default_source
        self.snapshot_source_counts[source] += 1
        return True
        
    def load_json_snapshots(self) -> int:
        """
        Load all JSON snapshot files from storage backend.
        
        JSON format: {
            "channel": str,
            "timestamp": str,
            "viewer_count": int,
            "game_name": str,
            "title": str,
            "started_at": str,
            "chatters": [str, str, ...]
        }
        
        Returns:
            Number of JSON files loaded
        """
        if self.storage:
            # Load from storage backend (supports S3)
            json_files = self.storage.list_files(prefix="raw/snapshots", suffix=".json")
            count = 0
            
            for json_key in json_files:
                try:
                    data = self.storage.download_json(json_key)
                    if not data:
                        continue
                    
                    # Handle both single snapshot and array of snapshots
                    if isinstance(data, list):
                        snapshots = data
                    else:
                        snapshots = [data]
                    
                    for snapshot in snapshots:
                        if self._ingest_snapshot(snapshot, default_source="live"):
                            count += 1
                
                except Exception as e:
                    print(f"Error loading {json_key}: {e}")
            
            return count
        else:
            # Legacy local filesystem loading
            if not self.logs_dir.exists():
                print(f"Logs directory {self.logs_dir} does not exist")
                return 0
            
            json_files = list(self.logs_dir.glob("*.json"))
            count = 0
            
            for json_file in json_files:
                try:
                    with open(json_file, 'r') as f:
                        data = json.load(f)
                    
                    # Handle both single snapshot and array of snapshots
                    if isinstance(data, list):
                        snapshots = data
                    else:
                        snapshots = [data]
                    
                    for snapshot in snapshots:
                        if self._ingest_snapshot(snapshot, default_source="live"):
                            count += 1
                
                except (json.JSONDecodeError, IOError) as e:
                    print(f"Error loading {json_file}: {e}")
            
            return count
    
    def load_csv_snapshots(self) -> int:
        """
        Load all CSV snapshot files from logs directory.
        
        CSV format: channel,chatter,viewers,game,title,timestamp
        
        Returns:
            Number of CSV files loaded
        """
        if not self.logs_dir.exists():
            return 0
        
        csv_files = list(self.logs_dir.glob("*.csv"))
        count = 0
        
        for csv_file in csv_files:
            try:
                with open(csv_file, 'r') as f:
                    reader = csv.DictReader(f)
                    
                    for row in reader:
                        channel = row.get("channel", "").lower()
                        chatter = row.get("chatter", "").lower()
                        
                        if not channel or not chatter:
                            continue
                        
                        # Add chatter to channel's viewer set
                        self.channel_viewers[channel].add(chatter)
                        
                        # Store metadata if not already present
                        if channel not in self.channel_metadata:
                            self.channel_metadata[channel] = {
                                "viewers": int(row.get("viewers", 0) or 0),
                                "game": row.get("game", "Unknown"),
                                "title": row.get("title", ""),
                                "timestamp": row.get("timestamp", "")
                            }
                        
                        count += 1
            
            except (csv.Error, IOError) as e:
                print(f"Error loading {csv_file}: {e}")
        
        return count

    def load_vod_snapshots(self) -> int:
        """
        Load bucketized VOD presence snapshots.

        Expected locations:
        - Storage: curated/presence_snapshots/source=vod/**.json
        - Storage: curated/presence_snapshots/source=vod/**.parquet
        - Local: logs/vod_snapshots/**/snapshot_*.json
        - Local: logs/vod_snapshots/**/part-*.parquet
        """
        count = 0

        # Parquet first (preferred format)
        try:
            import pandas as pd
        except ImportError:
            pd = None

        if self.storage:
            if pd is not None:
                parquet_keys = self.storage.list_files(
                    prefix="curated/presence_snapshots/source=vod",
                    suffix=".parquet"
                )
                for key in parquet_keys:
                    try:
                        import tempfile

                        with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
                            if not self.storage.download_file(key, tmp.name):
                                continue
                            df = pd.read_parquet(tmp.name)
                            for record in df.to_dict(orient="records"):
                                if self._ingest_snapshot(record, default_source="vod"):
                                    count += 1
                    except Exception as e:
                        print(f"Error loading {key}: {e}")

            # JSON fallback
            vod_files = self.storage.list_files(
                prefix="curated/presence_snapshots/source=vod",
                suffix=".json"
            )
            for vod_key in vod_files:
                try:
                    snapshot = self.storage.download_json(vod_key)
                    if not snapshot:
                        continue

                    if self._ingest_snapshot(snapshot, default_source="vod"):
                        count += 1
                except Exception as e:
                    print(f"Error loading {vod_key}: {e}")

            return count

        # Local filesystem fallback
        base_dir = self.logs_dir / "vod_snapshots"
        if base_dir.exists():
            if pd is not None:
                parquet_files = list(base_dir.rglob("part-*.parquet"))
                for parquet_file in parquet_files:
                    try:
                        df = pd.read_parquet(parquet_file)
                        for record in df.to_dict(orient="records"):
                            if self._ingest_snapshot(record, default_source="vod"):
                                count += 1
                    except Exception as e:
                        print(f"Error loading {parquet_file}: {e}")

            vod_files = list(base_dir.rglob("snapshot_*.json"))
            for vod_file in vod_files:
                try:
                    with open(vod_file, 'r') as f:
                        snapshot = json.load(f)

                    if self._ingest_snapshot(snapshot, default_source="vod"):
                        count += 1

                except (json.JSONDecodeError, IOError) as e:
                    print(f"Error loading {vod_file}: {e}")

        return count
    
    def load_all(self) -> Tuple[int, int, int]:
        """
        Load all available snapshots (both JSON and CSV).
        
        Returns:
            Tuple of (json_count, csv_count, vod_count)
        """
        json_count = self.load_json_snapshots()
        csv_count = self.load_csv_snapshots()
        vod_count = self.load_vod_snapshots()
        return json_count, csv_count, vod_count
    
    def get_channel_viewers(self) -> Dict[str, Set[str]]:
        """
        Get the accumulated viewer data.
        
        Returns:
            Dict mapping channel name to set of unique viewers
        """
        return dict(self.channel_viewers)
    
    def get_channel_metadata(self) -> Dict[str, dict]:
        """
        Get channel metadata (game, title, viewer count, etc.).
        
        Returns:
            Dict mapping channel name to metadata dict
        """
        return dict(self.channel_metadata)
    
    def get_statistics(self) -> dict:
        """
        Get aggregation statistics.
        
        Returns:
            Dict with stats like total channels, total unique viewers, etc.
        """
        total_snapshots = len(self.snapshots)
        total_channels = len(self.channel_viewers)
        total_unique_viewers = sum(len(viewers) for viewers in self.channel_viewers.values())
        
        # Unique viewers across all channels (not counting duplicates)
        all_viewers = set()
        for viewers in self.channel_viewers.values():
            all_viewers.update(viewers)
        
        channel_sizes = [(ch, len(viewers)) for ch, viewers in self.channel_viewers.items()]
        channel_sizes.sort(key=lambda x: x[1], reverse=True)
        
        return {
            "total_snapshots": total_snapshots,
            "total_channels": total_channels,
            "total_unique_viewers_per_channel": total_unique_viewers,
            "total_unique_viewers_across_all": len(all_viewers),
            "top_channels_by_viewers": channel_sizes[:10],
            "snapshot_sources": dict(self.snapshot_source_counts)
        }
    
    def filter_channels_by_size(self, min_viewers: int = 1) -> Dict[str, Set[str]]:
        """
        Get channel viewers filtered by minimum audience size.
        
        Args:
            min_viewers: Minimum number of unique viewers required
        
        Returns:
            Filtered dict mapping channel name to viewer set
        """
        return {
            ch: viewers for ch, viewers in self.channel_viewers.items()
            if len(viewers) >= min_viewers
        }
    
    def filter_channels_by_metadata(self, 
                                   min_viewer_count: int = 0,
                                   exclude_games: list = None) -> Dict[str, Set[str]]:
        """
        Filter channels by metadata attributes.
        
        Args:
            min_viewer_count: Minimum stream viewer count from Twitch
            exclude_games: List of games/categories to exclude (e.g., bots, inactive)
        
        Returns:
            Filtered channel viewers dict
        """
        exclude_games = exclude_games or []
        
        filtered = {}
        for ch, viewers in self.channel_viewers.items():
            if ch not in self.channel_metadata:
                # Include if no metadata (safer)
                filtered[ch] = viewers
                continue
            
            meta = self.channel_metadata[ch]
            
            # Check viewer count threshold
            if meta.get("viewers", 0) < min_viewer_count:
                continue
            
            # Check excluded games
            game = meta.get("game", "Unknown").lower()
            if any(excl.lower() in game for excl in exclude_games):
                continue
            
            filtered[ch] = viewers
        
        return filtered
    
    def get_user_channel_map(self) -> Dict[str, Set[str]]:
        """
        Build user-centric view: each user mapped to channels they appear in.
        
        Useful for analyzing user behavior across channels.
        
        Returns:
            Dict mapping username -> set of channels
        """
        user_channels = {}
        for channel, viewers in self.channel_viewers.items():
            for viewer in viewers:
                if viewer not in user_channels:
                    user_channels[viewer] = set()
                user_channels[viewer].add(channel)
        
        return user_channels
    
    def filter_by_repeat_viewers(self, min_appearances: int = 1) -> Dict[str, Set[str]]:
        """
        Filter to only include viewers who appear in multiple channels.
        
        This helps identify genuinely engaged viewers vs one-time visitors.
        
        Args:
            min_appearances: Minimum number of different channels user must appear in
        
        Returns:
            Filtered channel viewers dict (with reduced viewer sets)
        """
        # Get user-to-channels mapping
        user_channels = self.get_user_channel_map()
        
        # Find repeat viewers
        repeat_viewers = {
            user for user, channels in user_channels.items()
            if len(channels) >= min_appearances
        }
        
        # Filter channels to only include repeat viewers
        filtered = {}
        for channel, viewers in self.channel_viewers.items():
            filtered_viewers = viewers & repeat_viewers
            if filtered_viewers:  # Only include if has repeat viewers
                filtered[channel] = filtered_viewers
        
        return filtered
    
    def get_data_quality_report(self) -> dict:
        """
        Generate a data quality report for diagnostics.
        
        Returns:
            Dict with quality metrics
        """
        user_channels = self.get_user_channel_map()
        all_viewers = set()
        for viewers in self.channel_viewers.values():
            all_viewers.update(viewers)
        
        # Calculate distribution stats
        channel_sizes = [len(viewers) for viewers in self.channel_viewers.values()]
        user_appearances = [len(channels) for channels in user_channels.values()]
        
        return {
            "total_channels": len(self.channel_viewers),
            "total_unique_viewers": len(all_viewers),
            "total_snapshots": len(self.snapshots),
            "avg_viewers_per_channel": sum(channel_sizes) / len(channel_sizes) if channel_sizes else 0,
            "median_viewers_per_channel": sorted(channel_sizes)[len(channel_sizes)//2] if channel_sizes else 0,
            "max_viewers_in_channel": max(channel_sizes) if channel_sizes else 0,
            "min_viewers_in_channel": min(channel_sizes) if channel_sizes else 0,
            "avg_channels_per_viewer": sum(user_appearances) / len(user_appearances) if user_appearances else 0,
            "repeat_viewers_2plus": sum(1 for c in user_appearances if c >= 2),
            "repeat_viewers_3plus": sum(1 for c in user_appearances if c >= 3),
            "one_off_viewers": sum(1 for c in user_appearances if c == 1),
            "one_off_percentage": (sum(1 for c in user_appearances if c == 1) / len(user_appearances) * 100) if user_appearances else 0
        }


if __name__ == "__main__":
    # Test the aggregator
    aggregator = DataAggregator("logs")
    json_count, csv_count, vod_count = aggregator.load_all()
    
    print(f"Loaded {json_count} JSON snapshots, {csv_count} CSV rows, and {vod_count} VOD snapshots")
    print("\nStatistics:")
    stats = aggregator.get_statistics()
    for key, value in stats.items():
        print(f"  {key}: {value}")
