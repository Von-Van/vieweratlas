import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from data_aggregator import DataAggregator
from graph_builder import GraphBuilder


@pytest.fixture
def tmp_logs_dir(tmp_path):
    """Create a temporary logs directory with sample snapshot JSON files."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()

    # Channel A: fps game, English, 5 chatters
    snapshot_a = {
        "channel": "streamer_a",
        "timestamp": "2025-01-01T12:00:00",
        "viewer_count": 5000,
        "game_name": "Valorant",
        "title": "Ranked grind",
        "started_at": "2025-01-01T10:00:00",
        "chatters": ["alice", "bob", "carol", "dave", "eve"],
        "language": "en",
    }

    # Channel B: fps game, English, overlaps 3 viewers with A
    snapshot_b = {
        "channel": "streamer_b",
        "timestamp": "2025-01-01T12:00:00",
        "viewer_count": 3000,
        "game_name": "Valorant",
        "title": "Playing with subs",
        "started_at": "2025-01-01T11:00:00",
        "chatters": ["alice", "bob", "carol", "frank", "grace"],
        "language": "en",
    }

    # Channel C: moba game, Spanish, overlaps 1 viewer with A and 0 with B
    snapshot_c = {
        "channel": "streamer_c",
        "timestamp": "2025-01-01T12:00:00",
        "viewer_count": 2000,
        "game_name": "League of Legends",
        "title": "Ranked LoL",
        "started_at": "2025-01-01T09:00:00",
        "chatters": ["dave", "hank", "iris", "jose"],
        "language": "es",
    }

    # Channel D: fps game, English, overlaps 2 viewers with A and 2 with B
    snapshot_d = {
        "channel": "streamer_d",
        "timestamp": "2025-01-01T12:00:00",
        "viewer_count": 8000,
        "game_name": "Valorant",
        "title": "Pro scrims",
        "started_at": "2025-01-01T10:30:00",
        "chatters": ["alice", "eve", "frank", "grace", "kate", "leo"],
        "language": "en",
    }

    # Channel E: isolated, no overlaps with anyone
    snapshot_e = {
        "channel": "streamer_e",
        "timestamp": "2025-01-01T12:00:00",
        "viewer_count": 100,
        "game_name": "Art",
        "title": "Drawing stream",
        "started_at": "2025-01-01T08:00:00",
        "chatters": ["zara", "yolanda"],
        "language": "en",
    }

    for i, snap in enumerate([snapshot_a, snapshot_b, snapshot_c, snapshot_d, snapshot_e]):
        filepath = logs_dir / f"snapshot_{i:03d}.json"
        with open(filepath, "w") as f:
            json.dump(snap, f)

    return logs_dir


@pytest.fixture
def aggregator(tmp_logs_dir):
    """Return a DataAggregator loaded with fixture data, bypassing storage backend."""
    agg = DataAggregator(str(tmp_logs_dir))
    # Force local filesystem path (bypass S3/storage auto-detection)
    agg.storage = None
    agg.load_all()
    return agg


@pytest.fixture
def channel_viewers(aggregator):
    return aggregator.get_channel_viewers()


@pytest.fixture
def channel_metadata(aggregator):
    return aggregator.get_channel_metadata()


@pytest.fixture
def graph(channel_viewers, channel_metadata):
    """Build an overlap graph with threshold=1 from fixture data."""
    builder = GraphBuilder(overlap_threshold=1)
    return builder.build_graph(channel_viewers, channel_metadata)
