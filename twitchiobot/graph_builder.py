"""
Graph Builder Module

Constructs a network graph from aggregated viewer data.
Creates weighted edges between channels based on shared viewers (overlap).
Applies threshold filtering to focus on meaningful connections.
"""

import networkx as nx
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
    
    def __init__(self, overlap_threshold: int = 1):
        """
        Initialize graph builder.
        
        Args:
            overlap_threshold: Minimum shared viewers required for an edge to exist
        """
        self.overlap_threshold = overlap_threshold
        self.graph = nx.Graph()
        self.overlap_data: Dict[Tuple[str, str], int] = {}
        
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
        
        channels = list(channel_viewers.keys())
        logger.info(f"Building graph with {len(channels)} channels")
        
        # Add nodes with metadata
        for channel in channels:
            attributes = {"viewers": len(channel_viewers[channel])}
            
            if channel_metadata and channel in channel_metadata:
                meta = channel_metadata[channel]
                attributes.update({
                    "viewer_count": meta.get("viewers", 0),
                    "game": meta.get("game", "Unknown"),
                    "title": meta.get("title", ""),
                })
            
            self.graph.add_node(channel, **attributes)
        
        # Compute overlaps and add edges
        edge_count = 0
        for channel1, channel2 in combinations(channels, 2):
            viewers1 = channel_viewers[channel1]
            viewers2 = channel_viewers[channel2]
            
            # Compute intersection (shared viewers)
            overlap = len(viewers1 & viewers2)
            
            if overlap >= self.overlap_threshold:
                self.graph.add_edge(channel1, channel2, weight=overlap)
                self.overlap_data[(channel1, channel2)] = overlap
                edge_count += 1
        
        logger.info(f"Created graph with {self.graph.number_of_nodes()} nodes "
                   f"and {self.graph.number_of_edges()} edges "
                   f"(threshold: {self.overlap_threshold})")
        
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
            "top_connected_channels": top_connected
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
                game = attrs.get('game', 'Unknown').replace(',', ';')
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
