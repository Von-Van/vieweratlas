"""
Visualizer Module

Creates bubble graph visualizations of the community-detected network.
Supports both static (Matplotlib) and interactive (PyVis) output.
"""

import networkx as nx
import matplotlib.pyplot as plt
import math
from typing import Dict, Set, Tuple, Optional
import logging

logger = logging.getLogger(__name__)

try:
    from pyvis.network import Network
    PYVIS_AVAILABLE = True
except ImportError:
    PYVIS_AVAILABLE = False
    logger.warning("PyVis not installed. Interactive visualization unavailable. "
                  "Install with: pip install pyvis")


# Color palette for communities (20 distinct colors)
COMMUNITY_COLORS = [
    '#FF6B6B',  # Red
    '#4ECDC4',  # Teal
    '#45B7D1',  # Blue
    '#FFA07A',  # Salmon
    '#98D8C8',  # Mint
    '#F7DC6F',  # Yellow
    '#BB8FCE',  # Purple
    '#85C1E2',  # Light Blue
    '#F8B88B',  # Peach
    '#52C0A1',  # Green
    '#FF6B9D',  # Pink
    '#C44569',  # Dark Red
    '#355C7D',  # Navy
    '#2A9D8F',  # Dark Teal
    '#E76F51',  # Orange
    '#F4A261',  # Light Orange
    '#E9C46A',  # Gold
    '#2A9D8F',  # Teal
    '#264653',  # Dark Blue
    '#E76F51',  # Rust
]


class Visualizer:
    """
    Creates visualizations of the community detection results.
    """
    
    def __init__(self, figsize: Tuple[int, int] = (20, 16)):
        """
        Initialize visualizer.
        
        Args:
            figsize: Figure size for matplotlib (width, height) in inches
        """
        self.figsize = figsize
        self.colors = COMMUNITY_COLORS
        
    def get_color_for_community(self, comm_id: int) -> str:
        """
        Get a consistent color for a community ID.
        
        Args:
            comm_id: Community ID
        
        Returns:
            Hex color string
        """
        return self.colors[comm_id % len(self.colors)]
    
    def visualize_static(self,
                        graph: nx.Graph,
                        partition: Dict[str, int],
                        labels: Dict[int, str] = None,
                        output_file: str = "community_graph.png",
                        show_labels: bool = True,
                        edge_threshold: Optional[int] = None) -> None:
        """
        Create a static visualization using Matplotlib.
        
        Args:
            graph: NetworkX graph
            partition: Dict mapping node -> community_id
            labels: Optional dict mapping community_id -> label string
            output_file: Path to save the image
            show_labels: Whether to label top nodes
            edge_threshold: Optional minimum edge weight to display
        """
        logger.info(f"Creating static visualization: {output_file}")
        
        # Filter edges by threshold if specified
        if edge_threshold:
            edges_to_remove = [
                (u, v) for u, v, d in graph.edges(data=True)
                if d['weight'] < edge_threshold
            ]
            display_graph = graph.copy()
            display_graph.remove_edges_from(edges_to_remove)
        else:
            display_graph = graph.copy()
        
        # Compute layout using spring (force-directed)
        logger.info("Computing force-directed layout...")
        pos = nx.spring_layout(
            display_graph,
            k=2,
            iterations=100,
            weight='weight',
            seed=42
        )
        
        # Create figure
        fig, ax = plt.subplots(figsize=self.figsize)
        
        # Prepare node colors and sizes
        node_colors = []
        node_sizes = []
        
        for node in display_graph.nodes():
            # Color by community
            comm_id = partition.get(node, -1)
            color = self.get_color_for_community(comm_id)
            node_colors.append(color)
            
            # Size by viewer count (with scaling)
            viewers = display_graph.nodes[node].get('viewers', 1)
            size = math.sqrt(viewers) * 5  # Scale down to reasonable range
            size = max(100, min(5000, size))  # Clamp between 100-5000
            node_sizes.append(size)
        
        # Draw edges
        logger.info("Drawing edges...")
        weights = [display_graph[u][v]['weight'] for u, v in display_graph.edges()]
        max_weight = max(weights) if weights else 1
        
        for u, v in display_graph.edges():
            weight = display_graph[u][v]['weight']
            # Normalize weight to opacity (0.1 to 0.8)
            alpha = 0.1 + (weight / max_weight) * 0.7
            # Normalize weight to line width (0.5 to 3.0)
            width = 0.5 + (weight / max_weight) * 2.5
            
            nx.draw_networkx_edges(
                display_graph, pos,
                [(u, v)],
                ax=ax,
                alpha=alpha,
                width=width,
                edge_color='gray'
            )
        
        # Draw nodes
        logger.info("Drawing nodes...")
        nx.draw_networkx_nodes(
            display_graph, pos,
            node_color=node_colors,
            node_size=node_sizes,
            ax=ax,
            alpha=0.9,
            edgecolors='black',
            linewidths=2
        )
        
        # Add labels for largest nodes
        if show_labels:
            # Label top 15 largest nodes
            node_size_map = {node: size for node, size in zip(display_graph.nodes(), node_sizes)}
            top_nodes = sorted(node_size_map.items(), key=lambda x: x[1], reverse=True)[:15]
            top_node_names = {node: node for node, _ in top_nodes}
            
            nx.draw_networkx_labels(
                display_graph, pos,
                labels=top_node_names,
                ax=ax,
                font_size=8,
                font_weight='bold'
            )
        
        # Create legend
        if labels:
            legend_handles = []
            for comm_id, label in sorted(labels.items()):
                color = self.get_color_for_community(comm_id)
                from matplotlib.patches import Patch
                legend_handles.append(Patch(facecolor=color, label=label))
            
            ax.legend(
                handles=legend_handles,
                loc='upper left',
                fontsize=10,
                title='Communities',
                title_fontsize=12
            )
        
        ax.set_title("Twitch Community Network Map", fontsize=18, fontweight='bold')
        ax.axis('off')
        plt.tight_layout()
        
        plt.savefig(output_file, dpi=300, bbox_inches='tight')
        logger.info(f"Saved visualization to {output_file}")
        plt.close()
    
    def visualize_interactive(self,
                             graph: nx.Graph,
                             partition: Dict[str, int],
                             labels: Dict[int, str] = None,
                             output_file: str = "community_graph.html") -> None:
        """
        Create an interactive visualization using PyVis.
        
        Args:
            graph: NetworkX graph
            partition: Dict mapping node -> community_id
            labels: Optional dict mapping community_id -> label
            output_file: Path to save the HTML file
        """
        if not PYVIS_AVAILABLE:
            logger.error("PyVis not available. Install with: pip install pyvis")
            return
        
        logger.info(f"Creating interactive visualization: {output_file}")
        
        # Create PyVis network
        net = Network(directed=False, height='750px', width='100%')
        net.show_buttons(filter_=['physics'])
        
        # Add nodes
        for node in graph.nodes():
            viewers = graph.nodes[node].get('viewers', 1)
            game = graph.nodes[node].get('game', 'Unknown')
            comm_id = partition.get(node, -1)
            
            # Community label
            community_label = labels.get(comm_id, f"Community {comm_id}") if labels else f"Community {comm_id}"
            
            color = self.get_color_for_community(comm_id)
            size = math.sqrt(viewers) * 2
            size = max(20, min(60, size))
            
            title = f"<b>{node}</b><br>Viewers: {viewers}<br>Game: {game}<br>Community: {community_label}"
            
            net.add_node(
                node,
                label=node,
                title=title,
                color=color,
                size=size,
                font={'size': 12}
            )
        
        # Add edges
        for u, v, data in graph.edges(data=True):
            weight = data['weight']
            # Edge thickness based on weight
            width = 0.5 + (weight / graph.number_of_edges()) * 3
            width = max(0.5, min(5, width))
            
            net.add_edge(u, v, value=weight, width=width, title=f"Shared viewers: {weight}")
        
        # Configure physics
        net.toggle_physics(True)
        net.show(output_file)
        logger.info(f"Saved interactive visualization to {output_file}")
    
    def export_layout_csv(self,
                         graph: nx.Graph,
                         partition: Dict[str, int],
                         pos: Dict[str, Tuple[float, float]],
                         output_file: str = "node_layout.csv") -> None:
        """
        Export node positions and community assignments to CSV.
        Useful for importing into other visualization tools.
        
        Args:
            graph: NetworkX graph
            partition: Dict mapping node -> community_id
            pos: Dict mapping node -> (x, y) position
            output_file: Path to save CSV
        """
        import csv
        
        with open(output_file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['node', 'community', 'x', 'y', 'viewers'])
            
            for node in graph.nodes():
                comm_id = partition.get(node, -1)
                x, y = pos.get(node, (0, 0))
                viewers = graph.nodes[node].get('viewers', 0)
                writer.writerow([node, comm_id, x, y, viewers])
        
        logger.info(f"Exported layout to {output_file}")


if __name__ == "__main__":
    # Test with sample data
    from data_aggregator import DataAggregator
    from graph_builder import GraphBuilder
    from community_detector import CommunityDetector
    from cluster_tagger import ClusterTagger
    
    logging.basicConfig(level=logging.INFO)
    
    # Load and process data
    aggregator = DataAggregator("logs")
    aggregator.load_all()
    
    builder = GraphBuilder(overlap_threshold=1)
    graph = builder.build_graph(
        aggregator.get_channel_viewers(),
        aggregator.get_channel_metadata()
    )
    
    try:
        detector = CommunityDetector()
        detector.detect_communities(graph)
        partition = detector.get_partition()
        communities = detector.get_communities()
        
        tagger = ClusterTagger()
        labels = tagger.tag_communities(communities, aggregator.get_channel_metadata())
        
        # Visualize
        viz = Visualizer()
        viz.visualize_static(graph, partition, labels, output_file="community_graph.png")
        
        if PYVIS_AVAILABLE:
            viz.visualize_interactive(graph, partition, labels, output_file="community_graph.html")
        else:
            print("PyVis not available for interactive visualization")
    
    except Exception as e:
        print(f"Visualization test failed: {e}")
        import traceback
        traceback.print_exc()
