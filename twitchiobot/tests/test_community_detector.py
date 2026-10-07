import networkx as nx
import pytest

from community_detector import CommunityDetector


class TestCommunityDetector:
    """Tests for Louvain community detection."""

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

    def test_unknown_channel_returns_minus_one(self, graph):
        detector = CommunityDetector(resolution=1.0)
        detector.detect_communities(graph)
        assert detector.get_community_for_channel("nonexistent_channel") == -1

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
