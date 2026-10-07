"""In-memory storage and v2 survey builders shared by the analysis tests."""

import json
from io import BytesIO

import pandas as pd


class InMemoryStorage:
    """Storage backend holding live/VOD JSON snapshots and Parquet in memory."""

    def __init__(self, live_snapshots=None, vod_snapshots=None, parquet_data=None):
        self._live = live_snapshots or []
        self._vod_json = vod_snapshots or []
        self._parquet = parquet_data or {}  # key -> bytes
        self._json_uploads = {}

    def list_files(self, prefix="", suffix=""):
        if prefix.startswith("raw/snapshots") and suffix == ".json":
            return [f"raw/snapshots/snap_{i}.json" for i in range(len(self._live))]
        if prefix.startswith("raw/snapshots") and suffix == ".parquet":
            return [k for k in self._parquet.keys() if k.startswith("raw/snapshots") and k.endswith(".parquet")]
        if prefix.startswith("curated/presence_snapshots/source=vod") and suffix == ".parquet":
            return []
        if prefix.startswith("curated/presence_snapshots/source=vod") and suffix == ".json":
            return [f"curated/presence_snapshots/source=vod/snap_{i}.json" for i in range(len(self._vod_json))]
        return []

    def download_json(self, key):
        if key in self._json_uploads:
            return self._json_uploads[key]
        if key.startswith("raw/snapshots/snap_"):
            idx = int(key.split("_")[-1].replace(".json", ""))
            return self._live[idx]
        if key.startswith("curated/presence_snapshots/source=vod/snap_"):
            idx = int(key.split("_")[-1].replace(".json", ""))
            return self._vod_json[idx]
        return None

    def upload_json(self, key, data, **kwargs):
        self._json_uploads[key] = data
        return True

    def upload_parquet(self, key, data, **kwargs):
        self._parquet[key] = data
        return True

    def download_parquet(self, key):
        return self._parquet.get(key)

    def get_uri(self, key):
        return f"mock://{key}"


def parquet_bytes(rows):
    output = BytesIO()
    pd.DataFrame(rows).to_parquet(output, index=False, engine="pyarrow")
    return output.getvalue()


def _v2_row(channel, chatters, **extra):
    return {
        "schema_version": 2,
        **extra,
        "channel": channel,
        "collection_status": "completed",
        "chatters_json": json.dumps(chatters),
        "chatter_ids_json": json.dumps([str(i) for i in range(len(chatters))]),
    }


def v2_batch_bytes(channel, chatters):
    """One v2 batch file holding a single channel."""
    return parquet_bytes([_v2_row(channel, chatters)])


def survey_storage(days):
    """Storage holding one completed v2 survey per given date string."""
    storage = InMemoryStorage()
    for day in days:
        prefix = f"raw/snapshots/v2/date={day}/session=s{day}"
        storage._parquet[f"{prefix}/batch=01.parquet"] = v2_batch_bytes(f"ch_{day}", ["alice"])
        storage._json_uploads[f"{prefix}/manifest.json"] = {"status": "complete"}
    return storage


def survey_window_storage(channels):
    """Storage holding one completed survey whose single batch is ``channels``,
    every channel observed in the same survey window."""
    prefix = "raw/snapshots/v2/date=2026-08-12/session=s1"
    rows = [
        _v2_row(channel, chatters, survey_session_id="s1", batch=0)
        for channel, chatters in channels.items()
    ]
    storage = InMemoryStorage(parquet_data={f"{prefix}/batch=00.parquet": parquet_bytes(rows)})
    storage._json_uploads[f"{prefix}/manifest.json"] = {"status": "complete"}
    return storage
