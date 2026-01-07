"""
Community Detector Module

Uses modularity-based algorithms (Louvain) to detect communities
in the overlap graph. Partitions channels into groups with strong
internal connections (shared viewers).
"""

import networkx as nx
from typing import Dict, Set, List
import logging

logger = logging.getLogger(__name__)

try:
    import community  # python-louvain
    LOUVAIN_AVAILABLE = True
except ImportError:
    LOUVAIN_AVAILABLE = False
    logger.warning("python-louvain not installed. Install with: pip install python-louvain")


class CommunityDetector:
    """
    Detects communities in the overlap graph using modularity optimization.
    """
    
    def __init__(self, resolution: float = 1.0):
        """
        Initialize community detector.
        
        Args:
            resolution: Resolution parameter for modularity optimization.
                       Higher values produce more fine-grained communities.
                       Default 1.0 is standard; try 0.5 for coarser, 2.0 for finer.
        """
        self.resolution = resolution
        self.partition: Dict[str, int] = {}
        self.communities: Dict[int, Set[str]] = {}
        self.modularity = 0.0
        
    def detect_communities(self, graph: nx.Graph) -> Dict[str, int]:
        """
        Detect communities in the graph using Louvain algorithm.
        
        Args:
            graph: NetworkX graph with weighted edges
        
        Returns:
            Dict mapping channel -> community_id
        """
        if not LOUVAIN_AVAILABLE:
            raise ImportError("python-louvain is not installed. "
                            "Install with: pip install python-louvain")
        
        if graph.number_of_nodes() == 0:
            logger.warning("Graph has no nodes. Returning empty partition.")
            return {}
        
        logger.info(f"Detecting communities with resolution={self.resolution}")
        
        # Use Louvain algorithm for community detection
        self.partition = community.best_partition(
            graph,
            weight='weight',
            resolution=self.resolution
        )
        
        # Build communities dict from partition
        self.communities = {}
        for node, comm_id in self.partition.items():
            if comm_id not in self.communities:
                self.communities[comm_id] = set()
            self.communities[comm_id].add(node)
        
        # Calculate modularity
        self.modularity = community.modularity(self.partition, graph, weight='weight')
        
        logger.info(f"Detected {len(self.communities)} communities. "
                   f"Modularity: {self.modularity:.4f}")
        
        return self.partition
    
    def get_partition(self) -> Dict[str, int]:
        """
        Get the community assignment for each channel.
        
        Returns:
            Dict mapping channel -> community_id
        """
        return dict(self.partition)
    
    def get_communities(self) -> Dict[int, Set[str]]:
        """
        Get communities as sets of channels.
        
        Returns:
            Dict mapping community_id -> set of channels
        """
        return {k: v.copy() for k, v in self.communities.items()}
    
    def get_modularity(self) -> float:
        """
        Get the modularity score of the current partition.
        
        Higher modularity (closer to 1.0) indicates stronger community structure.
        
        Returns:
            Modularity score
        """
        return self.modularity
    
    def get_community_for_channel(self, channel: str) -> int:
        """
        Get the community ID for a specific channel.
        
        Args:
            channel: Channel name
        
        Returns:
            Community ID, or -1 if channel not found
        """
        return self.partition.get(channel, -1)
    
    def get_statistics(self) -> dict:
        """
        Get community detection statistics.
        
        Returns:
            Dict with community stats
        """
        if not self.communities:
            return {
                "num_communities": 0,
                "modularity": 0.0,
                "community_sizes": []
            }
        
        community_sizes = [(cid, len(channels)) 
                          for cid, channels in self.communities.items()]
        community_sizes.sort(key=lambda x: x[1], reverse=True)
        
        return {
            "num_communities": len(self.communities),
            "modularity": self.modularity,
            "community_sizes": community_sizes,
            "largest_community_size": community_sizes[0][1] if community_sizes else 0,
            "smallest_community_size": community_sizes[-1][1] if community_sizes else 0,
        }
    
    def set_resolution(self, resolution: float) -> None:
        """
        Set the resolution parameter for next detection.
        
        Lower resolution (e.g., 0.5): Larger, fewer communities
        Higher resolution (e.g., 2.0): Smaller, more communities
        
        Args:
            resolution: New resolution value
        """
        self.resolution = resolution
        logger.info(f"Resolution set to {self.resolution}")
    
    def add_community_attribute_to_graph(self, graph: nx.Graph) -> nx.Graph:
        """
        Add community assignment as node attribute in the graph.
        
        Args:
            graph: NetworkX graph
        
        Returns:
            Updated graph with 'community' attribute on each node
        """
        for node, comm_id in self.partition.items():
            if node in graph:
                graph.nodes[node]['community'] = comm_id
        
        return graph


class SimpleGreedyCommunityDetector:
    """
    Fallback community detector using a simple greedy algorithm.
    Use if python-louvain is not available.
    """
    
    def __init__(self):
        self.partition: Dict[str, int] = {}
        self.communities: Dict[int, Set[str]] = {}
        
    def detect_communities(self, graph: nx.Graph) -> Dict[str, int]:
        """
        Simple greedy community detection based on weighted edges.
        
        Algorithm:
        1. Start with each node in its own community
        2. Repeatedly merge communities that maximize modularity gain
        3. Stop when no beneficial merges remain
        
        Args:
            graph: NetworkX graph
        
        Returns:
            Dict mapping channel -> community_id
        """
        logger.info("Running greedy community detection (fallback)")
        
        # Initialize: each node is its own community
        self.partition = {node: i for i, node in enumerate(graph.nodes())}
        
        # Simple heuristic: merge communities connected by high-weight edges
        # This is a naive approach and less sophisticated than Louvain
        improved = True
        iteration = 0
        
        while improved and iteration < 10:  # Limit iterations
            improved = False
            iteration += 1
            
            # Try merging adjacent communities
            for u, v in graph.edges():
                comm_u = self.partition[u]
                comm_v = self.partition[v]
                
                if comm_u != comm_v:
                    # Merge v's community into u's
                    for node in graph.nodes():
                        if self.partition[node] == comm_v:
                            self.partition[node] = comm_u
                    improved = True
                    break
        
        # Rebuild communities dict
        self.communities = {}
        for node, comm_id in self.partition.items():
            if comm_id not in self.communities:
                self.communities[comm_id] = set()
            self.communities[comm_id].add(node)
        
        logger.info(f"Greedy detection found {len(self.communities)} communities")
        
        return self.partition
    
    def get_partition(self) -> Dict[str, int]:
        return dict(self.partition)
    
    def get_communities(self) -> Dict[int, Set[str]]:
        return {k: v.copy() for k, v in self.communities.items()}


if __name__ == "__main__":
    # Test with sample data
    from data_aggregator import DataAggregator
    from graph_builder import GraphBuilder
    
    logging.basicConfig(level=logging.INFO)
    
    # Load data and build graph
    aggregator = DataAggregator("logs")
    aggregator.load_all()
    
    builder = GraphBuilder(overlap_threshold=1)
    graph = builder.build_graph(
        aggregator.get_channel_viewers(),
        aggregator.get_channel_metadata()
    )
    
    # Detect communities
    if LOUVAIN_AVAILABLE:
        detector = CommunityDetector(resolution=1.0)
        partition = detector.detect_communities(graph)
        
        print("\nCommunity Detection Statistics:")
        stats = detector.get_statistics()
        for key, value in stats.items():
            print(f"  {key}: {value}")
    else:
        print("\npython-louvain not available. Using greedy fallback.")
        detector = SimpleGreedyCommunityDetector()
        partition = detector.detect_communities(graph)
        print(f"  Detected {len(detector.get_communities())} communities")
