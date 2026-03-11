"""
Daily collection state tracking.

Tracks whether live or VOD chatter data has already been collected for a
channel on the current UTC day. Supports both local filesystem and storage
backends (FileStorage/S3Storage via BaseStorage).
"""

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import boto3
    from botocore.exceptions import ClientError
    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False


class DailyCollectionState:
    """Persists per-channel daily collection markers for live and VOD sources."""

    def __init__(
        self,
        storage=None,
        storage_key: str = "state/daily_collection_state.json",
        local_state_path: str = "logs/state/daily_collection_state.json",
        retention_days: int = 30
    ):
        self.storage = storage
        self.storage_key = storage_key
        self.local_state_path = Path(local_state_path)
        self.retention_days = retention_days
        self._state = None

    def current_utc_day(self) -> str:
        """Return current day in UTC as YYYY-MM-DD."""
        return datetime.now(timezone.utc).date().isoformat()

    def has_collected(self, source: str, channel_login: str, utc_day: Optional[str] = None) -> bool:
        """Check whether source/channel has been collected for utc_day."""
        self._ensure_loaded()
        day = utc_day or self.current_utc_day()
        channel = channel_login.lower()
        self._validate_source(source)
        return self._state[source].get(channel) == day

    def mark_collected(self, source: str, channel_login: str, utc_day: Optional[str] = None) -> bool:
        """Mark source/channel as collected for utc_day and persist."""
        self._ensure_loaded()
        day = utc_day or self.current_utc_day()
        channel = channel_login.lower()
        self._validate_source(source)

        if self._state[source].get(channel) == day:
            return False

        self._state[source][channel] = day
        self._prune_old_entries()
        self._save()
        return True

    def _validate_source(self, source: str):
        if source not in ("live", "vod"):
            raise ValueError(f"Unsupported source '{source}'. Expected 'live' or 'vod'.")

    def _ensure_loaded(self):
        if self._state is not None:
            return

        state = {"live": {}, "vod": {}}
        loaded = None

        try:
            if self.storage is not None:
                loaded = self.storage.download_json(self.storage_key)
            elif self.local_state_path.exists():
                with open(self.local_state_path, "r") as f:
                    loaded = json.load(f)
        except Exception as e:
            logger.warning("Failed to load daily collection state; starting fresh: %s", e)

        if isinstance(loaded, dict):
            for source in ("live", "vod"):
                section = loaded.get(source, {})
                if isinstance(section, dict):
                    state[source] = {str(k).lower(): str(v) for k, v in section.items()}

        self._state = state
        self._prune_old_entries()

    def _prune_old_entries(self):
        if self.retention_days <= 0:
            return

        cutoff = datetime.now(timezone.utc).date() - timedelta(days=self.retention_days)
        for source in ("live", "vod"):
            pruned = {}
            for channel, day_str in self._state[source].items():
                try:
                    day = datetime.fromisoformat(day_str).date()
                    if day >= cutoff:
                        pruned[channel] = day_str
                except ValueError:
                    continue
            self._state[source] = pruned

    def _save(self):
        try:
            if self.storage is not None:
                self.storage.upload_json(self.storage_key, self._state)
                return

            self.local_state_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.local_state_path, "w") as f:
                json.dump(self._state, f, indent=2)
        except Exception as e:
            logger.error("Failed to persist daily collection state: %s", e)


class DynamoDBCollectionState:
    """Race-free dedup state using DynamoDB for multi-worker collection.

    Each item uses a composite primary key ``pk`` of the form
    ``{source}#{channel}#{utc_day}`` (e.g. ``live#xqc#2026-03-10``).

    Items are auto-expired via DynamoDB TTL after ``ttl_days``.
    """

    def __init__(self, table_name: str, region: str = "us-east-1", ttl_days: int = 32, dynamodb_resource=None):
        if not HAS_BOTO3 and dynamodb_resource is None:
            raise ImportError("boto3 is required for DynamoDBCollectionState")
        if dynamodb_resource is not None:
            self._table = dynamodb_resource.Table(table_name)
        else:
            self._table = boto3.resource("dynamodb", region_name=region).Table(table_name)
        self.ttl_days = ttl_days

    @staticmethod
    def _make_pk(source: str, channel: str, utc_day: str) -> str:
        return f"{source}#{channel.lower()}#{utc_day}"

    @staticmethod
    def current_utc_day() -> str:
        return datetime.now(timezone.utc).date().isoformat()

    def has_collected(self, source: str, channel: str, utc_day: Optional[str] = None) -> bool:
        day = utc_day or self.current_utc_day()
        pk = self._make_pk(source, channel, day)
        try:
            resp = self._table.get_item(Key={"pk": pk}, ConsistentRead=True)
            return "Item" in resp
        except Exception as e:
            logger.error("DynamoDB get_item failed for %s: %s", pk, e)
            return False

    def mark_collected(self, source: str, channel: str, utc_day: Optional[str] = None) -> bool:
        """Conditionally write item. Returns True if newly marked, False if already existed."""
        day = utc_day or self.current_utc_day()
        pk = self._make_pk(source, channel, day)
        ttl_epoch = int(time.time()) + (self.ttl_days * 86400)
        try:
            self._table.put_item(
                Item={
                    "pk": pk,
                    "source": source,
                    "channel": channel.lower(),
                    "utc_day": day,
                    "ttl": ttl_epoch,
                },
                ConditionExpression="attribute_not_exists(pk)",
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            logger.error("DynamoDB put_item failed for %s: %s", pk, e)
            raise
