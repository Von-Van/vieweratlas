"""
Visualizer Module

Creates bubble graph visualizations of the community-detected network.
Supports both static (Matplotlib) and interactive (PyVis) output.
"""

import networkx as nx
import matplotlib.pyplot as plt
from matplotlib.colors import to_rgb
import math
from typing import Dict, Tuple, Optional
import logging

logger = logging.getLogger(__name__)

try:
    from pyvis.network import Network
    PYVIS_AVAILABLE = True
except ImportError:
    PYVIS_AVAILABLE = False
    logger.warning("PyVis not installed. Interactive visualization unavailable. "
                  "Install with: pip install pyvis")


# Community colors, cycled when there are more communities than entries.
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
    '#264653',  # Dark Blue
]


class Visualizer:
    """
    Creates visualizations of the community detection results.
    """

    def __init__(self, figsize: Tuple[int, int] = (20, 16)):
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
                        edge_threshold: Optional[int] = None,
                        label_top_n: int = 15,
                        dpi: int = 300) -> None:
        """
        Create a static visualization using Matplotlib.

        Args:
            graph: NetworkX graph
            partition: Dict mapping node -> community_id
            labels: Optional dict mapping community_id -> label string
            output_file: Path to save the image
            show_labels: Whether to label top nodes
            edge_threshold: Optional minimum edge weight to display
            label_top_n: Number of largest nodes to label when show_labels is set
            dpi: Output resolution for the saved PNG
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

        fig, ax = plt.subplots(figsize=self.figsize)

        # Prepare node colors and sizes
        node_colors = []
        node_sizes = []

        for node in display_graph.nodes():
            comm_id = partition.get(node, -1)
            color = self.get_color_for_community(comm_id)
            node_colors.append(color)

            # Size by viewer count (with scaling)
            viewers = display_graph.nodes[node].get('viewers', 1)
            size = math.sqrt(viewers) * 5  # Scale down to reasonable range
            size = max(100, min(5000, size))  # Clamp between 100-5000
            node_sizes.append(size)

        #
        # One batched call, not one per edge. Calling draw_networkx_edges inside
        # the loop built a separate LineCollection artist for every edge and
        # kept them all on the axes: at 12,886 edges that fit, at 76,119 it
        # exhausted a 2 GB task. A single call holds one artist regardless of
        # graph size. Per-edge alpha rides in the RGBA colour because the
        # batched call takes only a scalar `alpha`.
        logger.info("Drawing edges...")
        edges = list(display_graph.edges())
        weights = [display_graph[u][v]['weight'] for u, v in edges]
        max_weight = max(weights) if weights else 1

        grey = to_rgb('gray')
        # Normalize weight to line width (0.5 to 3.0) and opacity (0.1 to 0.8).
        widths = [0.5 + (w / max_weight) * 2.5 for w in weights]
        colors = [(*grey, 0.1 + (w / max_weight) * 0.7) for w in weights]

        if edges:
            nx.draw_networkx_edges(
                display_graph, pos,
                edgelist=edges,
                ax=ax,
                width=widths,
                edge_color=colors,
            )

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
        if show_labels and label_top_n > 0:
            # Label the N largest nodes
            node_size_map = {node: size for node, size in zip(display_graph.nodes(), node_sizes)}
            top_nodes = sorted(node_size_map.items(), key=lambda x: x[1], reverse=True)[:label_top_n]
            top_node_names = {node: node for node, _ in top_nodes}

            nx.draw_networkx_labels(
                display_graph, pos,
                labels=top_node_names,
                ax=ax,
                font_size=8,
                font_weight='bold'
            )

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

        plt.savefig(output_file, dpi=dpi, bbox_inches='tight')
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

        for node in graph.nodes():
            viewers = graph.nodes[node].get('viewers', 1)
            game = graph.nodes[node].get('game_name', 'Unknown')
            comm_id = partition.get(node, -1)

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

        for u, v, data in graph.edges(data=True):
            weight = data['weight']
            # Edge thickness based on weight
            width = 0.5 + (weight / graph.number_of_edges()) * 3
            width = max(0.5, min(5, width))

            net.add_edge(u, v, value=weight, width=width, title=f"Shared viewers: {weight}")

        net.toggle_physics(True)
        # write_html, not show(): show() defaults to notebook=True, which needs a
        # notebook template this Network was not constructed with.
        net.write_html(output_file, notebook=False)
        logger.info(f"Saved interactive visualization to {output_file}")
