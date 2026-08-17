"""
Graph Builder Module

Constructs a network graph from aggregated viewer data.
Creates weighted edges between channels based on shared viewers (overlap).
Applies threshold filtering to focus on meaningful connections.
"""

import networkx as nx
from collections import defaultdict
from typing import Dict, Set, Tuple, List
from itertools import combinations
import logging

logger = logging.getLogger(__name__)


class GraphBuilder:
    """
    Builds a weighted undirected graph where:
    - Nodes: Streamer channels
    - Edges: Represent shared viewers between channels
    - Edge Weight: Number of shared viewers
    """
    
    #: Weight formulas. ``shared_count`` is the raw intersection size; the others
    #: normalise it so channels sampled at different depths stay comparable.
    WEIGHTING_MODES = ("shared_count", "jaccard", "overlap_coef")

    def __init__(self, overlap_threshold: int = 1, max_viewer_channel_degree: int = 200,
                 include_isolated_nodes: bool = True,
                 weighting_mode: str = "shared_count",
                 normalized_overlap_threshold: float = 0.0):
        """
        Initialize graph builder.

        Args:
            overlap_threshold: Minimum shared chatters required for an edge.
                Applies to the raw intersection in every mode.
            max_viewer_channel_degree: Ignore viewers seen in more than this many
                channels. Very high-degree viewer IDs are usually noisy and create
                a combinatorial number of weak edges.
            include_isolated_nodes: Keep channels that share no viewers with any
                other channel. Set False to drop them from the graph entirely.
            weighting_mode: One of WEIGHTING_MODES. A raw count rewards channels
                that were simply sampled more often, so ``jaccard`` and
                ``overlap_coef`` divide it by union / smaller-set size instead.
            normalized_overlap_threshold: Minimum normalised score (0-1) for an
                edge. Ignored by ``shared_count``.
        """
        if weighting_mode not in self.WEIGHTING_MODES:
            raise ValueError(
                f"weighting_mode must be one of {self.WEIGHTING_MODES}"
            )
        if not 0.0 <= normalized_overlap_threshold <= 1.0:
            raise ValueError("normalized_overlap_threshold must be between 0 and 1")

        self.overlap_threshold = overlap_threshold
        self.max_viewer_channel_degree = max_viewer_channel_degree
        self.include_isolated_nodes = include_isolated_nodes
        self.weighting_mode = weighting_mode
        self.normalized_overlap_threshold = normalized_overlap_threshold
        self.graph = nx.Graph()
        self.overlap_data: Dict[Tuple[str, str], int] = {}
        self.skipped_high_degree_viewers = 0
        self.skipped_below_normalized_threshold = 0
        
    def _normalized_score(self, overlap: int, size1: int, size2: int) -> float:
        """Similarity in 0-1 for the configured mode.

        jaccard      -- |A n B| / |A u B|; penalises pairs of very different size.
        overlap_coef -- |A n B| / min(|A|,|B|); asks how much of the *smaller*
                        audience is shared, so a small channel can still score
                        highly against a large one.
        """
        if overlap <= 0 or size1 <= 0 or size2 <= 0:
            return 0.0
        if self.weighting_mode == "overlap_coef":
            return overlap / min(size1, size2)
        union = size1 + size2 - overlap
        return overlap / union if union > 0 else 0.0

    def build_graph(self,
                   channel_viewers: Dict[str, Set[str]],
                   channel_metadata: Dict[str, dict] = None) -> nx.Graph:
        """
        Build the overlap graph from viewer data.
        
        Args:
            channel_viewers: Dict mapping channel -> set of viewers
            channel_metadata: Optional dict with channel metadata (game, viewers, etc.)
        
        Returns:
            NetworkX graph with nodes (channels) and weighted edges (overlaps)
        """
        self.graph = nx.Graph()
        self.overlap_data = {}
        self.skipped_high_degree_viewers = 0
        
        channels = list(channel_viewers.keys())
        logger.info(f"Building graph with {len(channels)} channels")
        
        # Add nodes with metadata
        for channel in channels:
            attributes = {"viewers": len(channel_viewers[channel])}
            
            if channel_metadata and channel in channel_metadata:
                meta = channel_metadata[channel]
                attributes.update({
                    "viewer_count": meta.get("viewer_count", meta.get("viewers", 0)),
                    "game_name": meta.get("game_name", meta.get("game", "Unknown")),
                    "language": meta.get("language", ""),
                    "title": meta.get("title", ""),
                })
            
            self.graph.add_node(channel, **attributes)
        
        viewer_channels: Dict[str, List[str]] = defaultdict(list)
        for channel, viewers in channel_viewers.items():
            for viewer in viewers:
                viewer_channels[viewer].append(channel)

        overlap_counts: Dict[Tuple[str, str], int] = defaultdict(int)
        for viewer, viewer_channel_list in viewer_channels.items():
            if len(viewer_channel_list) < 2:
                continue
            if len(viewer_channel_list) > self.max_viewer_channel_degree:
                self.skipped_high_degree_viewers += 1
                continue

            for channel1, channel2 in combinations(sorted(viewer_channel_list), 2):
                overlap_counts[(channel1, channel2)] += 1

        self.skipped_below_normalized_threshold = 0
        for (channel1, channel2), overlap in overlap_counts.items():
            if overlap < self.overlap_threshold:
                continue

            size1 = len(channel_viewers[channel1])
            size2 = len(channel_viewers[channel2])
            score = self._normalized_score(overlap, size1, size2)
            if (self.weighting_mode != "shared_count"
                    and score < self.normalized_overlap_threshold):
                self.skipped_below_normalized_threshold += 1
                continue

            # ``weight`` drives Louvain and thresholding; ``shared`` is always the
            # measured intersection so the public export stays a real count.
            weight = overlap if self.weighting_mode == "shared_count" else score
            self.graph.add_edge(
                channel1, channel2, weight=weight, shared=overlap, similarity=score
            )
            self.overlap_data[(channel1, channel2)] = overlap
        
        if not self.include_isolated_nodes:
            isolated = list(nx.isolates(self.graph))
            if isolated:
                self.graph.remove_nodes_from(isolated)
                logger.info("Dropped %d isolated channels (no shared viewers)", len(isolated))

        logger.info(f"Created graph with {self.graph.number_of_nodes()} nodes "
                   f"and {self.graph.number_of_edges()} edges "
                   f"(threshold: {self.overlap_threshold})")
        if self.skipped_high_degree_viewers:
            logger.info(
                "Skipped %d high-degree viewer IDs (>%d channels)",
                self.skipped_high_degree_viewers,
                self.max_viewer_channel_degree,
            )
        
        return self.graph
    
    def apply_threshold(self, threshold: int) -> nx.Graph:
        """
        Remove edges below a new threshold and update the graph.
        
        Args:
            threshold: Minimum edge weight to keep
        
        Returns:
            Updated graph with threshold applied
        """
        edges_to_remove = []
        for u, v, data in self.graph.edges(data=True):
            if data['weight'] < threshold:
                edges_to_remove.append((u, v))
        
        self.graph.remove_edges_from(edges_to_remove)
        self.overlap_threshold = threshold
        
        logger.info(f"Applied threshold {threshold}. "
                   f"Graph now has {self.graph.number_of_edges()} edges")
        
        return self.graph
    
    def get_graph(self) -> nx.Graph:
        """
        Get the current graph object.
        
        Returns:
            NetworkX graph
        """
        return self.graph
    
    def get_statistics(self) -> dict:
        """
        Get graph statistics.
        
        Returns:
            Dict with graph metrics
        """
        nodes = self.graph.number_of_nodes()
        edges = self.graph.number_of_edges()
        
        if edges == 0:
            avg_weight = 0
            max_weight = 0
        else:
            weights = [data['weight'] for u, v, data in self.graph.edges(data=True)]
            avg_weight = sum(weights) / len(weights)
            max_weight = max(weights)
        
        # Identify isolated nodes
        isolated = list(nx.isolates(self.graph))
        
        # Get degree centrality (which channels have most connections)
        degree_centrality = nx.degree_centrality(self.graph)
        top_connected = sorted(degree_centrality.items(), 
                              key=lambda x: x[1], reverse=True)[:10]
        
        return {
            "num_nodes": nodes,
            "num_edges": edges,
            "avg_edge_weight": avg_weight,
            "max_edge_weight": max_weight,
            "num_isolated_nodes": len(isolated),
            "density": nx.density(self.graph),
            "top_connected_channels": top_connected,
            "skipped_high_degree_viewers": self.skipped_high_degree_viewers,
            "max_viewer_channel_degree": self.max_viewer_channel_degree
        }
    
    def get_largest_component(self) -> nx.Graph:
        """
        Get the largest connected component of the graph.
        Useful for focusing analysis on the main network.
        
        Returns:
            Subgraph containing only the largest connected component
        """
        if self.graph.number_of_nodes() == 0:
            return self.graph.copy()
        
        largest_cc = max(nx.connected_components(self.graph), key=len)
        return self.graph.subgraph(largest_cc).copy()
    
    def export_edges_csv(self, filename: str) -> None:
        """
        Export edges to CSV format for external tools (e.g., Gephi).
        
        CSV format: source,target,weight
        
        Args:
            filename: Path to output CSV file
        """
        with open(filename, 'w') as f:
            f.write("source,target,weight\n")
            for u, v, data in self.graph.edges(data=True):
                weight = data['weight']
                f.write(f"{u},{v},{weight}\n")
        
        logger.info(f"Exported edges to {filename}")
    
    def export_nodes_csv(self, filename: str) -> None:
        """
        Export nodes with attributes to CSV format for external tools.
        
        CSV format: id,viewers,viewer_count,game,title
        
        Args:
            filename: Path to output CSV file
        """
        with open(filename, 'w') as f:
            f.write("id,viewers,viewer_count,game,title\n")
            for node, attrs in self.graph.nodes(data=True):
                viewers = attrs.get('viewers', 0)
                viewer_count = attrs.get('viewer_count', 0)
                game = attrs.get('game_name', attrs.get('game', 'Unknown')).replace(',', ';')
                title = attrs.get('title', '').replace(',', ';')
                
                f.write(f"{node},{viewers},{viewer_count},{game},{title}\n")
        
        logger.info(f"Exported nodes to {filename}")
    
    def get_channel_neighbors(self, channel: str) -> List[Tuple[str, int]]:
        """
        Get all channels connected to a given channel, sorted by overlap.
        
        Args:
            channel: Channel name
        
        Returns:
            List of (neighbor_channel, overlap_count) tuples, sorted descending
        """
        if channel not in self.graph:
            return []
        
        neighbors = []
        for neighbor in self.graph.neighbors(channel):
            weight = self.graph[channel][neighbor]['weight']
            neighbors.append((neighbor, weight))
        
        return sorted(neighbors, key=lambda x: x[1], reverse=True)


if __name__ == "__main__":
    # Test with sample data
    from data_aggregator import DataAggregator
    
    logging.basicConfig(level=logging.INFO)
    
    # Load data
    aggregator = DataAggregator("logs")
    aggregator.load_all()
    
    channel_viewers = aggregator.get_channel_viewers()
    channel_metadata = aggregator.get_channel_metadata()
    
    # Build graph
    builder = GraphBuilder(overlap_threshold=1)
    graph = builder.build_graph(channel_viewers, channel_metadata)
    
    # Print stats
    print("\nGraph Statistics:")
    stats = builder.get_statistics()
    for key, value in stats.items():
        print(f"  {key}: {value}")
    
    # Export for external tools
    builder.export_nodes_csv("nodes.csv")
    builder.export_edges_csv("edges.csv")
    print("\nExported nodes.csv and edges.csv")
