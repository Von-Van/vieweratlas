import json

import networkx as nx
import pytest

from frontend_exporter import (
    FrontendExportConfig,
    export_frontend_data,
    export_pending_frontend_data,
)
from graph_builder import GraphBuilder
from survey_fakes import InMemoryStorage


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
        storage = InMemoryStorage()

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

    @staticmethod
    def _uneven_viewers():
        # `small` sits entirely inside `big`; `peer` shares half of `big`.
        return {
            "big": {f"u{i}" for i in range(1000)},
            "small": {f"u{i}" for i in range(100)},
            "peer": {f"u{i}" for i in range(500, 1500)},
        }

    def _export_single_community(self, graph, viewers, config=None):
        storage = InMemoryStorage()
        assert export_frontend_data(
            graph=graph,
            partition={name: 0 for name in viewers},
            communities={0: set(viewers)},
            labels={0: "Test"},
            detection_stats={"modularity": 0.0},
            aggregator_stats={},
            storage=storage,
            config=config,
        )
        return storage._json_uploads["data/frontend-data.json"]

    @pytest.mark.parametrize("mode", ["jaccard", "overlap_coef"])
    def test_normalised_modes_publish_measured_counts(self, mode):
        """The graph weight is a 0-1 score in these modes, but the payload
        promises the measured shared-chatter count. Truncating the score to an
        integer used to publish 0 for every edge."""
        viewers = self._uneven_viewers()
        graph = GraphBuilder(overlap_threshold=1, weighting_mode=mode).build_graph(viewers)
        payload = self._export_single_community(graph, viewers)

        published = {
            frozenset((edge["source"], edge["target"])): edge["weight"]
            for edge in payload["edges"]
        }
        assert published == {
            frozenset(("big", "peer")): 500,
            frozenset(("big", "small")): 100,
        }
        assert payload["overallStats"]["avgOverlapWeight"] == 300

    def test_public_cap_ranks_edges_by_the_analysis_weight(self):
        """With one edge per channel, overlap_coef keeps the containment pair
        (score 1.0, 100 shared) over the larger raw count (score 0.5, 500)."""
        viewers = self._uneven_viewers()
        graph = GraphBuilder(
            overlap_threshold=1, weighting_mode="overlap_coef"
        ).build_graph(viewers)
        payload = self._export_single_community(
            graph, viewers, FrontendExportConfig(top_edges_per_channel=1)
        )

        assert [
            (sorted((edge["source"], edge["target"])), edge["weight"])
            for edge in payload["edges"]
        ] == [(["big", "small"], 100)]

    def test_export_makes_duplicate_community_labels_unique(self):
        graph = nx.Graph()
        graph.add_node("a", viewer_count=100, viewers=10, game_name="Game", language="en")
        graph.add_node("b", viewer_count=90, viewers=9, game_name="Game", language="en")
        graph.add_edge("a", "b", weight=5)

        storage = InMemoryStorage()
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
        storage = InMemoryStorage()
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
        storage = InMemoryStorage()
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
