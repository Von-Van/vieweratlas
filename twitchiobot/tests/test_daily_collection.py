import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure src/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from daily_collection_state import DailyCollectionState
from get_viewers import ChatLogger
from storage import FileStorage
from vod_collector import VODCollector, get_recent_vods


def test_daily_collection_state_roundtrip(tmp_path):
    storage = FileStorage(base_dir=str(tmp_path / "logs"))

    state = DailyCollectionState(storage=storage)
    assert not state.has_collected("live", "example_channel")
    assert state.mark_collected("live", "example_channel")
    assert state.has_collected("live", "example_channel")

    # Ensure marker survives reload from storage.
    state_reloaded = DailyCollectionState(storage=storage)
    assert state_reloaded.has_collected("live", "example_channel")


def test_chatlogger_skips_second_live_collection_same_day(tmp_path):
    storage = FileStorage(base_dir=str(tmp_path / "logs"))
    bot = ChatLogger(token="oauth:test-token", channels=["samplechannel"], storage=storage)
    bot.chatters["samplechannel"] = {"alice"}

    stream_info = {
        "viewer_count": 1234,
        "game_name": "Test Game",
        "title": "Test Title",
        "started_at": "2026-01-01T00:00:00Z"
    }

    with patch.object(ChatLogger, "fetch_stream_info", return_value=stream_info):
        asyncio.run(bot.log_results())

    # Try collecting same channel again on same UTC day.
    bot.chatters["samplechannel"] = {"alice", "bob"}
    with patch.object(ChatLogger, "fetch_stream_info", return_value=stream_info):
        asyncio.run(bot.log_results())

    snapshot_files = storage.list_files(prefix="raw/snapshots", suffix=".json")
    assert len(snapshot_files) == 1
    assert bot.collection_stats["skipped"] == 1


def test_vod_discovery_enforces_one_per_channel_per_day(tmp_path):
    storage = FileStorage(base_dir=str(tmp_path / "logs"))
    collector = VODCollector(
        storage=storage,
        queue_file=str(tmp_path / "vod_queue.json"),
        raw_dir=str(tmp_path / "vod_raw"),
        max_age_hours=24
    )

    collector.daily_state.mark_collected("vod", "already_done")

    discovered = [
        ("111", "already_done", "2026-02-05T01:00:00+00:00"),
        ("222", "new_channel", "2026-02-05T02:00:00+00:00"),
        ("333", "new_channel", "2026-02-05T03:00:00+00:00"),
        ("444", "other_channel", "2026-02-05T04:00:00+00:00"),
    ]

    with patch("vod_collector.get_recent_vods_batch", return_value=discovered):
        collector.add_vods_for_channels(["already_done", "new_channel", "other_channel"], vod_limit=5)

    queued_ids = {item["vod_id"] for item in collector.queue.queue}
    assert queued_ids == {"222", "444"}


def test_vod_processing_skips_when_channel_already_collected_today(tmp_path):
    storage = FileStorage(base_dir=str(tmp_path / "logs"))
    collector = VODCollector(
        storage=storage,
        queue_file=str(tmp_path / "vod_queue.json"),
        raw_dir=str(tmp_path / "vod_raw"),
        max_age_hours=24
    )

    collector.queue.add_vod("555", "skip_channel", vod_created_at="2026-02-05T01:00:00+00:00")
    collector.daily_state.mark_collected("vod", "skip_channel")

    with patch.object(collector.downloader, "download_vod_chat", return_value=True) as mock_download:
        result = collector.process_next_vod()

    assert result is True
    assert mock_download.call_count == 0
    status = next(item["status"] for item in collector.queue.queue if item["vod_id"] == "555")
    assert status == "completed"


def test_get_recent_vods_filters_to_last_24_hours(monkeypatch):
    monkeypatch.setenv("TWITCH_CLIENT_ID", "client-id")
    monkeypatch.setenv("TWITCH_OAUTH_TOKEN", "oauth-token")

    now = datetime.now(timezone.utc)
    in_window = (now - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    out_window = (now - timedelta(hours=30)).isoformat().replace("+00:00", "Z")

    user_resp = MagicMock()
    user_resp.raise_for_status.return_value = None
    user_resp.json.return_value = {"data": [{"id": "user-1"}]}

    videos_resp = MagicMock()
    videos_resp.raise_for_status.return_value = None
    videos_resp.json.return_value = {
        "data": [
            {"id": "recent-vod", "created_at": in_window, "view_count": 100},
            {"id": "old-vod", "created_at": out_window, "view_count": 100},
        ]
    }

    with patch("vod_collector.requests.get", side_effect=[user_resp, videos_resp]):
        vods = get_recent_vods("samplechannel", limit=5, max_age_hours=24, min_views=0)

    assert len(vods) == 1
    assert vods[0][0] == "recent-vod"
