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
from unittest.mock import patch

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
    PipelineConfig,
    get_default_config,
    get_rigorous_config,
    get_exploratory_config,
    get_debug_config,
    load_config_from_yaml,
)


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
        json_count, csv_count, vod_count = agg.load_all()
        assert json_count == 0
        assert csv_count == 0
        assert agg.get_channel_viewers() == {}

    def test_load_all_returns_tuple(self, tmp_logs_dir):
        agg = DataAggregator(str(tmp_logs_dir))
        agg.storage = None  # Force local filesystem
        result = agg.load_all()
        assert isinstance(result, tuple)
        assert len(result) == 3


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
            "ch1": {"game": "Valorant", "viewers": 100},
            "ch2": {"game": "Valorant", "viewers": 200},
            "ch3": {"game": "Valorant", "viewers": 150},
            "ch4": {"game": "Valorant", "viewers": 300},
            "ch5": {"game": "CS2", "viewers": 50},
        }
        tagger = ClusterTagger()
        labels = tagger.tag_communities(communities, metadata)
        assert "Valorant" in labels[0]

    def test_language_game_combo_label(self):
        """Community with clear language + game combo."""
        communities = {0: {"ch1", "ch2", "ch3", "ch4", "ch5"}}
        metadata = {
            "ch1": {"game": "Minecraft", "language": "es", "viewers": 100},
            "ch2": {"game": "Fortnite", "language": "es", "viewers": 200},
            "ch3": {"game": "Minecraft", "language": "es", "viewers": 150},
            "ch4": {"game": "Roblox", "language": "en", "viewers": 300},
            "ch5": {"game": "Minecraft", "language": "es", "viewers": 50},
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
            "ch1": {"game": "Valorant", "viewers": 100},
            "ch2": {"game": "Fortnite", "viewers": 200},
            "ch3": {"game": "Minecraft", "viewers": 150},
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
            "ch1": {"game": "Valorant", "viewers": 100},
            "ch2": {"game": "Valorant", "viewers": 200},
            "ch3": {"game": "LoL", "viewers": 300},
            "ch4": {"game": "LoL", "viewers": 400},
            "ch5": {"game": "Art", "viewers": 50},
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
            "ch1": {"game": "Valorant", "viewers": 100},
            "ch2": {"game": "Valorant", "viewers": 200},
            "ch3": {"game": "Valorant", "viewers": 150},
            "ch4": {"game": "Art", "viewers": 50},
            "ch5": {"game": "Music", "viewers": 50},
        }
        tagger = ClusterTagger()
        tagger.tag_communities(communities, metadata)
        stats = tagger.get_statistics()

        assert stats["total_labeled"] == 2
        assert stats["with_clear_game"] >= 1  # The Valorant community

    def test_get_label_reasoning(self):
        communities = {0: {"ch1", "ch2"}}
        metadata = {
            "ch1": {"game": "Valorant", "viewers": 100},
            "ch2": {"game": "Valorant", "viewers": 200},
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


# ═══════════════════════════════════════════════════════════════════════════════
# Integration Test
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.skipif(not LOUVAIN_AVAILABLE, reason="python-louvain not installed")
class TestIntegration:
    """End-to-end test of the analysis pipeline with fixture data."""

    @staticmethod
    def _normalize_metadata_for_tagger(raw_meta: dict) -> dict:
        """
        Bridge the key mismatch between DataAggregator output
        (game_name, viewer_count) and ClusterTagger input (game, viewers).
        This mirrors what the real pipeline should do.
        """
        normalized = {}
        for ch, meta in raw_meta.items():
            normalized[ch] = {
                "game": meta.get("game_name", meta.get("game", "Unknown")),
                "viewers": meta.get("viewer_count", meta.get("viewers", 0)),
                "language": meta.get("language", "Unknown"),
                "title": meta.get("title", ""),
            }
        return normalized

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
        json_count, csv_count, vod_count = aggregator.load_all()
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

        # 4. Tag communities (normalize metadata keys for tagger)
        tagger = ClusterTagger()
        tagger_meta = self._normalize_metadata_for_tagger(channel_metadata)
        labels = tagger.tag_communities(communities, tagger_meta)
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
