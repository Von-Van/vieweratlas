import pytest

from graph_builder import GraphBuilder


class TestGraphBuilder:
    """Tests for overlap graph construction and properties."""

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
