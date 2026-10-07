from cluster_tagger import ClusterTagger
from community_detector import CommunityDetector
from data_aggregator import DataAggregator
from graph_builder import GraphBuilder


class TestIntegration:
    """End-to-end test of the analysis pipeline with fixture data."""

    def test_full_pipeline(self, tmp_logs_dir, tmp_path):
        """Aggregate, build the graph, detect and label communities, export."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        aggregator = DataAggregator(str(tmp_logs_dir))
        aggregator.storage = None
        json_count, csv_count, vod_count, parquet_count = aggregator.load_all()
        assert json_count == 5

        channel_viewers = aggregator.get_channel_viewers()
        channel_metadata = aggregator.get_channel_metadata()
        assert len(channel_viewers) == 5

        builder = GraphBuilder(overlap_threshold=1)
        graph = builder.build_graph(channel_viewers, channel_metadata)
        assert graph.number_of_nodes() == 5
        assert graph.number_of_edges() > 0

        detector = CommunityDetector(resolution=1.0)
        partition = detector.detect_communities(graph)
        communities = detector.get_communities()
        assert len(partition) == 5
        assert len(communities) >= 1

        tagger = ClusterTagger()
        labels = tagger.tag_communities(communities, channel_metadata)
        assert len(labels) == len(communities)

        valorant_channels = {"streamer_a", "streamer_b", "streamer_d"}
        valorant_comms = {partition[ch] for ch in valorant_channels}
        assert len(valorant_comms) <= 2

        builder.export_nodes_csv(str(output_dir / "nodes.csv"))
        builder.export_edges_csv(str(output_dir / "edges.csv"))
        assert (output_dir / "nodes.csv").exists()
        assert (output_dir / "edges.csv").exists()
