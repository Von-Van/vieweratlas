"""
Test Suite for ViewerAtlas Pipeline

Tests for:
- DataAggregator: snapshot ingestion, viewer set building, statistics
- GraphBuilder: overlap computation, thresholds, graph properties
- CommunityDetector: Louvain partitioning, modularity, community structure
- ClusterTagger: label generation from metadata
- Integration: full pipeline from fixture data through visualization
"""

import json
import os
import sys
import tempfile
import shutil
import asyncio
from datetime import date, timedelta
from pathlib import Path
from collections import defaultdict

import pytest
import logging
import networkx as nx
from unittest.mock import patch, MagicMock

# Ensure src/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from data_aggregator import DataAggregator, survey_date_span
from graph_builder import GraphBuilder
from community_detector import CommunityDetector, LOUVAIN_AVAILABLE
from cluster_tagger import ClusterTagger
from config import (
    CollectionConfig,
    AnalysisConfig,
    VODConfig,
    PipelineConfig,
    get_default_config,
    get_rigorous_config,
    get_exploratory_config,
    get_debug_config,
    load_config_from_yaml,
)
from frontend_exporter import (
    FrontendExportConfig,
    export_frontend_data,
    export_pending_frontend_data,
)
from storage import S3Storage


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════


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


@pytest.fixture
def partition(graph):
    """Run community detection on the fixture graph."""
    if not LOUVAIN_AVAILABLE:
        pytest.skip("python-louvain not installed")
    detector = CommunityDetector(resolution=1.0)
    detector.detect_communities(graph)
    return detector.get_partition()


@pytest.fixture
def communities(graph):
    """Get communities dict from fixture graph."""
    if not LOUVAIN_AVAILABLE:
        pytest.skip("python-louvain not installed")
    detector = CommunityDetector(resolution=1.0)
    detector.detect_communities(graph)
    return detector.get_communities()


# ═══════════════════════════════════════════════════════════════════════════════
# DataAggregator Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestDataAggregator:
    """Tests for data loading, viewer set building, and statistics."""

    def test_load_json_snapshots_count(self, tmp_logs_dir):
        agg = DataAggregator(str(tmp_logs_dir))
        agg.storage = None  # Force local filesystem
        json_count = agg.load_json_snapshots()
        assert json_count == 5, f"Expected 5 snapshots loaded, got {json_count}"

    def test_channel_viewers_keys(self, aggregator):
        viewers = aggregator.get_channel_viewers()
        expected_channels = {"streamer_a", "streamer_b", "streamer_c", "streamer_d", "streamer_e"}
        assert set(viewers.keys()) == expected_channels

    def test_channel_viewers_sets(self, aggregator):
        viewers = aggregator.get_channel_viewers()
        assert viewers["streamer_a"] == {"alice", "bob", "carol", "dave", "eve"}
        assert viewers["streamer_b"] == {"alice", "bob", "carol", "frank", "grace"}
        assert viewers["streamer_c"] == {"dave", "hank", "iris", "jose"}

    def test_unique_viewers_across_all(self, aggregator):
        stats = aggregator.get_statistics()
        # All unique: alice bob carol dave eve frank grace hank iris jose kate leo zara yolanda = 14
        assert stats["total_unique_viewers_across_all"] == 14

    def test_channel_metadata_game(self, aggregator):
        meta = aggregator.get_channel_metadata()
        assert meta["streamer_a"]["game_name"] == "Valorant"
        assert meta["streamer_c"]["game_name"] == "League of Legends"

    def test_filter_channels_by_size(self, aggregator):
        filtered = aggregator.filter_channels_by_size(min_viewers=4)
        # streamer_a=5, streamer_b=5, streamer_c=4, streamer_d=6 pass; streamer_e=2 fails
        assert "streamer_e" not in filtered
        assert "streamer_a" in filtered
        assert "streamer_d" in filtered

    def test_user_channel_map(self, aggregator):
        ucm = aggregator.get_user_channel_map()
        # alice appears in streamer_a, streamer_b, streamer_d
        assert ucm["alice"] == {"streamer_a", "streamer_b", "streamer_d"}
        # zara only in streamer_e
        assert ucm["zara"] == {"streamer_e"}

    def test_filter_by_repeat_viewers(self, aggregator):
        filtered = aggregator.filter_by_repeat_viewers(min_appearances=2)
        # streamer_e has only zara+yolanda who each appear in 1 channel => excluded
        assert "streamer_e" not in filtered
        # streamer_a should still be present (alice, bob, carol, dave, eve — most appear in 2+ channels)
        assert "streamer_a" in filtered

    def test_data_quality_report(self, aggregator):
        report = aggregator.get_data_quality_report()
        assert report["total_channels"] == 5
        assert report["total_unique_viewers"] == 14
        assert report["total_snapshots"] == 5
        assert report["one_off_viewers"] >= 0  # At least some one-off viewers

    def test_load_empty_directory(self, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        agg = DataAggregator(str(empty_dir))
        agg.storage = None  # Force local filesystem
        json_count, csv_count, vod_count, parquet_count = agg.load_all()
        assert json_count == 0
        assert csv_count == 0
        assert agg.get_channel_viewers() == {}

    def test_load_all_returns_tuple(self, tmp_logs_dir):
        agg = DataAggregator(str(tmp_logs_dir))
        agg.storage = None  # Force local filesystem
        result = agg.load_all()
        assert isinstance(result, tuple)
        assert len(result) == 4

    def test_csv_username_column_loads_correctly(self, tmp_path):
        """Regression: CSV written with 'username' header must be read correctly."""
        csv_dir = tmp_path / "csv_logs"
        csv_dir.mkdir()
        csv_file = csv_dir / "test_channel_20250101_120000.csv"
        csv_file.write_text(
            "timestamp,channel,viewer_count,game_name,title,started_at,username\n"
            "2025-01-01T12:00:00,test_ch,1000,Valorant,Test,2025-01-01T10:00:00,alice\n"
            "2025-01-01T12:00:00,test_ch,1000,Valorant,Test,2025-01-01T10:00:00,bob\n"
        )
        agg = DataAggregator(str(csv_dir))
        agg.storage = None
        csv_count = agg.load_csv_snapshots()
        assert csv_count == 2
        viewers = agg.get_channel_viewers()
        assert "test_ch" in viewers
        assert viewers["test_ch"] == {"alice", "bob"}


# ═══════════════════════════════════════════════════════════════════════════════
# GraphBuilder Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestGraphBuilder:
    """Tests for overlap graph construction and properties."""

    def test_graph_has_all_nodes(self, graph):
        assert graph.number_of_nodes() == 5

    def test_graph_edge_weights(self, channel_viewers):
        builder = GraphBuilder(overlap_threshold=1)
        g = builder.build_graph(channel_viewers)

        # streamer_a & streamer_b share alice, bob, carol => weight 3
        assert g.has_edge("streamer_a", "streamer_b")
        assert g["streamer_a"]["streamer_b"]["weight"] == 3

        # streamer_a & streamer_c share dave => weight 1
        assert g.has_edge("streamer_a", "streamer_c")
        assert g["streamer_a"]["streamer_c"]["weight"] == 1

        # streamer_b & streamer_c share nobody => no edge
        assert not g.has_edge("streamer_b", "streamer_c")

    def test_graph_threshold_filters_edges(self, channel_viewers):
        builder = GraphBuilder(overlap_threshold=2)
        g = builder.build_graph(channel_viewers)

        # streamer_a & streamer_c overlap=1, below threshold => no edge
        assert not g.has_edge("streamer_a", "streamer_c")
        # streamer_a & streamer_b overlap=3, above threshold => edge exists
        assert g.has_edge("streamer_a", "streamer_b")

    def test_apply_threshold_removes_edges(self, channel_viewers):
        builder = GraphBuilder(overlap_threshold=1)
        g = builder.build_graph(channel_viewers)
        original_edges = g.number_of_edges()

        builder.apply_threshold(3)
        assert g.number_of_edges() < original_edges

    def test_isolated_node(self, channel_viewers):
        builder = GraphBuilder(overlap_threshold=1)
        g = builder.build_graph(channel_viewers)
        # streamer_e has no shared viewers with anyone
        assert g.degree("streamer_e") == 0

    def test_statistics(self, channel_viewers):
        builder = GraphBuilder(overlap_threshold=1)
        builder.build_graph(channel_viewers)
        stats = builder.get_statistics()

        assert stats["num_nodes"] == 5
        assert stats["num_edges"] > 0
        assert stats["avg_edge_weight"] > 0
        assert stats["max_edge_weight"] >= stats["avg_edge_weight"]
        assert 0 <= stats["density"] <= 1

    def test_largest_component(self, channel_viewers):
        builder = GraphBuilder(overlap_threshold=1)
        builder.build_graph(channel_viewers)
        lc = builder.get_largest_component()
        # The largest component should contain connected channels, not the isolated streamer_e
        assert "streamer_e" not in lc.nodes()
        assert "streamer_a" in lc.nodes()

    def test_export_csvs(self, channel_viewers, tmp_path):
        builder = GraphBuilder(overlap_threshold=1)
        builder.build_graph(channel_viewers)

        nodes_csv = str(tmp_path / "nodes.csv")
        edges_csv = str(tmp_path / "edges.csv")
        builder.export_nodes_csv(nodes_csv)
        builder.export_edges_csv(edges_csv)

        assert os.path.exists(nodes_csv)
        assert os.path.exists(edges_csv)

        with open(edges_csv) as f:
            lines = f.readlines()
        # Header + at least one edge
        assert len(lines) >= 2

    def test_export_nodes_csv_has_correct_game_name(self, channel_viewers, channel_metadata, tmp_path):
        """Regression: export_nodes_csv must use game_name attribute, not 'game'."""
        builder = GraphBuilder(overlap_threshold=1)
        builder.build_graph(channel_viewers, channel_metadata)

        nodes_csv = str(tmp_path / "nodes.csv")
        builder.export_nodes_csv(nodes_csv)

        with open(nodes_csv) as f:
            lines = f.readlines()

        # Find streamer_a row and verify game is Valorant, not Unknown
        found = False
        for line in lines[1:]:  # skip header
            parts = line.strip().split(",")
            if parts[0] == "streamer_a":
                assert parts[3] == "Valorant", f"Expected Valorant, got {parts[3]}"
                found = True
                break
        assert found, "streamer_a not found in nodes CSV"

    def test_empty_graph(self):
        builder = GraphBuilder(overlap_threshold=1)
        g = builder.build_graph({})
        assert g.number_of_nodes() == 0
        assert g.number_of_edges() == 0

    def test_get_channel_neighbors(self, channel_viewers):
        builder = GraphBuilder(overlap_threshold=1)
        builder.build_graph(channel_viewers)
        neighbors = builder.get_channel_neighbors("streamer_a")
        # Should be sorted by weight descending
        weights = [w for _, w in neighbors]
        assert weights == sorted(weights, reverse=True)

    def test_no_self_loops(self, channel_viewers):
        builder = GraphBuilder(overlap_threshold=1)
        g = builder.build_graph(channel_viewers)
        for node in g.nodes():
            assert not g.has_edge(node, node)

    def test_high_degree_viewer_is_skipped(self):
        channel_viewers = {
            f"channel_{i}": {"shared_noise", f"user_{i}"}
            for i in range(300)
        }
        builder = GraphBuilder(overlap_threshold=1, max_viewer_channel_degree=100)
        g = builder.build_graph(channel_viewers)
        assert g.number_of_nodes() == 300
        assert g.number_of_edges() == 0
        assert builder.get_statistics()["skipped_high_degree_viewers"] == 1

    def test_inverted_index_handles_5000_channel_fixture(self):
        channel_viewers = {
            f"channel_{i}": {f"user_{i}", f"user_{i + 1}"}
            for i in range(5000)
        }
        builder = GraphBuilder(overlap_threshold=1)
        g = builder.build_graph(channel_viewers)
        assert g.number_of_nodes() == 5000
        assert g.number_of_edges() == 4999
        assert g["channel_0"]["channel_1"]["weight"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# CommunityDetector Tests
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.skipif(not LOUVAIN_AVAILABLE, reason="python-louvain not installed")
class TestCommunityDetector:
    """Tests for Louvain community detection."""

    def test_every_node_assigned(self, graph):
        detector = CommunityDetector(resolution=1.0)
        partition = detector.detect_communities(graph)
        for node in graph.nodes():
            assert node in partition

    def test_partition_values_are_ints(self, graph):
        detector = CommunityDetector(resolution=1.0)
        partition = detector.detect_communities(graph)
        for comm_id in partition.values():
            assert isinstance(comm_id, int)

    def test_communities_cover_all_nodes(self, graph):
        detector = CommunityDetector(resolution=1.0)
        detector.detect_communities(graph)
        communities = detector.get_communities()

        all_nodes = set()
        for channels in communities.values():
            all_nodes.update(channels)
        assert all_nodes == set(graph.nodes())

    def test_communities_are_disjoint(self, graph):
        detector = CommunityDetector(resolution=1.0)
        detector.detect_communities(graph)
        communities = detector.get_communities()

        seen = set()
        for channels in communities.values():
            overlap = seen & channels
            assert len(overlap) == 0, f"Communities overlap on: {overlap}"
            seen.update(channels)

    def test_modularity_non_negative(self, graph):
        detector = CommunityDetector(resolution=1.0)
        detector.detect_communities(graph)
        assert detector.get_modularity() >= 0

    def test_min_community_size_default_keeps_everything(self):
        """Default of 1 must be a pure no-op."""
        g = nx.Graph()
        g.add_edge("a", "b", weight=5)
        g.add_edge("b", "c", weight=5)
        g.add_edge("lonely1", "lonely2", weight=1)

        partition = CommunityDetector(resolution=1.0).detect_communities(g)
        assert set(partition) == set(g.nodes())

    def test_min_community_size_discards_small_communities(self):
        # Two tight triangles joined weakly to an isolated pair.
        g = nx.Graph()
        for u, v in [("a1", "a2"), ("a2", "a3"), ("a1", "a3")]:
            g.add_edge(u, v, weight=50)
        for u, v in [("b1", "b2"), ("b2", "b3"), ("b1", "b3")]:
            g.add_edge(u, v, weight=50)
        g.add_edge("a1", "b1", weight=1)
        g.add_edge("solo1", "solo2", weight=40)
        g.add_edge("solo1", "a1", weight=1)

        detector = CommunityDetector(resolution=1.0, min_community_size=3)
        partition = detector.detect_communities(g)

        # The 2-channel community is gone; every survivor is in a >=3 community.
        assert "solo1" not in partition
        assert "solo2" not in partition
        assert detector.discarded_channels == {"solo1", "solo2"}
        for members in detector.get_communities().values():
            assert len(members) >= 3
        # Partition and communities stay in agreement.
        from_communities = set()
        for members in detector.get_communities().values():
            from_communities.update(members)
        assert from_communities == set(partition)

    def test_min_community_size_recomputes_modularity_on_survivors(self):
        g = nx.Graph()
        for u, v in [("a1", "a2"), ("a2", "a3"), ("a1", "a3")]:
            g.add_edge(u, v, weight=50)
        g.add_edge("solo1", "solo2", weight=40)

        detector = CommunityDetector(resolution=1.0, min_community_size=3)
        detector.detect_communities(g)
        # Would raise inside python-louvain if scored against the full graph.
        assert detector.get_modularity() >= 0

    def test_min_community_size_can_empty_the_partition(self):
        g = nx.Graph()
        g.add_edge("a", "b", weight=5)

        detector = CommunityDetector(resolution=1.0, min_community_size=10)
        partition = detector.detect_communities(g)
        assert partition == {}
        assert detector.get_modularity() == 0.0

    def test_min_community_size_below_one_rejected(self):
        with pytest.raises(ValueError):
            CommunityDetector(min_community_size=0)

    def test_statistics(self, graph):
        detector = CommunityDetector(resolution=1.0)
        detector.detect_communities(graph)
        stats = detector.get_statistics()

        assert stats["num_communities"] >= 1
        assert stats["largest_community_size"] >= 1
        assert stats["smallest_community_size"] >= 1
        assert stats["largest_community_size"] >= stats["smallest_community_size"]

    def test_community_for_channel(self, graph):
        detector = CommunityDetector(resolution=1.0)
        detector.detect_communities(graph)
        comm = detector.get_community_for_channel("streamer_a")
        assert isinstance(comm, int)
        assert comm >= 0

    def test_unknown_channel_returns_minus_one(self, graph):
        detector = CommunityDetector(resolution=1.0)
        detector.detect_communities(graph)
        assert detector.get_community_for_channel("nonexistent_channel") == -1

    def test_resolution_changes_communities(self, graph):
        # High resolution should produce at least as many communities as low
        det_low = CommunityDetector(resolution=0.5)
        det_low.detect_communities(graph)
        n_low = len(det_low.get_communities())

        det_high = CommunityDetector(resolution=3.0)
        det_high.detect_communities(graph)
        n_high = len(det_high.get_communities())

        # Not strictly guaranteed but very likely with reasonable data
        assert n_high >= n_low

    def test_add_community_attribute(self, graph):
        detector = CommunityDetector(resolution=1.0)
        detector.detect_communities(graph)
        detector.add_community_attribute_to_graph(graph)

        for node in graph.nodes():
            assert "community" in graph.nodes[node]

    def test_empty_graph(self):
        detector = CommunityDetector(resolution=1.0)
        partition = detector.detect_communities(nx.Graph())
        assert partition == {}

    def test_set_resolution(self):
        detector = CommunityDetector(resolution=1.0)
        detector.set_resolution(2.5)
        assert detector.resolution == 2.5


# ═══════════════════════════════════════════════════════════════════════════════
# ClusterTagger Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestClusterTagger:
    """Tests for community label generation."""

    def test_dominant_game_label(self):
        """Community where 80% play the same game should get that game as label."""
        communities = {0: {"ch1", "ch2", "ch3", "ch4", "ch5"}}
        metadata = {
            "ch1": {"game_name": "Valorant", "viewer_count": 100},
            "ch2": {"game_name": "Valorant", "viewer_count": 200},
            "ch3": {"game_name": "Valorant", "viewer_count": 150},
            "ch4": {"game_name": "Valorant", "viewer_count": 300},
            "ch5": {"game_name": "CS2", "viewer_count": 50},
        }
        tagger = ClusterTagger()
        labels = tagger.tag_communities(communities, metadata)
        assert "Valorant" in labels[0]

    def test_language_game_combo_label(self):
        """Community with clear language + game combo."""
        communities = {0: {"ch1", "ch2", "ch3", "ch4", "ch5"}}
        metadata = {
            "ch1": {"game_name": "Minecraft", "language": "es", "viewer_count": 100},
            "ch2": {"game_name": "Fortnite", "language": "es", "viewer_count": 200},
            "ch3": {"game_name": "Minecraft", "language": "es", "viewer_count": 150},
            "ch4": {"game_name": "Roblox", "language": "en", "viewer_count": 300},
            "ch5": {"game_name": "Minecraft", "language": "es", "viewer_count": 50},
        }
        tagger = ClusterTagger()
        labels = tagger.tag_communities(communities, metadata)
        label = labels[0]
        # Should reference game and/or language
        assert "Minecraft" in label or "es" in label

    def test_mixed_games_label(self):
        """Community with no dominant game should get a mixed label."""
        communities = {0: {"ch1", "ch2", "ch3"}}
        metadata = {
            "ch1": {"game_name": "Valorant", "viewer_count": 100},
            "ch2": {"game_name": "Fortnite", "viewer_count": 200},
            "ch3": {"game_name": "Minecraft", "viewer_count": 150},
        }
        tagger = ClusterTagger()
        labels = tagger.tag_communities(communities, metadata)
        label = labels[0]
        # Should contain "Mix" or multiple game names
        assert "Mix" in label or "/" in label

    def test_all_communities_get_labels(self):
        communities = {
            0: {"ch1", "ch2"},
            1: {"ch3", "ch4"},
            2: {"ch5"},
        }
        metadata = {
            "ch1": {"game_name": "Valorant", "viewer_count": 100},
            "ch2": {"game_name": "Valorant", "viewer_count": 200},
            "ch3": {"game_name": "LoL", "viewer_count": 300},
            "ch4": {"game_name": "LoL", "viewer_count": 400},
            "ch5": {"game_name": "Art", "viewer_count": 50},
        }
        tagger = ClusterTagger()
        labels = tagger.tag_communities(communities, metadata)
        assert len(labels) == 3
        assert 0 in labels
        assert 1 in labels
        assert 2 in labels

    def test_empty_metadata_fallback(self):
        """Channels with no metadata should still get a label."""
        communities = {0: {"ch1", "ch2"}}
        metadata = {}
        tagger = ClusterTagger()
        labels = tagger.tag_communities(communities, metadata)
        assert 0 in labels
        assert len(labels[0]) > 0

    def test_statistics(self):
        communities = {
            0: {"ch1", "ch2", "ch3"},
            1: {"ch4", "ch5"},
        }
        metadata = {
            "ch1": {"game_name": "Valorant", "viewer_count": 100},
            "ch2": {"game_name": "Valorant", "viewer_count": 200},
            "ch3": {"game_name": "Valorant", "viewer_count": 150},
            "ch4": {"game_name": "Art", "viewer_count": 50},
            "ch5": {"game_name": "Music", "viewer_count": 50},
        }
        tagger = ClusterTagger()
        tagger.tag_communities(communities, metadata)
        stats = tagger.get_statistics()

        assert stats["total_labeled"] == 2
        assert stats["with_clear_game"] >= 1  # The Valorant community

    def test_get_label_reasoning(self):
        communities = {0: {"ch1", "ch2"}}
        metadata = {
            "ch1": {"game_name": "Valorant", "viewer_count": 100},
            "ch2": {"game_name": "Valorant", "viewer_count": 200},
        }
        tagger = ClusterTagger()
        tagger.tag_communities(communities, metadata)
        reasoning = tagger.get_label_reasoning(0)
        assert "reasoning" in reasoning


# ═══════════════════════════════════════════════════════════════════════════════
# Config Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestConfig:
    """Tests for configuration loading and validation."""

    def test_default_config_valid(self):
        config = get_default_config()
        assert config.analysis.overlap_threshold == 1
        assert config.analysis.resolution == 1.0

    def test_rigorous_config_is_calibrated_for_eventsub_surveys(self):
        """Production analysis preset, measured rather than inherited.

        The old TwitchAtlas threshold of 300 produced a graph with zero edges on
        real five-minute EventSub survey data, so these values come from
        scripts/sweep_threshold.py. They are expected to rise as data
        accumulates; this test exists so a change is deliberate, not accidental.
        """
        analysis = get_rigorous_config().analysis
        assert analysis.overlap_threshold == 2
        assert analysis.min_community_size == 10
        assert analysis.min_channel_observations == 3
        assert analysis.min_channel_viewers == 10
        assert analysis.weighting_mode == "shared_count"
        assert analysis.include_isolated_nodes is False

    def test_rigorous_config_bounds_the_analysis_window(self):
        """Without a window the union grows daily and thresholds drift."""
        assert get_rigorous_config().analysis.analysis_window_days == 30

    def test_exploratory_config_has_high_resolution(self):
        config = get_exploratory_config()
        assert config.analysis.resolution == 2.0

    def test_debug_config_small_dataset(self):
        config = get_debug_config()
        assert config.collection.top_channels_limit == 100

    def test_collection_config_validation(self):
        with pytest.raises(ValueError):
            CollectionConfig(batch_size=0)
        with pytest.raises(ValueError):
            CollectionConfig(duration_per_batch=-1)

    def test_collection_config_survey_env_overrides(self, monkeypatch):
        monkeypatch.setenv("SURVEY_TOP_CHANNELS_LIMIT", "7")
        monkeypatch.setenv("SURVEY_BATCH_SIZE", "4")
        monkeypatch.setenv("SURVEY_WINDOW_SECONDS", "12")
        monkeypatch.setenv("SURVEY_TIMEOUT_SECONDS", "90")

        config = CollectionConfig()
        assert config.top_channels_limit == 7
        assert config.batch_size == 4
        assert config.duration_per_batch == 12
        assert config.survey_timeout_seconds == 90

    def test_collection_config_rejects_invalid_survey_env_override(self, monkeypatch):
        monkeypatch.setenv("SURVEY_BATCH_SIZE", "101")
        with pytest.raises(ValueError, match="100-room"):
            CollectionConfig()

    def test_analysis_config_validation(self):
        with pytest.raises(ValueError):
            AnalysisConfig(overlap_threshold=-1)
        with pytest.raises(ValueError):
            AnalysisConfig(resolution=0)
        with pytest.raises(ValueError):
            AnalysisConfig(min_community_size=0)

    def test_pipeline_config_s3_requires_bucket(self):
        with pytest.raises(ValueError):
            PipelineConfig(storage_type="s3", s3_bucket=None)

    def test_yaml_loading(self, tmp_path):
        yaml_file = tmp_path / "test_config.yaml"
        yaml_file.write_text(
            "collection:\n"
            "  logs_dir: logs\n"
            "  batch_size: 50\n"
            "  top_channels_limit: 200\n"
            "  collection_interval_minutes: 30\n"
            "  wait_for_hour_alignment: false\n"
            "  max_runtime_hours: 8\n"
            "  max_collection_cycles: 12\n"
            "analysis:\n"
            "  overlap_threshold: 5\n"
            "  resolution: 1.5\n"
            "  min_community_size: 3\n"
            "vod:\n"
            "  persist_raw_chat: true\n"
            "  max_vods_per_run: 7\n"
            "  max_processing_hours: 2\n"
            "  rate_limit_delay_s: 3\n"
        )
        config = load_config_from_yaml(str(yaml_file))
        assert config.collection.batch_size == 50
        assert config.collection.top_channels_limit == 200
        assert config.collection.collection_interval_minutes == 30
        assert config.collection.wait_for_hour_alignment is False
        assert config.collection.max_runtime_hours == 8
        assert config.collection.max_collection_cycles == 12
        assert config.analysis.overlap_threshold == 5
        assert config.analysis.resolution == 1.5
        assert config.analysis.min_community_size == 3
        assert config.vod.persist_raw_chat is True
        assert config.vod.max_vods_per_run == 7
        assert config.vod.max_processing_hours == 2
        assert config.vod.rate_limit_delay_s == 3

    def test_yaml_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_config_from_yaml(str(tmp_path / "nonexistent.yaml"))

    def test_default_weighting_mode_is_shared_count(self):
        config = get_default_config()
        assert config.analysis.weighting_mode == "shared_count"

    def test_frontend_export_defaults(self):
        config = get_default_config()
        assert config.analysis.frontend_max_channels == 1000
        assert config.analysis.frontend_max_edges == 25000
        assert config.analysis.frontend_top_edges_per_channel == 25

    def test_invalid_frontend_export_limits_raise(self):
        with pytest.raises(ValueError):
            AnalysisConfig(frontend_max_channels=0)
        with pytest.raises(ValueError):
            AnalysisConfig(frontend_max_edges=0)
        with pytest.raises(ValueError):
            AnalysisConfig(frontend_top_edges_per_channel=0)

    def test_invalid_weighting_mode_raises(self):
        with pytest.raises(ValueError):
            AnalysisConfig(weighting_mode="cosine")

    @pytest.mark.parametrize("mode", ["shared_count", "jaccard", "overlap_coef"])
    def test_supported_weighting_modes_accepted(self, mode):
        assert AnalysisConfig(weighting_mode=mode).weighting_mode == mode

    def test_normalized_threshold_bounds(self):
        assert AnalysisConfig(normalized_overlap_threshold=0.5)
        for bad in (-0.1, 1.1):
            with pytest.raises(ValueError):
                AnalysisConfig(normalized_overlap_threshold=bad)

    def test_min_channel_observations_bounds(self):
        assert AnalysisConfig(min_channel_observations=3)
        with pytest.raises(ValueError):
            AnalysisConfig(min_channel_observations=0)

    def test_yaml_loads_every_analysis_field(self, tmp_path):
        """Fields the hand-enumerated loader used to drop silently."""
        yaml_file = tmp_path / "full_config.yaml"
        yaml_file.write_text(
            "analysis:\n"
            "  enable_static_viz: false\n"
            "  enable_interactive_viz: false\n"
            "  export_graph_csv: false\n"
            "  save_analysis_json: false\n"
            "  label_top_n_nodes: 30\n"
            "  static_viz_dpi: 150\n"
            "  include_isolated_nodes: false\n"
            "  min_user_appearances: 2\n"
            "  show_node_labels: false\n"
        )
        config = load_config_from_yaml(str(yaml_file))
        assert config.analysis.enable_static_viz is False
        assert config.analysis.enable_interactive_viz is False
        assert config.analysis.export_graph_csv is False
        assert config.analysis.save_analysis_json is False
        assert config.analysis.label_top_n_nodes == 30
        assert config.analysis.static_viz_dpi == 150
        assert config.analysis.include_isolated_nodes is False
        assert config.analysis.min_user_appearances == 2
        assert config.analysis.show_node_labels is False

    def test_yaml_figsize_list_becomes_tuple(self, tmp_path):
        yaml_file = tmp_path / "figsize_config.yaml"
        yaml_file.write_text("analysis:\n  static_viz_figsize: [12, 9]\n")
        config = load_config_from_yaml(str(yaml_file))
        assert config.analysis.static_viz_figsize == (12, 9)

    def test_yaml_unknown_section_key_raises(self, tmp_path):
        yaml_file = tmp_path / "typo_config.yaml"
        yaml_file.write_text("analysis:\n  min_comunity_size: 3\n")
        with pytest.raises(ValueError, match="min_comunity_size"):
            load_config_from_yaml(str(yaml_file))

    def test_yaml_unknown_top_level_key_raises(self, tmp_path):
        yaml_file = tmp_path / "typo_top_config.yaml"
        yaml_file.write_text("stroage_type: s3\n")
        with pytest.raises(ValueError, match="stroage_type"):
            load_config_from_yaml(str(yaml_file))

    def test_yaml_vod_max_age_days_fallback(self, tmp_path):
        yaml_file = tmp_path / "vod_config.yaml"
        yaml_file.write_text("vod:\n  max_age_days: 3\n")
        config = load_config_from_yaml(str(yaml_file))
        assert config.vod.max_age_days == 3
        assert config.vod.max_age_hours == 72

    def test_yaml_explicit_max_age_hours_wins(self, tmp_path):
        yaml_file = tmp_path / "vod_hours_config.yaml"
        yaml_file.write_text("vod:\n  max_age_days: 3\n  max_age_hours: 6\n")
        config = load_config_from_yaml(str(yaml_file))
        assert config.vod.max_age_hours == 6

    def test_shipped_config_yaml_loads(self):
        """The config.yaml shipped in the repo must satisfy the strict loader."""
        shipped = Path(__file__).resolve().parent.parent / "config" / "config.yaml"
        config = load_config_from_yaml(str(shipped))
        assert config.analysis.overlap_threshold == 10
        assert config.analysis.min_community_size == 3

    def test_yaml_loading_weighting_mode(self, tmp_path):
        yaml_file = tmp_path / "wm_config.yaml"
        yaml_file.write_text(
            "analysis:\n"
            "  overlap_threshold: 1\n"
            '  weighting_mode: "shared_count"\n'
            "  frontend_max_channels: 500\n"
            "  frontend_max_edges: 10000\n"
            "  frontend_top_edges_per_channel: 10\n"
        )
        config = load_config_from_yaml(str(yaml_file))
        assert config.analysis.weighting_mode == "shared_count"
        assert config.analysis.frontend_max_channels == 500
        assert config.analysis.frontend_max_edges == 10000
        assert config.analysis.frontend_top_edges_per_channel == 10


# ═══════════════════════════════════════════════════════════════════════════════
# Integration Test
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.skipif(not LOUVAIN_AVAILABLE, reason="python-louvain not installed")
class TestIntegration:
    """End-to-end test of the analysis pipeline with fixture data."""

    def test_full_pipeline(self, tmp_logs_dir, tmp_path):
        """
        Run the complete pipeline:
        aggregate → build graph → detect communities → tag → verify
        """
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        # 1. Aggregate
        aggregator = DataAggregator(str(tmp_logs_dir))
        aggregator.storage = None  # Force local filesystem
        json_count, csv_count, vod_count, parquet_count = aggregator.load_all()
        assert json_count == 5

        channel_viewers = aggregator.get_channel_viewers()
        channel_metadata = aggregator.get_channel_metadata()
        assert len(channel_viewers) == 5

        # 2. Build graph
        builder = GraphBuilder(overlap_threshold=1)
        graph = builder.build_graph(channel_viewers, channel_metadata)
        assert graph.number_of_nodes() == 5
        assert graph.number_of_edges() > 0

        # 3. Detect communities
        detector = CommunityDetector(resolution=1.0)
        partition = detector.detect_communities(graph)
        communities = detector.get_communities()
        assert len(partition) == 5
        assert len(communities) >= 1

        # 4. Tag communities (metadata keys are now normalized by DataAggregator)
        tagger = ClusterTagger()
        labels = tagger.tag_communities(communities, channel_metadata)
        assert len(labels) == len(communities)

        # 5. Verify structural properties
        # All Valorant streamers should likely cluster together
        valorant_channels = {"streamer_a", "streamer_b", "streamer_d"}
        valorant_comms = {partition[ch] for ch in valorant_channels}
        # They should be in at most 2 communities (ideally 1)
        assert len(valorant_comms) <= 2

        # 6. Export and verify files
        builder.export_nodes_csv(str(output_dir / "nodes.csv"))
        builder.export_edges_csv(str(output_dir / "edges.csv"))
        assert (output_dir / "nodes.csv").exists()
        assert (output_dir / "edges.csv").exists()

    def test_pipeline_with_threshold_filtering(self, tmp_logs_dir):
        """Test that raising the threshold reduces graph connectivity."""
        aggregator = DataAggregator(str(tmp_logs_dir))
        aggregator.storage = None  # Force local filesystem
        aggregator.load_all()
        channel_viewers = aggregator.get_channel_viewers()

        builder_low = GraphBuilder(overlap_threshold=1)
        g_low = builder_low.build_graph(channel_viewers)

        builder_high = GraphBuilder(overlap_threshold=3)
        g_high = builder_high.build_graph(channel_viewers)

        assert g_high.number_of_edges() <= g_low.number_of_edges()

    def test_pipeline_with_viewer_filtering(self, tmp_logs_dir):
        """Test that filtering by min viewers reduces channels."""
        aggregator = DataAggregator(str(tmp_logs_dir))
        aggregator.storage = None  # Force local filesystem
        aggregator.load_all()

        all_channels = aggregator.get_channel_viewers()
        filtered = aggregator.filter_channels_by_size(min_viewers=5)

        assert len(filtered) <= len(all_channels)
        # streamer_e has only 2 viewers, should be filtered out at min=5
        assert "streamer_e" not in filtered


# ═══════════════════════════════════════════════════════════════════════════════
# Frontend Export Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestFrontendExporter:
    """Tests for public frontend artifact shaping."""

    def test_export_caps_graph_and_adds_layout_without_private_fields(self):
        graph = nx.Graph()
        for idx, viewers in enumerate([5000, 4000, 3000, 2000, 1000]):
            node = f"ch_{idx}"
            graph.add_node(
                node,
                viewer_count=viewers,
                viewers=idx + 10,
                game_name="Valorant" if idx < 3 else "Minecraft",
                language="en",
                title="Test",
            )

        graph.add_edge("ch_0", "ch_1", weight=50)
        graph.add_edge("ch_0", "ch_2", weight=40)
        graph.add_edge("ch_1", "ch_2", weight=30)
        graph.add_edge("ch_2", "ch_3", weight=20)
        graph.add_edge("ch_3", "ch_4", weight=10)

        partition = {"ch_0": 0, "ch_1": 0, "ch_2": 0, "ch_3": 1, "ch_4": 1}
        communities = {0: {"ch_0", "ch_1", "ch_2"}, 1: {"ch_3", "ch_4"}}
        labels = {0: "FPS English", 1: "Cozy English"}
        storage = MockS3Storage()

        ok = export_frontend_data(
            graph=graph,
            partition=partition,
            communities=communities,
            labels=labels,
            detection_stats={"modularity": 0.42},
            aggregator_stats={
                "total_unique_viewers_across_all": 123,
                "total_snapshots": 5,
            },
            storage=storage,
            config=FrontendExportConfig(
                max_channels=3,
                max_edges=2,
                top_edges_per_channel=2,
            ),
        )

        assert ok is True
        payload = storage._json_uploads["data/frontend-data.json"]
        assert len(payload["channels"]) == 3
        assert len(payload["edges"]) <= 2
        assert payload["overallStats"]["totalChannels"] == 5
        assert payload["overallStats"]["renderedChannels"] == 3
        assert all("layout" in channel for channel in payload["channels"])
        assert "chatters" not in json.dumps(payload)
        assert "chatters_json" not in json.dumps(payload)

        channel_ids = {channel["id"] for channel in payload["channels"]}
        for edge in payload["edges"]:
            assert edge["source"] in channel_ids
            assert edge["target"] in channel_ids

    def test_export_makes_duplicate_community_labels_unique(self):
        graph = nx.Graph()
        graph.add_node("a", viewer_count=100, viewers=10, game_name="Game", language="en")
        graph.add_node("b", viewer_count=90, viewers=9, game_name="Game", language="en")
        graph.add_edge("a", "b", weight=5)

        storage = MockS3Storage()
        ok = export_frontend_data(
            graph=graph,
            partition={"a": 0, "b": 1},
            communities={0: {"a"}, 1: {"b"}},
            labels={0: "Mixed", 1: "Mixed"},
            detection_stats={"modularity": 0.1},
            aggregator_stats={"total_unique_viewers_across_all": 2, "total_snapshots": 1},
            storage=storage,
        )

        assert ok is True
        ids = [community["id"] for community in storage._json_uploads["data/frontend-data.json"]["communities"]]
        assert len(ids) == len(set(ids))

    @staticmethod
    def _windowed_graph(members_by_cid):
        """Cliques per community joined by one weak bridge.

        The public graph drops everything outside its largest connected
        component, so disconnected communities would vanish before the identity
        assignment under test ever sees them.
        """
        graph = nx.Graph()
        partition = {}
        firsts = []
        for cid, members in members_by_cid.items():
            firsts.append(members[0])
            for offset, name in enumerate(members):
                graph.add_node(
                    name, viewer_count=100 - offset, viewers=50,
                    game_name="Game", language="en",
                )
                partition[name] = cid
            for a in members:
                for b in members:
                    if a < b:
                        graph.add_edge(a, b, weight=20)
        for a, b in zip(firsts, firsts[1:]):
            graph.add_edge(a, b, weight=1)
        return graph, partition

    def _export_window(self, members_by_cid, labels, key, anchor, also_write=()):
        graph, partition = self._windowed_graph(members_by_cid)
        storage = MockS3Storage()
        assert export_frontend_data(
            graph=graph,
            partition=partition,
            communities={cid: set(m) for cid, m in members_by_cid.items()},
            labels=labels,
            detection_stats={"modularity": 0.5},
            aggregator_stats={"total_unique_viewers_across_all": 9, "total_snapshots": 3},
            storage=storage,
            output_key=key,
            also_write=also_write,
            anchor=anchor,
        ) is True
        payload = storage._json_uploads[key]
        return storage, {c["id"]: c["color"] for c in payload["communities"]}

    def test_anchor_holds_community_colors_when_size_rank_changes(self):
        """A later window must not repaint a community just because it grew.

        Colour is otherwise assigned by size rank, so the map would recolour
        itself every time the window filter moved.
        """
        big, small = ["a1", "a2", "a3", "a4", "a5"], ["b1", "b2", "b3"]
        labels = {0: "FPS English", 1: "Cozy English"}
        anchor = {}

        _, canonical = self._export_window(
            {0: big, 1: small}, labels,
            "data/frontend-data-30d.json", anchor,
            also_write=("data/frontend-data.json",),
        )

        # Cozy overtakes FPS in the wider window; rank ordering alone would swap
        # their colours here.
        _, wider = self._export_window(
            {0: big + ["a6"], 1: small + ["b4", "b5", "b6", "b7", "b8"]},
            labels, "data/frontend-data-90d.json", anchor,
        )

        assert set(wider) == set(canonical)
        assert all(wider[slug] == canonical[slug] for slug in canonical)

    def test_anchor_gives_a_new_community_its_own_color(self):
        big, small = ["a1", "a2", "a3", "a4", "a5"], ["b1", "b2", "b3"]
        labels = {0: "FPS English", 1: "Cozy English"}
        anchor = {}

        _, canonical = self._export_window(
            {0: big, 1: small}, labels, "data/frontend-data-30d.json", anchor
        )
        _, wider = self._export_window(
            {0: big, 1: small, 2: ["z1", "z2", "z3"]},
            {**labels, 2: "Chess English"},
            "data/frontend-data-90d.json", anchor,
        )

        assert all(wider[slug] == canonical[slug] for slug in canonical)
        assert "chess-english" in wider
        assert len(set(wider.values())) == 3

    def test_canonical_window_also_writes_the_unsuffixed_key(self):
        """smoke-test.sh and pre-filter clients still fetch the unsuffixed file."""
        storage, _ = self._export_window(
            {0: ["a1", "a2", "a3"], 1: ["b1", "b2", "b3"]},
            {0: "FPS English", 1: "Cozy English"},
            "data/frontend-data-30d.json", {},
            also_write=("data/frontend-data.json",),
        )
        assert storage._json_uploads["data/frontend-data.json"] == \
            storage._json_uploads["data/frontend-data-30d.json"]

    def test_pending_export_is_schema_valid_and_contains_no_graph_data(self):
        storage = MockS3Storage()
        assert export_pending_frontend_data(
            storage=storage,
            pending_windows=(14, 30, 90),
            default_window=14,
        ) is True

        payload = storage._json_uploads["data/frontend-data.json"]
        assert payload["availableWindows"] == []
        assert payload["pendingWindows"] == [14, 30, 90]
        assert payload["defaultWindow"] == 14
        assert payload["communities"] == []
        assert payload["channels"] == []
        assert payload["edges"] == []
        assert "chatter" not in json.dumps(payload).lower()


# ═══════════════════════════════════════════════════════════════════════════════
# VOD Snapshot Loading Tests
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def tmp_vod_snapshots_dir(tmp_path):
    """Create a temporary logs/vod_snapshots directory with VOD JSON snapshot files."""
    vod_dir = tmp_path / "logs" / "vod_snapshots"
    vod_dir.mkdir(parents=True)

    vod_snap_1 = {
        "channel": "streamer_v1",
        "timestamp": "2025-01-01T14:00:00",
        "viewer_count": 1200,
        "game_name": "Minecraft",
        "title": "VOD: Sunday session",
        "started_at": "2025-01-01T12:00:00",
        "chatters": ["alpha", "beta", "gamma"],
        "_source": "vod",
    }
    vod_snap_2 = {
        "channel": "streamer_v2",
        "timestamp": "2025-01-01T14:05:00",
        "viewer_count": 800,
        "game_name": "Minecraft",
        "title": "VOD: Monday highlights",
        "started_at": "2025-01-01T13:00:00",
        "chatters": ["beta", "delta", "epsilon"],
        "_source": "vod",
    }

    for i, snap in enumerate([vod_snap_1, vod_snap_2]):
        filepath = vod_dir / f"snapshot_{i:03d}.json"
        with open(filepath, "w") as f:
            json.dump(snap, f)

    return tmp_path / "logs"

class TestVODSnapshotLoading:
    """Tests for VOD snapshot ingestion from local filesystem."""

    def test_load_vod_snapshots_count(self, tmp_vod_snapshots_dir):
        agg = DataAggregator(str(tmp_vod_snapshots_dir))
        agg.storage = None
        count = agg.load_vod_snapshots()
        assert count == 2, f"Expected 2 VOD snapshots loaded, got {count}"

    def test_vod_channels_in_viewer_map(self, tmp_vod_snapshots_dir):
        agg = DataAggregator(str(tmp_vod_snapshots_dir))
        agg.storage = None
        agg.load_vod_snapshots()
        viewers = agg.get_channel_viewers()
        assert "streamer_v1" in viewers
        assert "streamer_v2" in viewers

    def test_vod_viewer_sets_correct(self, tmp_vod_snapshots_dir):
        agg = DataAggregator(str(tmp_vod_snapshots_dir))
        agg.storage = None
        agg.load_vod_snapshots()
        viewers = agg.get_channel_viewers()
        assert viewers["streamer_v1"] == {"alpha", "beta", "gamma"}
        assert viewers["streamer_v2"] == {"beta", "delta", "epsilon"}

    def test_vod_source_count_tracked(self, tmp_vod_snapshots_dir):
        agg = DataAggregator(str(tmp_vod_snapshots_dir))
        agg.storage = None
        agg.load_vod_snapshots()
        assert agg.snapshot_source_counts.get("vod", 0) == 2

    def test_load_all_vod_count(self, tmp_vod_snapshots_dir):
        agg = DataAggregator(str(tmp_vod_snapshots_dir))
        agg.storage = None
        json_count, csv_count, vod_count, parquet_count = agg.load_all()
        assert vod_count == 2

    def test_vod_metadata_stored(self, tmp_vod_snapshots_dir):
        agg = DataAggregator(str(tmp_vod_snapshots_dir))
        agg.storage = None
        agg.load_vod_snapshots()
        meta = agg.get_channel_metadata()
        assert meta["streamer_v1"]["game_name"] == "Minecraft"
        assert meta["streamer_v1"]["viewer_count"] == 1200

    def test_empty_vod_dir_returns_zero(self, tmp_path):
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        agg = DataAggregator(str(logs_dir))
        agg.storage = None
        count = agg.load_vod_snapshots()
        assert count == 0

    def test_vod_graph_edges_on_overlap(self, tmp_vod_snapshots_dir):
        """VOD data flows into the graph with correct overlap weights."""
        agg = DataAggregator(str(tmp_vod_snapshots_dir))
        agg.storage = None
        agg.load_vod_snapshots()
        viewers = agg.get_channel_viewers()

        builder = GraphBuilder(overlap_threshold=1)
        g = builder.build_graph(viewers)

        # streamer_v1 and streamer_v2 share 'beta' => weight 1
        assert g.has_edge("streamer_v1", "streamer_v2")
        assert g["streamer_v1"]["streamer_v2"]["weight"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# S3 Backend Integration Tests (mocked)
# ═══════════════════════════════════════════════════════════════════════════════


class MockS3Storage:
    """Minimal S3Storage mock for testing without real AWS credentials."""

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
            return []  # No parquet in this mock
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


class TestS3Integration:
    """Tests for S3 storage backend path through DataAggregator (mocked)."""

    def test_storage_startup_uses_bucket_metadata_not_unscoped_listing(self):
        """The least-privilege task role must not need an unscoped ListBucket."""
        s3 = MagicMock()
        with patch("storage.boto3.client", return_value=s3):
            storage = S3Storage(
                bucket="private-surveys",
                prefix="vieweratlas/raw/snapshots/v2",
            )

        s3.get_bucket_location.assert_called_once_with(Bucket="private-surveys")
        s3.head_bucket.assert_not_called()
        assert storage.prefix == "vieweratlas/raw/snapshots/v2/"

    @pytest.fixture
    def live_snapshots(self):
        return [
            {
                "channel": "s3_streamer_a",
                "timestamp": "2025-02-01T10:00:00",
                "viewer_count": 500,
                "game_name": "Apex Legends",
                "title": "S3 live stream",
                "chatters": ["user1", "user2", "user3"],
            },
            {
                "channel": "s3_streamer_b",
                "timestamp": "2025-02-01T10:00:00",
                "viewer_count": 300,
                "game_name": "Apex Legends",
                "title": "S3 live stream B",
                "chatters": ["user2", "user3", "user4"],
            },
        ]

    @pytest.fixture
    def vod_snapshots(self):
        return [
            {
                "channel": "s3_vod_channel",
                "timestamp": "2025-02-01T08:00:00",
                "viewer_count": 200,
                "game_name": "Apex Legends",
                "title": "VOD replay",
                "chatters": ["user1", "user5"],
                "_source": "vod",
            }
        ]

    def test_s3_load_json_snapshots(self, live_snapshots, tmp_path):
        agg = DataAggregator(str(tmp_path), storage=MockS3Storage(live_snapshots=live_snapshots))
        count = agg.load_json_snapshots()
        assert count == 2

    def test_s3_channels_populated(self, live_snapshots, tmp_path):
        agg = DataAggregator(str(tmp_path), storage=MockS3Storage(live_snapshots=live_snapshots))
        agg.load_json_snapshots()
        viewers = agg.get_channel_viewers()
        assert "s3_streamer_a" in viewers
        assert "s3_streamer_b" in viewers
        assert viewers["s3_streamer_a"] == {"user1", "user2", "user3"}

    def test_s3_load_vod_snapshots(self, vod_snapshots, tmp_path):
        agg = DataAggregator(str(tmp_path), storage=MockS3Storage(vod_snapshots=vod_snapshots))
        count = agg.load_vod_snapshots()
        assert count == 1
        viewers = agg.get_channel_viewers()
        assert "s3_vod_channel" in viewers
        assert viewers["s3_vod_channel"] == {"user1", "user5"}

    def test_s3_vod_source_tracked(self, vod_snapshots, tmp_path):
        agg = DataAggregator(str(tmp_path), storage=MockS3Storage(vod_snapshots=vod_snapshots))
        agg.load_vod_snapshots()
        assert agg.snapshot_source_counts.get("vod", 0) == 1

    def test_s3_load_all_returns_correct_counts(self, live_snapshots, vod_snapshots, tmp_path):
        storage = MockS3Storage(live_snapshots=live_snapshots, vod_snapshots=vod_snapshots)
        agg = DataAggregator(str(tmp_path), storage=storage)
        json_count, csv_count, vod_count, parquet_count = agg.load_all()
        assert json_count == 2
        assert vod_count == 1

    def test_s3_graph_from_live_data(self, live_snapshots, tmp_path):
        agg = DataAggregator(str(tmp_path), storage=MockS3Storage(live_snapshots=live_snapshots))
        agg.load_json_snapshots()
        viewers = agg.get_channel_viewers()

        builder = GraphBuilder(overlap_threshold=1)
        g = builder.build_graph(viewers)

        # s3_streamer_a and s3_streamer_b share user2 and user3 => weight 2
        assert g.has_edge("s3_streamer_a", "s3_streamer_b")
        assert g["s3_streamer_a"]["s3_streamer_b"]["weight"] == 2


# ═══════════════════════════════════════════════════════════════════════════════
# Parquet Snapshot Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestParquetSnapshots:
    """Tests for Parquet-based snapshot loading via storage backend."""

    @pytest.fixture
    def parquet_storage(self):
        """Create a MockS3Storage with Parquet data."""
        import pandas as pd
        from io import BytesIO

        rows = [
            {
                "channel": "pq_streamer_a",
                "timestamp": "2025-03-01T10:00:00",
                "viewer_count": 500,
                "game_name": "Valorant",
                "title": "Ranked",
                "started_at": "2025-03-01T08:00:00",
                "language": "en",
                "chatters_json": '["alice", "bob", "carol"]',
            },
            {
                "channel": "pq_streamer_b",
                "timestamp": "2025-03-01T10:00:00",
                "viewer_count": 300,
                "game_name": "Fortnite",
                "title": "Squads",
                "started_at": "2025-03-01T09:00:00",
                "language": "en",
                "chatters_json": '["bob", "carol", "dave"]',
            },
        ]
        df = pd.DataFrame(rows)
        buf = BytesIO()
        df.to_parquet(buf, index=False, engine="pyarrow")
        parquet_bytes = buf.getvalue()

        return MockS3Storage(
            parquet_data={"raw/snapshots/2025/03/01/cycle_20250301_100000.parquet": parquet_bytes}
        )

    def test_load_parquet_snapshots_count(self, parquet_storage, tmp_path):
        agg = DataAggregator(str(tmp_path), storage=parquet_storage)
        count = agg.load_parquet_snapshots()
        assert count == 2

    def test_parquet_channels_populated(self, parquet_storage, tmp_path):
        agg = DataAggregator(str(tmp_path), storage=parquet_storage)
        agg.load_parquet_snapshots()
        viewers = agg.get_channel_viewers()
        assert "pq_streamer_a" in viewers
        assert "pq_streamer_b" in viewers

    def test_parquet_viewer_sets_correct(self, parquet_storage, tmp_path):
        agg = DataAggregator(str(tmp_path), storage=parquet_storage)
        agg.load_parquet_snapshots()
        viewers = agg.get_channel_viewers()
        assert viewers["pq_streamer_a"] == {"alice", "bob", "carol"}
        assert viewers["pq_streamer_b"] == {"bob", "carol", "dave"}

    def test_parquet_source_count_tracked(self, parquet_storage, tmp_path):
        agg = DataAggregator(str(tmp_path), storage=parquet_storage)
        agg.load_parquet_snapshots()
        assert agg.snapshot_source_counts.get("live", 0) == 2

    def test_parquet_graph_edge_on_overlap(self, parquet_storage, tmp_path):
        """Parquet data flows into the graph with correct overlap weights."""
        agg = DataAggregator(str(tmp_path), storage=parquet_storage)
        agg.load_parquet_snapshots()
        viewers = agg.get_channel_viewers()

        builder = GraphBuilder(overlap_threshold=1)
        g = builder.build_graph(viewers)

        # pq_streamer_a and pq_streamer_b share bob, carol => weight 2
        assert g.has_edge("pq_streamer_a", "pq_streamer_b")
        assert g["pq_streamer_a"]["pq_streamer_b"]["weight"] == 2

    def test_parquet_metadata_stored(self, parquet_storage, tmp_path):
        agg = DataAggregator(str(tmp_path), storage=parquet_storage)
        agg.load_parquet_snapshots()
        meta = agg.get_channel_metadata()
        assert meta["pq_streamer_a"]["game_name"] == "Valorant"
        assert meta["pq_streamer_a"]["viewer_count"] == 500

    def test_load_all_includes_parquet_count(self, parquet_storage, tmp_path):
        agg = DataAggregator(str(tmp_path), storage=parquet_storage)
        result = agg.load_all()
        assert len(result) == 4
        json_count, csv_count, vod_count, parquet_count = result
        assert parquet_count == 2

    def test_memory_estimate(self, parquet_storage, tmp_path):
        agg = DataAggregator(str(tmp_path), storage=parquet_storage)
        agg.load_parquet_snapshots()
        mb = agg.get_viewer_memory_estimate_mb()
        assert mb >= 0

    def test_v2_loader_keeps_completed_empty_rows_and_skips_failures(self, tmp_path):
        import pandas as pd
        from io import BytesIO

        rows = [
            {
                "schema_version": 2,
                "channel": "completed_with_authors",
                "collection_status": "completed",
                "chatters_json": '["ALICE", "bob"]',
                "chatter_ids_json": '["1", "2"]',
            },
            {
                "schema_version": 2,
                "channel": "completed_empty",
                "collection_status": "completed",
                "chatters_json": "[]",
                "chatter_ids_json": "[]",
            },
            {
                "schema_version": 2,
                "channel": "failed_channel",
                "collection_status": "subscription_failed",
                "chatters_json": "[]",
                "chatter_ids_json": "[]",
            },
        ]
        output = BytesIO()
        pd.DataFrame(rows).to_parquet(output, index=False, engine="pyarrow")
        storage = MockS3Storage(
            parquet_data={
                "raw/snapshots/v2/date=2026-08-12/session=test/batch=01.parquet": output.getvalue()
            }
        )
        storage._json_uploads[
            "raw/snapshots/v2/date=2026-08-12/session=test/manifest.json"
        ] = {"status": "complete_with_errors"}

        agg = DataAggregator(str(tmp_path), storage=storage)
        assert agg.load_parquet_snapshots() == 2
        viewers = agg.get_channel_viewers()
        assert viewers["completed_with_authors"] == {"alice", "bob"}
        assert viewers["completed_empty"] == set()
        assert "failed_channel" not in viewers

    @pytest.mark.parametrize("manifest_status", [None, "running", "partial"])
    def test_v2_loader_skips_session_without_terminal_manifest(
        self, tmp_path, manifest_status
    ):
        import pandas as pd
        from io import BytesIO

        output = BytesIO()
        pd.DataFrame(
            [
                {
                    "schema_version": 2,
                    "channel": "must_not_be_loaded",
                    "collection_status": "completed",
                    "chatters_json": '["alice"]',
                    "chatter_ids_json": '["1"]',
                }
            ]
        ).to_parquet(output, index=False, engine="pyarrow")
        storage = MockS3Storage(
            parquet_data={
                "raw/snapshots/v2/date=2026-08-12/session=unfinished/batch=01.parquet": output.getvalue()
            }
        )
        if manifest_status is not None:
            storage._json_uploads[
                "raw/snapshots/v2/date=2026-08-12/session=unfinished/manifest.json"
            ] = {"status": manifest_status}

        agg = DataAggregator(str(tmp_path), storage=storage)
        assert agg.load_parquet_snapshots() == 0
        assert "must_not_be_loaded" not in agg.get_channel_viewers()


class TestWeightingModes:
    """Raw counts reward sampling depth; normalised modes must not."""

    @pytest.fixture
    def uneven(self):
        # `small` is entirely contained in `big`; `peer` is the same size as
        # `big` and shares half of it.
        return {
            "big": {f"u{i}" for i in range(1000)},
            "small": {f"u{i}" for i in range(100)},
            "peer": {f"u{i}" for i in range(500, 1500)},
        }

    def test_shared_count_is_the_unchanged_default(self, uneven):
        g = GraphBuilder(overlap_threshold=1).build_graph(uneven)
        assert g["big"]["peer"]["weight"] == 500
        assert g["big"]["small"]["weight"] == 100

    def test_measured_count_always_recorded_alongside_weight(self, uneven):
        for mode in GraphBuilder.WEIGHTING_MODES:
            g = GraphBuilder(overlap_threshold=1, weighting_mode=mode).build_graph(uneven)
            assert g["big"]["peer"]["shared"] == 500
            assert g["big"]["small"]["shared"] == 100
            assert isinstance(g["big"]["peer"]["shared"], int)

    def test_jaccard_normalises_by_union(self, uneven):
        g = GraphBuilder(overlap_threshold=1, weighting_mode="jaccard").build_graph(uneven)
        # 500 / (1000 + 1000 - 500)
        assert g["big"]["peer"]["weight"] == pytest.approx(1 / 3)
        # 100 / (1000 + 100 - 100)
        assert g["big"]["small"]["weight"] == pytest.approx(0.1)

    def test_overlap_coef_recognises_containment(self, uneven):
        """A small channel fully inside a large one scores 1.0, not 100."""
        g = GraphBuilder(overlap_threshold=1, weighting_mode="overlap_coef").build_graph(uneven)
        assert g["big"]["small"]["weight"] == pytest.approx(1.0)
        assert g["big"]["peer"]["weight"] == pytest.approx(0.5)
        # Raw count ranks these the other way round.
        assert g["big"]["small"]["shared"] < g["big"]["peer"]["shared"]

    def test_normalized_threshold_drops_weak_pairs(self, uneven):
        g = GraphBuilder(
            overlap_threshold=1, weighting_mode="jaccard",
            normalized_overlap_threshold=0.2,
        ).build_graph(uneven)
        assert g.has_edge("big", "peer")        # 0.333
        assert not g.has_edge("big", "small")   # 0.100
        assert g.number_of_edges() == 1

    def test_normalized_threshold_ignored_by_shared_count(self, uneven):
        g = GraphBuilder(
            overlap_threshold=1, weighting_mode="shared_count",
            normalized_overlap_threshold=0.9,
        ).build_graph(uneven)
        assert g.has_edge("big", "small")

    def test_raw_threshold_still_applies_in_normalized_modes(self, uneven):
        g = GraphBuilder(
            overlap_threshold=200, weighting_mode="jaccard"
        ).build_graph(uneven)
        assert not g.has_edge("big", "small")   # only 100 shared
        assert g.has_edge("big", "peer")

    def test_invalid_mode_and_threshold_rejected(self):
        with pytest.raises(ValueError):
            GraphBuilder(weighting_mode="cosine")
        with pytest.raises(ValueError):
            GraphBuilder(normalized_overlap_threshold=1.5)


class TestObservationFiltering:
    """Channels sampled once cannot produce reliable overlap at any threshold."""

    def _agg_with(self, tmp_path, counts):
        agg = DataAggregator(str(tmp_path))
        for channel, n in counts.items():
            for i in range(n):
                agg._ingest_snapshot({"channel": channel, "chatters": [f"u{i}", "shared"]})
        return agg

    def test_observations_counted_per_channel(self, tmp_path):
        agg = self._agg_with(tmp_path, {"often": 5, "once": 1})
        assert agg.get_channel_observations() == {"often": 5, "once": 1}

    def test_filter_keeps_only_well_sampled_channels(self, tmp_path):
        agg = self._agg_with(tmp_path, {"often": 5, "twice": 2, "once": 1})
        kept = agg.filter_channels_by_observations(2)
        assert set(kept) == {"often", "twice"}

    def test_filter_of_one_is_a_no_op(self, tmp_path):
        agg = self._agg_with(tmp_path, {"often": 5, "once": 1})
        assert set(agg.filter_channels_by_observations(1)) == {"often", "once"}


# ═══════════════════════════════════════════════════════════════════════════════
# Analysis Window Tests
# ═══════════════════════════════════════════════════════════════════════════════


def _v2_batch_bytes(channel, chatters):
    import pandas as pd
    from io import BytesIO

    output = BytesIO()
    pd.DataFrame(
        [
            {
                "schema_version": 2,
                "channel": channel,
                "collection_status": "completed",
                "chatters_json": json.dumps(chatters),
                "chatter_ids_json": json.dumps([str(i) for i in range(len(chatters))]),
            }
        ]
    ).to_parquet(output, index=False, engine="pyarrow")
    return output.getvalue()


def _survey_storage(days):
    """Build storage holding one completed v2 survey per given date string."""
    parquet, manifests = {}, {}
    for day in days:
        prefix = f"raw/snapshots/v2/date={day}/session=s{day}"
        parquet[f"{prefix}/batch=01.parquet"] = _v2_batch_bytes(f"ch_{day}", ["alice"])
        manifests[f"{prefix}/manifest.json"] = {"status": "complete"}
    storage = MockS3Storage(parquet_data=parquet)
    storage._json_uploads.update(manifests)
    return storage


class TestAnalysisWindow:
    """Without a window, viewer sets only grow and graph density climbs over time."""

    ALL_DAYS = ["2026-05-01", "2026-06-15", "2026-08-10", "2026-08-11", "2026-08-12"]

    def test_no_window_loads_every_retained_survey(self, tmp_path):
        agg = DataAggregator(str(tmp_path), storage=_survey_storage(self.ALL_DAYS))
        assert agg.load_parquet_snapshots() == 5
        assert agg.window_start is None and agg.window_end is None

    def test_window_anchors_to_newest_snapshot_not_wall_clock(self, tmp_path):
        """Replays and backfills must reproduce the original graph."""
        agg = DataAggregator(
            str(tmp_path), storage=_survey_storage(self.ALL_DAYS), window_days=3
        )
        assert agg.load_parquet_snapshots() == 3
        assert agg.window_end == date(2026, 8, 12)
        assert agg.window_start == date(2026, 8, 10)
        channels = agg.get_channel_viewers()
        assert set(channels) == {"ch_2026-08-10", "ch_2026-08-11", "ch_2026-08-12"}

    def test_window_history_ignores_unfinished_v2_surveys(self):
        """An early Parquet PUT is not data until its manifest commits it."""
        storage = _survey_storage(["2026-08-12"])
        unfinished = "raw/snapshots/v2/date=2026-01-01/session=unfinished"
        storage._parquet[f"{unfinished}/batch=01.parquet"] = _v2_batch_bytes(
            "must_not_extend_history", ["alice"]
        )
        storage._json_uploads[f"{unfinished}/manifest.json"] = {"status": "partial"}

        assert survey_date_span(storage) == (date(2026, 8, 12), date(2026, 8, 12))

    def test_window_boundary_is_inclusive(self, tmp_path):
        """window_days=N spans N distinct days, anchor included."""
        agg = DataAggregator(
            str(tmp_path), storage=_survey_storage(self.ALL_DAYS), window_days=1
        )
        assert agg.load_parquet_snapshots() == 1
        assert agg.window_start == agg.window_end == date(2026, 8, 12)

    def test_window_larger_than_data_keeps_everything(self, tmp_path):
        agg = DataAggregator(
            str(tmp_path), storage=_survey_storage(self.ALL_DAYS), window_days=3650
        )
        assert agg.load_parquet_snapshots() == 5

    def test_window_applies_to_legacy_date_partitioned_keys(self, tmp_path):
        import pandas as pd
        from io import BytesIO

        parquet = {}
        for day in ["2026/08/01", "2026/08/12"]:
            output = BytesIO()
            pd.DataFrame(
                [{"channel": f"legacy_{day.replace('/', '')}", "chatters_json": '["bob"]'}]
            ).to_parquet(output, index=False, engine="pyarrow")
            parquet[f"raw/snapshots/{day}/cycle_1.parquet"] = output.getvalue()

        agg = DataAggregator(
            str(tmp_path), storage=MockS3Storage(parquet_data=parquet), window_days=2
        )
        assert agg.load_parquet_snapshots() == 1
        assert "legacy_20260812" in agg.get_channel_viewers()

    def test_undated_keys_are_never_dropped_by_the_window(self, tmp_path):
        """A window must not silently discard data whose date it cannot parse."""
        storage = _survey_storage(["2026-08-12"])
        storage._parquet["raw/snapshots/oddball.parquet"] = _v2_batch_bytes(
            "undated_channel", ["carol"]
        )
        agg = DataAggregator(str(tmp_path), storage=storage, window_days=1)
        agg.load_parquet_snapshots()
        assert "undated_channel" in agg.get_channel_viewers()

    def test_window_reported_in_statistics_and_collection_period(self, tmp_path):
        agg = DataAggregator(
            str(tmp_path), storage=_survey_storage(self.ALL_DAYS), window_days=3
        )
        agg.load_parquet_snapshots()
        stats = agg.get_statistics()
        assert stats["analysis_window_days"] == 3
        assert stats["window_start"] == "2026-08-10"
        assert stats["window_end"] == "2026-08-12"
        assert stats["collection_period"] == "Aug 10 – Aug 12, 2026"

    def test_window_reports_days_covered_not_days_requested(self, tmp_path):
        """collection_period goes straight onto the public site.

        A 90-day window over 60 days of surveys must not advertise a month of
        data that was never collected.
        """
        agg = DataAggregator(
            str(tmp_path), storage=_survey_storage(["2026-08-10", "2026-08-11", "2026-08-12"]),
            window_days=90,
        )
        agg.load_parquet_snapshots()
        stats = agg.get_statistics()

        # Requested 90 days; only three exist, so that is what is reported.
        assert stats["window_start"] == "2026-08-10"
        assert stats["window_end"] == "2026-08-12"
        assert stats["collection_period"] == "Aug 10 – Aug 12, 2026"
        # The requested length is still recorded, so the shortfall is visible.
        assert stats["analysis_window_days"] == 90

    def test_invalid_window_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            DataAggregator(str(tmp_path), window_days=0)
        with pytest.raises(ValueError):
            AnalysisConfig(analysis_window_days=0)

    def test_window_is_configurable_from_yaml(self, tmp_path):
        yaml_file = tmp_path / "window.yaml"
        yaml_file.write_text("analysis:\n  analysis_window_days: 30\n")
        assert load_config_from_yaml(str(yaml_file)).analysis.analysis_window_days == 30

    def test_multi_window_config_requires_the_canonical_window(self):
        """The canonical window writes the unsuffixed public file.

        Publishing a set that excludes it would leave that file stale while the
        suffixed ones moved.
        """
        AnalysisConfig(analysis_window_days=30, analysis_windows=(14, 30, 90))
        with pytest.raises(ValueError, match="analysis_window_days must appear"):
            AnalysisConfig(analysis_window_days=30, analysis_windows=(14, 90))
        with pytest.raises(ValueError, match="at least 1"):
            AnalysisConfig(analysis_window_days=30, analysis_windows=(0, 30))

    def test_window_overlap_thresholds_are_validated(self):
        AnalysisConfig(window_overlap_thresholds={14: 1, 90: 4})
        with pytest.raises(ValueError, match="keys must be day counts"):
            AnalysisConfig(window_overlap_thresholds={0: 1})
        with pytest.raises(ValueError, match="values must be non-negative"):
            AnalysisConfig(window_overlap_thresholds={14: -1})

    @staticmethod
    def _plan(window_days, windows, covered_days):
        """window_plan() for a deployment holding `covered_days` of surveys."""
        import main as app_main

        runner = app_main.PipelineRunner.__new__(app_main.PipelineRunner)
        runner.config = PipelineConfig(
            collection=CollectionConfig(),
            analysis=AnalysisConfig(analysis_window_days=window_days, analysis_windows=windows),
        )
        runner.logger = logging.getLogger("test")
        runner.storage = MagicMock()
        end = date(2026, 8, 26)
        span = (end - timedelta(days=covered_days - 1), end) if covered_days else (None, None)
        with patch("main.survey_date_span", return_value=span):
            return runner.window_plan()

    def test_windows_short_of_data_are_pending_not_analysed(self):
        """A 90-day window over 14 days of surveys is not a 90-day window.

        Analysing it would spend a full extra pass republishing the 14-day
        graph, so it waits and the browser shows PENDING instead.
        """
        plan = self._plan(30, (14, 30, 90), covered_days=14)
        assert plan["available"] == [14]
        assert plan["pending"] == [30, 90]
        # 30 is configured canonical but not ready, so the widest ready window
        # becomes the default rather than opening on a window with no file.
        assert plan["default"] == 14

    def test_windows_promote_themselves_as_surveys_accumulate(self):
        """No redeploy should be needed for a window to start working."""
        before_14 = self._plan(30, (14, 30, 90), 13)
        assert before_14["available"] == []
        assert before_14["pending"] == [14, 30, 90]
        assert before_14["default"] == 14

        at_30 = self._plan(30, (14, 30, 90), 30)
        assert sorted(at_30["available"]) == [14, 30]
        assert at_30["pending"] == [90]
        # The configured canonical is ready now, so it reclaims the default.
        assert at_30["default"] == 30

        at_90 = self._plan(30, (14, 30, 90), 90)
        assert sorted(at_90["available"]) == [14, 30, 90]
        assert at_90["pending"] == []
        assert at_90["default"] == 30

    def test_default_window_leads_so_it_seeds_community_colors(self):
        plan = self._plan(30, (14, 30, 90), 90)
        assert plan["available"][0] == plan["default"] == 30

    def test_every_window_is_pending_before_the_narrowest_is_full(self):
        """A short graph must never be published under a longer label."""
        plan = self._plan(30, (14, 30, 90), covered_days=3)
        assert plan["available"] == []
        assert plan["pending"] == [14, 30, 90]
        assert plan["default"] == 14

    def test_unwindowed_deployment_declares_no_windows(self):
        import main as app_main

        runner = app_main.PipelineRunner.__new__(app_main.PipelineRunner)
        runner.config = PipelineConfig(collection=CollectionConfig(), analysis=AnalysisConfig())
        runner.logger = logging.getLogger("test")
        runner.storage = MagicMock()
        plan = runner.window_plan()
        assert plan["available"] == [None] and plan["pending"] == []

    def test_duplicate_windows_are_analysed_once(self):
        """A repeat entry would cost a full extra aggregate/graph/detect pass."""
        plan = self._plan(30, (14, 14, 30, 90), covered_days=90)
        assert sorted(plan["available"]) == [14, 30, 90]

    def test_frontend_key_is_suffixed_per_window(self):
        import main as app_main

        assert app_main.PipelineRunner._frontend_key(14) == "data/frontend-data-14d.json"
        assert app_main.PipelineRunner._frontend_key(90) == "data/frontend-data-90d.json"
        # An unwindowed run keeps the original key untouched.
        assert app_main.PipelineRunner._frontend_key(None) == "data/frontend-data.json"

    def test_unwindowed_run_does_not_publish_the_same_key_twice(self):
        """A single-window deployment writes data/frontend-data.json as its own
        output key; also_write must not duplicate that PUT."""
        import main as app_main

        runner = app_main.PipelineRunner.__new__(app_main.PipelineRunner)
        runner.config = PipelineConfig(collection=CollectionConfig(), analysis=AnalysisConfig())
        runner.logger = logging.getLogger("test")
        runner.storage = MagicMock()

        graph = nx.Graph()
        graph.add_edge("a", "b", weight=9)
        aggregator = MagicMock()
        aggregator.get_statistics.return_value = {}
        aggregator.get_channel_metadata.return_value = {}
        saved = []

        with patch.object(app_main.PipelineRunner, "_step_aggregate", return_value=aggregator), \
             patch.object(app_main.PipelineRunner, "_step_build_graph", return_value=graph), \
             patch.object(app_main.PipelineRunner, "_step_detect_communities",
                          return_value=({"a": 0, "b": 0}, {0: {"a", "b"}},
                                        {"num_communities": 1, "modularity": 0.5}, graph)), \
             patch.object(app_main.PipelineRunner, "_step_tag_communities",
                          return_value=({0: "Label"}, {})), \
             patch.object(app_main.PipelineRunner, "_step_visualize"), \
             patch.object(app_main.PipelineRunner, "_step_save_results",
                          side_effect=lambda *a, **kw: saved.append(kw)):
            assert app_main.PipelineRunner.run_analysis_pipeline(runner)["status"] == "success"

        assert len(saved) == 1
        assert saved[0]["output_key"] == "data/frontend-data.json"
        assert saved[0]["also_write"] == ()

    def test_published_windows_are_declared_to_the_browser(self):
        """The filter renders from the payload, so it shows exactly the windows
        that exist and PENDING for the ones still filling up."""
        import main as app_main

        def run(analysis_kwargs, covered_days):
            runner = app_main.PipelineRunner.__new__(app_main.PipelineRunner)
            runner.config = PipelineConfig(
                collection=CollectionConfig(), analysis=AnalysisConfig(**analysis_kwargs)
            )
            runner.logger = logging.getLogger("test")
            runner.storage = MagicMock()
            graph = nx.Graph()
            graph.add_edge("a", "b", weight=9)
            aggregator = MagicMock()
            aggregator.get_statistics.return_value = {}
            aggregator.get_channel_metadata.return_value = {}
            saved = []
            end_day = date(2026, 8, 26)
            span = (end_day - timedelta(days=covered_days - 1), end_day)
            with patch("main.survey_date_span", return_value=span), \
                 patch.object(app_main.PipelineRunner, "_step_aggregate", return_value=aggregator), \
                 patch.object(app_main.PipelineRunner, "_step_build_graph", return_value=graph), \
                 patch.object(app_main.PipelineRunner, "_step_detect_communities",
                              return_value=({"a": 0, "b": 0}, {0: {"a", "b"}},
                                            {"num_communities": 1, "modularity": 0.5}, graph)), \
                 patch.object(app_main.PipelineRunner, "_step_tag_communities",
                              return_value=({0: "Label"}, {})), \
                 patch.object(app_main.PipelineRunner, "_step_visualize"), \
                 patch.object(app_main.PipelineRunner, "_step_save_results",
                              side_effect=lambda *a, **kw: saved.append(kw)):
                app_main.PipelineRunner.run_analysis_pipeline(runner)
            return saved

        # Only 14 days exist: one pass runs, the other two are declared pending.
        early = run({"analysis_window_days": 30, "analysis_windows": (14, 30, 90)}, 14)
        assert len(early) == 1
        assert early[0]["available_windows"] == (14,)
        assert early[0]["pending_windows"] == (30, 90)
        assert early[0]["default_window"] == 14

        # Once every window is full, all three run and nothing is pending.
        mature = run({"analysis_window_days": 30, "analysis_windows": (14, 30, 90)}, 90)
        assert len(mature) == 3
        assert all(kw["available_windows"] == (14, 30, 90) for kw in mature)
        assert all(kw["pending_windows"] == () for kw in mature)
        assert all(kw["default_window"] == 30 for kw in mature)

        # A single-window run advertises nothing, which hides the control.
        single = run({"analysis_window_days": 30}, 90)
        assert single[0]["available_windows"] == (30,)
        assert single[0]["pending_windows"] == ()

    def test_all_pending_plan_refreshes_both_status_outputs(self):
        """Daily analysis is still a successful, freshness-checkable run."""
        import main as app_main

        runner = app_main.PipelineRunner.__new__(app_main.PipelineRunner)
        runner.config = PipelineConfig(
            collection=CollectionConfig(),
            analysis=AnalysisConfig(
                analysis_window_days=30,
                analysis_windows=(14, 30, 90),
            ),
        )
        runner.logger = logging.getLogger("test")
        runner.storage = MockS3Storage()
        end_day = date(2026, 8, 26)

        with patch(
            "main.survey_date_span",
            return_value=(end_day - timedelta(days=2), end_day),
        ):
            result = app_main.PipelineRunner.run_analysis_pipeline(runner)

        assert result["status"] == "success"
        assert result["windows_published"] == 0
        assert result["windows_pending"] == [14, 30, 90]
        private = runner.storage._json_uploads["processed/analysis_results.json"]
        public = runner.storage._json_uploads["data/frontend-data.json"]
        assert private["status"] == "pending"
        assert private["partition"] == {}
        assert public["pendingWindows"] == [14, 30, 90]

    def test_each_window_is_analysed_and_published_once(self):
        """The canonical window leads, carries the private artifacts, and seeds
        the anchor the other windows reuse."""
        import main as app_main

        runner = app_main.PipelineRunner.__new__(app_main.PipelineRunner)
        runner.config = get_rigorous_config()
        # Stated here rather than inherited, so the test keeps meaning if the
        # preset's window list changes.
        runner.config.analysis.analysis_windows = (14, 30, 90)
        runner.config.analysis.window_overlap_thresholds = {14: 1, 90: 5}
        runner.logger = logging.getLogger("test")
        runner.storage = MagicMock()

        graph = nx.Graph()
        graph.add_edge("a", "b", weight=9)
        aggregator = MagicMock()
        aggregator.get_statistics.return_value = {}
        aggregator.get_channel_metadata.return_value = {}

        aggregated, built, saved, visualized = [], [], [], []

        end_day = date(2026, 8, 26)
        with patch("main.survey_date_span",
                   return_value=(end_day - timedelta(days=89), end_day)), \
             patch.object(
            app_main.PipelineRunner, "_step_aggregate",
            side_effect=lambda window_days=None: (aggregated.append(window_days), aggregator)[1],
        ), patch.object(
            app_main.PipelineRunner, "_step_build_graph",
            side_effect=lambda agg, overlap_threshold=None, export_csv=True: (
                built.append((overlap_threshold, export_csv)), graph)[1],
        ), patch.object(
            app_main.PipelineRunner, "_step_detect_communities",
            return_value=({"a": 0, "b": 0}, {0: {"a", "b"}},
                          {"num_communities": 1, "modularity": 0.5}, graph),
        ), patch.object(
            app_main.PipelineRunner, "_step_tag_communities",
            return_value=({0: "Label"}, {}),
        ), patch.object(
            app_main.PipelineRunner, "_step_visualize",
            side_effect=lambda *a: visualized.append(True),
        ), patch.object(
            app_main.PipelineRunner, "_step_save_results",
            side_effect=lambda *a, **kw: saved.append(kw),
        ):
            result = app_main.PipelineRunner.run_analysis_pipeline(runner)

        assert result["status"] == "success"
        assert result["windows_published"] == 3

        # Canonical first, then the rest in ascending order.
        assert aggregated == [30, 14, 90]
        # Each window uses its own calibrated threshold; only the canonical run
        # writes the graph CSVs, which share one dated key.
        assert built == [(2, True), (1, False), (5, False)]

        assert [kw["output_key"] for kw in saved] == [
            "data/frontend-data-30d.json",
            "data/frontend-data-14d.json",
            "data/frontend-data-90d.json",
        ]
        # Only the canonical window refreshes the unsuffixed file and the
        # private artifacts, and visualization runs once rather than per window.
        assert [kw["also_write"] for kw in saved] == [("data/frontend-data.json",), (), ()]
        assert [kw["publish_private"] for kw in saved] == [True, False, False]
        assert len(visualized) == 1
        # One anchor object threads through every window, which is what keeps
        # community colours stable across the filter.
        assert len({id(kw["anchor"]) for kw in saved}) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Mixed Live + VOD Integration Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestMixedLiveVOD:
    """Tests for pipelines combining live snapshots and VOD snapshots."""

    @pytest.fixture
    def mixed_logs_dir(self, tmp_path):
        """Create logs dir with both live JSON snapshots and vod_snapshots/ subfolder."""
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()

        # Live snapshot
        live_snap = {
            "channel": "live_channel",
            "timestamp": "2025-03-01T10:00:00",
            "viewer_count": 1000,
            "game_name": "Fortnite",
            "title": "Live now",
            "chatters": ["viewer_a", "viewer_b", "viewer_c"],
        }
        with open(logs_dir / "snapshot_live.json", "w") as f:
            json.dump(live_snap, f)

        # VOD snapshot (overlaps 1 viewer with live)
        vod_dir = logs_dir / "vod_snapshots"
        vod_dir.mkdir()
        vod_snap = {
            "channel": "vod_channel",
            "timestamp": "2025-03-01T09:00:00",
            "viewer_count": 500,
            "game_name": "Fortnite",
            "title": "VOD replay",
            "chatters": ["viewer_b", "viewer_d"],
            "_source": "vod",
        }
        with open(vod_dir / "snapshot_vod.json", "w") as f:
            json.dump(vod_snap, f)

        return logs_dir

    def test_load_all_counts_both_sources(self, mixed_logs_dir):
        agg = DataAggregator(str(mixed_logs_dir))
        agg.storage = None
        json_count, csv_count, vod_count, parquet_count = agg.load_all()
        assert json_count == 1
        assert vod_count == 1

    def test_both_channels_in_viewer_map(self, mixed_logs_dir):
        agg = DataAggregator(str(mixed_logs_dir))
        agg.storage = None
        agg.load_all()
        viewers = agg.get_channel_viewers()
        assert "live_channel" in viewers
        assert "vod_channel" in viewers

    def test_source_counts_separate(self, mixed_logs_dir):
        agg = DataAggregator(str(mixed_logs_dir))
        agg.storage = None
        agg.load_all()
        assert agg.snapshot_source_counts.get("live", 0) == 1
        assert agg.snapshot_source_counts.get("vod", 0) == 1

    def test_mixed_graph_edge_on_shared_viewer(self, mixed_logs_dir):
        """Live and VOD channels sharing a viewer should produce a graph edge."""
        agg = DataAggregator(str(mixed_logs_dir))
        agg.storage = None
        agg.load_all()
        viewers = agg.get_channel_viewers()

        builder = GraphBuilder(overlap_threshold=1)
        g = builder.build_graph(viewers)

        # live_channel and vod_channel share viewer_b => weight 1
        assert g.has_edge("live_channel", "vod_channel")
        assert g["live_channel"]["vod_channel"]["weight"] == 1

    def test_mixed_pipeline_full_run(self, mixed_logs_dir):
        """Full pipeline run on mixed live+VOD data produces valid community output."""
        if not LOUVAIN_AVAILABLE:
            pytest.skip("python-louvain not installed")

        agg = DataAggregator(str(mixed_logs_dir))
        agg.storage = None
        agg.load_all()

        viewers = agg.get_channel_viewers()
        metadata = agg.get_channel_metadata()

        builder = GraphBuilder(overlap_threshold=1)
        g = builder.build_graph(viewers, metadata)

        detector = CommunityDetector(resolution=1.0)
        partition = detector.detect_communities(g)

        assert len(partition) == 2  # live_channel and vod_channel
        assert "live_channel" in partition
        assert "vod_channel" in partition


# ═══════════════════════════════════════════════════════════════════════════════
# Analysis Prerequisite Validation Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestAnalysisInputValidation:
    """The analyze gate must accept exactly what collection writes.

    Live EventSub collection writes Parquet under raw/snapshots/, so a
    JSON-only check rejects a perfectly healthy dataset.
    """

    def _runner(self, storage, storage_type="file", logs_dir="logs"):
        from main import PipelineRunner

        config = PipelineConfig(
            analysis=AnalysisConfig(logs_dir=logs_dir, output_dir=logs_dir),
            storage_type=storage_type,
            s3_bucket="test-bucket" if storage_type == "s3" else None,
        )
        with patch("main.get_storage", return_value=storage):
            return PipelineRunner(config)

    def test_s3_parquet_only_dataset_is_accepted(self):
        storage = MockS3Storage(parquet_data={"raw/snapshots/2026/03/11/cycle_1.parquet": b"x"})
        runner = self._runner(storage, storage_type="s3")
        assert runner._validate_analysis_inputs() is True

    def test_s3_empty_dataset_is_rejected(self):
        runner = self._runner(MockS3Storage(), storage_type="s3")
        assert runner._validate_analysis_inputs() is False

    def test_s3_empty_dataset_allowed_when_data_not_required(self):
        runner = self._runner(MockS3Storage(), storage_type="s3")
        assert runner._validate_analysis_inputs(require_data=False) is True

    def test_s3_vod_only_dataset_is_accepted(self):
        storage = MockS3Storage(vod_snapshots=[{"channel": "a", "chatters": ["u1"]}])
        runner = self._runner(storage, storage_type="s3")
        assert runner._validate_analysis_inputs() is True

    def test_local_nested_parquet_dataset_is_accepted(self, tmp_path):
        from storage import FileStorage

        snapshot_dir = tmp_path / "raw" / "snapshots" / "2026" / "03" / "11"
        snapshot_dir.mkdir(parents=True)
        (snapshot_dir / "cycle_20260311_120000.parquet").write_bytes(b"placeholder")

        runner = self._runner(
            FileStorage(base_dir=str(tmp_path)),
            storage_type="file",
            logs_dir=str(tmp_path),
        )
        assert runner._validate_analysis_inputs() is True

    def test_local_legacy_flat_json_dataset_is_accepted(self, tmp_path):
        from storage import FileStorage

        (tmp_path / "snapshot.json").write_text('{"channel": "a", "chatters": ["u1"]}')

        runner = self._runner(
            FileStorage(base_dir=str(tmp_path)),
            storage_type="file",
            logs_dir=str(tmp_path),
        )
        assert runner._validate_analysis_inputs() is True

    def test_local_empty_dataset_is_rejected(self, tmp_path):
        from storage import FileStorage

        runner = self._runner(
            FileStorage(base_dir=str(tmp_path)),
            storage_type="file",
            logs_dir=str(tmp_path),
        )
        assert runner._validate_analysis_inputs() is False


class TestGraphCsvUpload:
    """Graph CSV exports must reach storage with (key, file_path) in that order."""

    def test_export_uploads_csvs_to_storage(self, tmp_path):
        from storage import FileStorage

        source_dir = tmp_path / "out"
        source_dir.mkdir()
        nodes_csv = source_dir / "graph_nodes.csv"
        nodes_csv.write_text("channel,viewers\na,1\n")

        storage = FileStorage(base_dir=str(tmp_path / "store"))
        assert storage.upload_file("curated/analysis/2026-03-11/graph_nodes.csv", str(nodes_csv)) is True

        landed = tmp_path / "store" / "curated" / "analysis" / "2026-03-11" / "graph_nodes.csv"
        assert landed.exists()
        assert landed.read_text() == "channel,viewers\na,1\n"


class TestScheduledAnalysisOutcome:
    """A scheduled task must fail closed when analysis or persistence fails."""

    @staticmethod
    def _runner_with_storage(storage):
        from main import PipelineRunner

        runner = object.__new__(PipelineRunner)
        runner.config = PipelineConfig(
            analysis=AnalysisConfig(
                output_dir="out",
                enable_static_viz=False,
                enable_interactive_viz=False,
                export_graph_csv=False,
            )
        )
        runner.storage = storage
        runner.logger = MagicMock()
        return runner

    @staticmethod
    def _save_args():
        graph = nx.Graph()
        graph.add_node("channel-a", viewer_count=10)
        aggregator = MagicMock()
        aggregator.get_statistics.return_value = {
            "total_channels": 1,
            "total_unique_viewers_across_all": 1,
        }
        return {
            "partition": {"channel-a": 0},
            "labels": {0: "Test"},
            "graph": graph,
            "aggregator": aggregator,
            "detection_stats": {"num_communities": 1, "modularity": 0.0},
            "tagging_stats": {},
            "communities": {0: ["channel-a"]},
        }

    def test_private_analysis_result_write_failure_is_fatal(self):
        class FailedStorage(MockS3Storage):
            def upload_json(self, key, data, **kwargs):
                return False

        runner = self._runner_with_storage(FailedStorage())

        with pytest.raises(IOError, match="private analysis results"):
            runner._step_save_results(**self._save_args())

    def test_public_frontend_write_failure_is_fatal(self):
        runner = self._runner_with_storage(MockS3Storage())

        with patch("main.export_frontend_data", return_value=False):
            with pytest.raises(IOError, match="public frontend data"):
                runner._step_save_results(**self._save_args())

    def test_mode_analyze_reports_success_and_failure_milestones(self, caplog):
        import main as app_main

        caplog.set_level("INFO")

        successful = MagicMock()
        successful._validate_prerequisites.return_value = True
        successful.run_analysis_pipeline.return_value = {
            "status": "success",
            "num_channels": 2,
            "num_communities": 1,
            "num_edges": 1,
        }
        failed = MagicMock()
        failed._validate_prerequisites.return_value = True
        failed.run_analysis_pipeline.return_value = {"status": "error"}

        with patch("main.PipelineRunner", return_value=successful):
            assert asyncio.run(app_main.mode_analyze(PipelineConfig())) is True
        with patch("main.PipelineRunner", return_value=failed):
            assert asyncio.run(app_main.mode_analyze(PipelineConfig())) is False

        assert "ANALYSIS_COMPLETED" in caplog.text
        assert "ANALYSIS_FAILED" in caplog.text
