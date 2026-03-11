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
from pathlib import Path
from collections import defaultdict

import pytest
import networkx as nx
from unittest.mock import patch, MagicMock

# Ensure src/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from data_aggregator import DataAggregator
from graph_builder import GraphBuilder
from community_detector import CommunityDetector, LOUVAIN_AVAILABLE
from cluster_tagger import ClusterTagger
from config import (
    CollectionConfig,
    AnalysisConfig,
    VODConfig,
    SQSConfig,
    PipelineConfig,
    get_default_config,
    get_rigorous_config,
    get_exploratory_config,
    get_debug_config,
    load_config_from_yaml,
)
from daily_collection_state import DynamoDBCollectionState
from sqs_task_queue import SQSTaskQueue, ChannelTask
from discovery import run_discovery
from worker import _collect_channel, _flush_parquet


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

    def test_rigorous_config_has_high_threshold(self):
        config = get_rigorous_config()
        assert config.analysis.overlap_threshold == 300
        assert config.analysis.min_community_size == 10

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
            "analysis:\n"
            "  overlap_threshold: 5\n"
            "  resolution: 1.5\n"
            "  min_community_size: 3\n"
        )
        config = load_config_from_yaml(str(yaml_file))
        assert config.collection.batch_size == 50
        assert config.collection.top_channels_limit == 200
        assert config.collection.collection_interval_minutes == 30
        assert config.analysis.overlap_threshold == 5
        assert config.analysis.resolution == 1.5
        assert config.analysis.min_community_size == 3

    def test_yaml_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_config_from_yaml(str(tmp_path / "nonexistent.yaml"))

    def test_default_weighting_mode_is_shared_count(self):
        config = get_default_config()
        assert config.analysis.weighting_mode == "shared_count"

    def test_invalid_weighting_mode_raises(self):
        with pytest.raises(ValueError):
            AnalysisConfig(weighting_mode="jaccard")

    def test_yaml_loading_weighting_mode(self, tmp_path):
        yaml_file = tmp_path / "wm_config.yaml"
        yaml_file.write_text(
            "analysis:\n"
            "  overlap_threshold: 1\n"
            '  weighting_mode: "shared_count"\n'
        )
        config = load_config_from_yaml(str(yaml_file))
        assert config.analysis.weighting_mode == "shared_count"


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
        if key.startswith("raw/snapshots/snap_"):
            idx = int(key.split("_")[-1].replace(".json", ""))
            return self._live[idx]
        if key.startswith("curated/presence_snapshots/source=vod/snap_"):
            idx = int(key.split("_")[-1].replace(".json", ""))
            return self._vod_json[idx]
        return None

    def upload_parquet(self, key, data, **kwargs):
        self._parquet[key] = data
        return True

    def download_parquet(self, key):
        return self._parquet.get(key)

    def get_uri(self, key):
        return f"mock://{key}"


class TestS3Integration:
    """Tests for S3 storage backend path through DataAggregator (mocked)."""

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
# DynamoDB Collection State Tests
# ═══════════════════════════════════════════════════════════════════════════════


class MockDynamoDBTable:
    """In-memory mock of a DynamoDB Table resource for testing."""

    def __init__(self):
        self._items = {}

    def get_item(self, Key, ConsistentRead=False):
        pk = Key["pk"]
        if pk in self._items:
            return {"Item": self._items[pk]}
        return {}

    def put_item(self, Item, ConditionExpression=None):
        pk = Item["pk"]
        if ConditionExpression == "attribute_not_exists(pk)" and pk in self._items:
            from botocore.exceptions import ClientError
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}},
                "PutItem",
            )
        self._items[pk] = Item


class MockDynamoDBResource:
    """Mock boto3 DynamoDB resource that returns MockDynamoDBTable."""

    def __init__(self):
        self._tables = {}

    def Table(self, name):
        if name not in self._tables:
            self._tables[name] = MockDynamoDBTable()
        return self._tables[name]


class TestDynamoDBCollectionState:

    @pytest.fixture
    def dynamo_state(self):
        resource = MockDynamoDBResource()
        return DynamoDBCollectionState(
            table_name="test-state",
            dynamodb_resource=resource,
            ttl_days=32,
        )

    def test_has_collected_returns_false_initially(self, dynamo_state):
        assert dynamo_state.has_collected("live", "xqc", "2026-03-10") is False

    def test_mark_collected_returns_true_first_time(self, dynamo_state):
        assert dynamo_state.mark_collected("live", "xqc", "2026-03-10") is True

    def test_mark_collected_returns_false_second_time(self, dynamo_state):
        dynamo_state.mark_collected("live", "xqc", "2026-03-10")
        assert dynamo_state.mark_collected("live", "xqc", "2026-03-10") is False

    def test_has_collected_returns_true_after_mark(self, dynamo_state):
        dynamo_state.mark_collected("live", "xqc", "2026-03-10")
        assert dynamo_state.has_collected("live", "xqc", "2026-03-10") is True

    def test_different_days_are_independent(self, dynamo_state):
        dynamo_state.mark_collected("live", "xqc", "2026-03-10")
        assert dynamo_state.has_collected("live", "xqc", "2026-03-11") is False

    def test_different_sources_are_independent(self, dynamo_state):
        dynamo_state.mark_collected("live", "xqc", "2026-03-10")
        assert dynamo_state.has_collected("vod", "xqc", "2026-03-10") is False

    def test_channel_name_normalized_to_lowercase(self, dynamo_state):
        dynamo_state.mark_collected("live", "XQC", "2026-03-10")
        assert dynamo_state.has_collected("live", "xqc", "2026-03-10") is True

    def test_pk_format(self):
        pk = DynamoDBCollectionState._make_pk("live", "XQC", "2026-03-10")
        assert pk == "live#xqc#2026-03-10"

    def test_ttl_is_set_in_item(self):
        resource = MockDynamoDBResource()
        state = DynamoDBCollectionState(
            table_name="test-state",
            dynamodb_resource=resource,
            ttl_days=32,
        )
        state.mark_collected("live", "xqc", "2026-03-10")
        table = resource._tables["test-state"]
        item = table._items["live#xqc#2026-03-10"]
        assert "ttl" in item
        assert isinstance(item["ttl"], int)
        assert item["ttl"] > 0


# ═══════════════════════════════════════════════════════════════════════════════
# SQS Config Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestSQSConfig:

    def test_default_disabled(self):
        cfg = SQSConfig()
        assert cfg.enabled is False

    def test_enabled_requires_queue_url(self):
        with pytest.raises(ValueError, match="queue_url required"):
            SQSConfig(enabled=True)

    def test_enabled_with_queue_url(self):
        cfg = SQSConfig(enabled=True, queue_url="https://sqs.us-east-1.amazonaws.com/123/queue.fifo")
        assert cfg.enabled is True

    def test_pipeline_config_includes_sqs(self):
        cfg = PipelineConfig()
        assert cfg.sqs is not None
        assert isinstance(cfg.sqs, SQSConfig)
        assert cfg.sqs.enabled is False

    def test_env_override(self):
        with patch.dict(os.environ, {"SQS_ENABLED": "true", "SQS_CHANNEL_QUEUE_URL": "https://sqs.example.com/q.fifo"}):
            cfg = SQSConfig()
            assert cfg.enabled is True
            assert cfg.queue_url == "https://sqs.example.com/q.fifo"

    def test_dynamodb_table_env_override(self):
        with patch.dict(os.environ, {"DYNAMODB_STATE_TABLE": "my-custom-table"}):
            cfg = SQSConfig()
            assert cfg.dynamodb_state_table == "my-custom-table"


# ═══════════════════════════════════════════════════════════════════════════════
# SQS Task Queue Tests
# ═══════════════════════════════════════════════════════════════════════════════


class MockSQSClient:
    """In-memory mock of boto3 SQS client."""

    def __init__(self):
        self._messages = []  # list of {"Body": str, "MessageId": str, "ReceiptHandle": str}
        self._next_id = 0

    def send_message_batch(self, QueueUrl, Entries):
        successful = []
        for entry in Entries:
            self._next_id += 1
            msg_id = f"msg-{self._next_id}"
            self._messages.append({
                "Body": entry["MessageBody"],
                "MessageId": msg_id,
                "ReceiptHandle": f"rh-{msg_id}",
            })
            successful.append({"Id": entry["Id"], "MessageId": msg_id})
        return {"Successful": successful, "Failed": []}

    def receive_message(self, QueueUrl, MaxNumberOfMessages=1, VisibilityTimeout=900, WaitTimeSeconds=0):
        batch = self._messages[:MaxNumberOfMessages]
        self._messages = self._messages[MaxNumberOfMessages:]
        return {"Messages": batch} if batch else {}

    def delete_message(self, QueueUrl, ReceiptHandle):
        return {}


class TestChannelTask:

    def test_round_trip_json(self):
        task = ChannelTask(channel="xqc", cycle_id="2026-03-11T14:00:00", utc_day="2026-03-11")
        restored = ChannelTask.from_json(task.to_json())
        assert restored.channel == "xqc"
        assert restored.cycle_id == "2026-03-11T14:00:00"
        assert restored.utc_day == "2026-03-11"


class TestSQSTaskQueue:

    @pytest.fixture
    def queue(self):
        client = MockSQSClient()
        return SQSTaskQueue(queue_url="https://sqs.example.com/q.fifo", sqs_client=client), client

    def test_publish_and_receive(self, queue):
        q, client = queue
        tasks = [
            ChannelTask(channel="xqc", cycle_id="c1", utc_day="2026-03-11"),
            ChannelTask(channel="shroud", cycle_id="c1", utc_day="2026-03-11"),
        ]
        sent = q.publish_tasks(tasks)
        assert sent == 2

        received = q.receive_tasks(max_messages=10)
        assert len(received) == 2
        assert received[0]["task"].channel == "xqc"
        assert received[1]["task"].channel == "shroud"

    def test_receive_empty_queue(self, queue):
        q, _ = queue
        received = q.receive_tasks(max_messages=10)
        assert received == []

    def test_delete_task(self, queue):
        q, client = queue
        q.publish_tasks([ChannelTask(channel="a", cycle_id="c1", utc_day="d1")])
        received = q.receive_tasks(max_messages=1)
        assert q.delete_task(received[0]["receipt_handle"]) is True

    def test_publish_large_batch(self, queue):
        """Batches of >10 are split into sub-batches of 10."""
        q, client = queue
        tasks = [ChannelTask(channel=f"ch{i}", cycle_id="c1", utc_day="d1") for i in range(25)]
        sent = q.publish_tasks(tasks)
        assert sent == 25
        assert len(client._messages) == 25


# ═══════════════════════════════════════════════════════════════════════════════
# Discovery Service Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestDiscovery:

    def test_run_discovery_publishes_new_channels(self):
        """Discovery should publish tasks for channels not yet collected."""
        sqs_client = MockSQSClient()
        dynamo_resource = MockDynamoDBResource()

        with patch("discovery.fetch_top_channels", return_value=["ch_a", "ch_b", "ch_c"]):
            result = run_discovery(
                channel_limit=100,
                queue_url="https://sqs.example.com/q.fifo",
                dynamodb_table="test-state",
                sqs_client=sqs_client,
                dynamodb_resource=dynamo_resource,
            )

        assert result["discovered"] == 3
        assert result["skipped"] == 0
        assert result["published"] == 3
        assert len(sqs_client._messages) == 3

    def test_run_discovery_skips_already_collected(self):
        """Channels already in DynamoDB should be skipped."""
        sqs_client = MockSQSClient()
        dynamo_resource = MockDynamoDBResource()

        # Pre-mark ch_b as collected
        state = DynamoDBCollectionState(
            table_name="test-state",
            dynamodb_resource=dynamo_resource,
        )
        from datetime import datetime, timezone
        utc_day = datetime.now(timezone.utc).date().isoformat()
        state.mark_collected("live", "ch_b", utc_day)

        with patch("discovery.fetch_top_channels", return_value=["ch_a", "ch_b", "ch_c"]):
            result = run_discovery(
                channel_limit=100,
                queue_url="https://sqs.example.com/q.fifo",
                dynamodb_table="test-state",
                sqs_client=sqs_client,
                dynamodb_resource=dynamo_resource,
            )

        assert result["discovered"] == 3
        assert result["skipped"] == 1
        assert result["published"] == 2


# ═══════════════════════════════════════════════════════════════════════════════
# Worker Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestWorkerHelpers:

    def test_collect_channel_returns_row_on_success(self):
        mock_info = {
            "viewer_count": 1500,
            "game_name": "Valorant",
            "title": "Ranked",
            "started_at": "2026-03-11T12:00:00Z",
            "language": "en",
        }
        with patch("worker.requests") as mock_requests:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"data": [mock_info]}
            mock_resp.raise_for_status = MagicMock()
            mock_requests.get.return_value = mock_resp
            row = _collect_channel("xqc", "client123", "oauth123")

        assert row is not None
        assert row["channel"] == "xqc"
        assert row["viewer_count"] == 1500
        assert row["game_name"] == "Valorant"

    def test_collect_channel_returns_none_when_offline(self):
        with patch("worker.requests") as mock_requests:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"data": []}
            mock_resp.raise_for_status = MagicMock()
            mock_requests.get.return_value = mock_resp
            row = _collect_channel("offline_ch", "client123", "oauth123")

        assert row is None

    def test_flush_parquet_writes_to_storage(self):
        rows = [
            {
                "channel": "test_ch",
                "timestamp": "2026-03-11T14:00:00",
                "viewer_count": 100,
                "game_name": "Test",
                "title": "Test stream",
                "started_at": "2026-03-11T12:00:00Z",
                "language": "en",
                "chatters_json": "[]",
            }
        ]
        storage = MockS3Storage()
        _flush_parquet(rows, storage, "20260311_140000")
        assert len(storage._parquet) == 1
        key = list(storage._parquet.keys())[0]
        assert key.startswith("raw/snapshots/")
        assert key.endswith(".parquet")
