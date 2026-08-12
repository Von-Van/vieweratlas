"""
Frontend Data Exporter

Transforms pipeline analysis outputs (NetworkX graph, community partition,
labels, aggregator stats) into a JSON file matching the frontend's TypeScript
type definitions. The output is written to storage at `data/frontend-data.json`
so the React app can fetch it at runtime.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Set, Any

import networkx as nx

logger = logging.getLogger(__name__)

# Fixed color palette — deterministic assignment by community size rank
COMMUNITY_COLORS = [
    "#9147FF",  # purple (Twitch brand)
    "#00E5CC",  # teal
    "#FF7B00",  # orange
    "#1DB954",  # green
    "#FF4D6D",  # pink
    "#FFD700",  # gold
    "#4299E1",  # blue
    "#E53E3E",  # red
    "#38B2AC",  # dark teal
    "#D69E2E",  # amber
    "#9F7AEA",  # light purple
    "#ED64A6",  # magenta
]


@dataclass(frozen=True)
class FrontendExportConfig:
    """Limits for the public browser-facing graph artifact."""

    max_channels: int = 1000
    max_edges: int = 25000
    top_edges_per_channel: int = 25
    layout_scale: int = 260


def _slugify(text: str) -> str:
    """Convert a label like 'FPS English' to 'fps-english'."""
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-") or "community"


def _unique_slug(label: str, cid: int, used: set[str]) -> str:
    """Create a stable community slug and avoid collisions from duplicate labels."""
    base = _slugify(label)
    slug = base
    if slug in used:
        slug = f"{base}-{cid}"
    suffix = 2
    while slug in used:
        slug = f"{base}-{cid}-{suffix}"
        suffix += 1
    used.add(slug)
    return slug


def _capitalize_channel(name: str) -> str:
    """Best-effort display name from a lowercase channel login."""
    if not name:
        return name
    return name[0].upper() + name[1:]


def _node_viewer_count(graph: nx.Graph, node: str) -> int:
    attrs = graph.nodes[node]
    return int(attrs.get("viewer_count", attrs.get("viewers", 0)) or 0)


def _build_public_graph(graph: nx.Graph, config: FrontendExportConfig) -> nx.Graph:
    """Return a deterministic capped subgraph suitable for browser rendering."""
    selected_nodes = [
        node
        for node, _attrs in sorted(
            graph.nodes(data=True),
            key=lambda item: (
                _node_viewer_count(graph, item[0]),
                graph.degree(item[0]),
                item[0],
            ),
            reverse=True,
        )[:config.max_channels]
    ]
    selected_node_ids = set(selected_nodes)

    public_graph = nx.Graph()
    for node in selected_nodes:
        public_graph.add_node(node, **graph.nodes[node])

    edge_counts: Dict[str, int] = {node: 0 for node in selected_nodes}
    candidate_edges = sorted(
        (
            (u, v, int(data.get("weight", 0) or 0))
            for u, v, data in graph.edges(data=True)
            if u in selected_node_ids and v in selected_node_ids
        ),
        key=lambda item: (-item[2], item[0], item[1]),
    )

    for u, v, weight in candidate_edges:
        if public_graph.number_of_edges() >= config.max_edges:
            break
        if edge_counts[u] >= config.top_edges_per_channel:
            continue
        if edge_counts[v] >= config.top_edges_per_channel:
            continue
        public_graph.add_edge(u, v, weight=weight)
        edge_counts[u] += 1
        edge_counts[v] += 1

    if public_graph.number_of_nodes() != graph.number_of_nodes() or public_graph.number_of_edges() != graph.number_of_edges():
        logger.info(
            "Public frontend graph capped: %d/%d channels, %d/%d edges",
            public_graph.number_of_nodes(),
            graph.number_of_nodes(),
            public_graph.number_of_edges(),
            graph.number_of_edges(),
        )

    return public_graph


def _compute_layout(graph: nx.Graph, scale: int) -> Dict[str, Dict[str, float]]:
    """Compute a stable layout for the public graph."""
    if graph.number_of_nodes() == 0:
        return {}
    try:
        positions = nx.spring_layout(
            graph,
            seed=42,
            weight="weight",
            iterations=80,
            scale=scale,
        )
    except Exception as exc:
        logger.warning("Frontend layout generation failed; falling back to circle layout: %s", exc)
        positions = nx.circular_layout(graph, scale=scale)

    return {
        node: {"x": round(float(pos[0]), 3), "y": round(float(pos[1]), 3)}
        for node, pos in positions.items()
    }


def export_frontend_data(
    graph: nx.Graph,
    partition: Dict[str, int],
    communities: Dict[int, Set[str]],
    labels: Dict[int, str],
    detection_stats: dict,
    aggregator_stats: dict,
    storage: Any,
    output_key: str = "data/frontend-data.json",
    config: FrontendExportConfig | None = None,
) -> bool:
    """
    Build and upload the frontend-compatible JSON from pipeline outputs.

    Args:
        graph: NetworkX graph with node/edge attributes from GraphBuilder.
        partition: channel_name → community_id mapping.
        communities: community_id → set of channel_names.
        labels: community_id → human-readable label.
        detection_stats: dict from CommunityDetector.get_statistics().
        aggregator_stats: dict from DataAggregator.get_statistics().
        storage: BaseStorage instance (FileStorage or S3Storage).
        output_key: Storage key for the JSON file.

    Returns:
        True on success, False on failure.
    """
    try:
        config = config or FrontendExportConfig()
        logger.info("Generating frontend data JSON...")
        public_graph = _build_public_graph(graph, config)
        public_nodes = set(public_graph.nodes())
        layout = _compute_layout(public_graph, config.layout_scale)

        public_communities: Dict[int, Set[str]] = {}
        for cid, members in communities.items():
            public_members = set(members) & public_nodes
            if public_members:
                public_communities[cid] = public_members

        # --- Sort communities by size for stable color assignment ---
        sorted_comm_ids = sorted(
            public_communities.keys(),
            key=lambda cid: len(public_communities[cid]),
            reverse=True,
        )

        comm_id_to_slug: Dict[int, str] = {}
        comm_id_to_color: Dict[int, str] = {}
        used_slugs: set[str] = set()

        frontend_communities = []
        for rank, cid in enumerate(sorted_comm_ids):
            label = labels.get(cid, f"Community {cid}")
            slug = _unique_slug(label, cid, used_slugs)
            color = COMMUNITY_COLORS[rank % len(COMMUNITY_COLORS)]

            comm_id_to_slug[cid] = slug
            comm_id_to_color[cid] = color

            frontend_communities.append({
                "id": slug,
                "label": label,
                "color": color,
                "nodeCount": len(public_communities[cid]),
                "description": f"{label} community - {len(public_communities[cid])} rendered channels",
            })

        # --- Build channels array ---
        analysis_date = datetime.now().strftime("%b %d")
        frontend_channels = []

        for node, attrs in public_graph.nodes(data=True):
            cid = partition.get(node)
            if cid is None:
                continue

            slug = comm_id_to_slug.get(cid, "unknown")
            color = comm_id_to_color.get(cid, "#9147FF")
            game = attrs.get("game_name", "Unknown")
            viewer_count = attrs.get("viewer_count", attrs.get("viewers", 0))
            language = attrs.get("language", "")
            community_label = labels.get(cid, "Unknown")

            # Top overlaps: neighbors sorted by edge weight desc
            neighbors = []
            for neighbor in public_graph.neighbors(node):
                weight = public_graph[node][neighbor].get("weight", 0)
                neighbors.append((neighbor, weight))
            neighbors.sort(key=lambda x: x[1], reverse=True)

            top_overlaps = [
                {
                    "channelId": n,
                    "channelName": _capitalize_channel(n),
                    "shared": w,
                }
                for n, w in neighbors[:5]
            ]

            # Per-node modularity approximation:
            # fraction of edges that are intra-community
            degree = public_graph.degree(node)
            if degree > 0:
                intra = sum(
                    1
                    for nb in public_graph.neighbors(node)
                    if partition.get(nb) == cid
                )
                mod_score = round(intra / degree, 2)
            else:
                mod_score = 0.0

            frontend_channels.append({
                "id": node,
                "name": node,
                "displayName": _capitalize_channel(node),
                "game": game,
                "viewers": viewer_count,
                "communityId": slug,
                "description": f"{_capitalize_channel(node)} — {game} streamer in the {community_label} community.",
                "language": language.capitalize() if language else "Unknown",
                "topOverlaps": top_overlaps,
                "viewerHistory": [{"date": analysis_date, "viewers": viewer_count}],
                "edgeCount": degree,
                "modularityScore": mod_score,
                "layout": layout.get(node),
            })

        # Sort channels by viewer count descending
        frontend_channels.sort(key=lambda c: c["viewers"], reverse=True)

        # --- Build edges array ---
        frontend_edges = [
            {"source": u, "target": v, "weight": d.get("weight", 0)}
            for u, v, d in public_graph.edges(data=True)
        ]

        # --- Overall stats ---
        total_viewers = aggregator_stats.get("total_unique_viewers_across_all", 0)
        edge_weights = [d.get("weight", 0) for _, _, d in public_graph.edges(data=True)]
        avg_weight = round(sum(edge_weights) / len(edge_weights)) if edge_weights else 0

        # Derive collection period from aggregator stats if available
        collection_period = aggregator_stats.get(
            "collection_period",
            f"as of {datetime.now().strftime('%b %d, %Y')}",
        )

        frontend_overall_stats = {
            "totalChannels": graph.number_of_nodes(),
            "totalViewers": total_viewers,
            "communitiesDetected": len(communities),
            "modularityScore": round(detection_stats.get("modularity", 0), 2),
            "collectionPeriod": collection_period,
            "dataPoints": aggregator_stats.get("total_snapshots", 0),
            "edgesTotal": graph.number_of_edges(),
            "avgOverlapWeight": avg_weight,
            "renderedChannels": public_graph.number_of_nodes(),
            "renderedEdges": public_graph.number_of_edges(),
        }

        # --- Top communities by size (top 8) ---
        frontend_top_communities = []
        for rank, cid in enumerate(sorted_comm_ids[:8]):
            label = labels.get(cid, f"Community {cid}")
            members = public_communities[cid]
            total_community_viewers = sum(
                public_graph.nodes[ch].get("viewer_count", public_graph.nodes[ch].get("viewers", 0))
                for ch in members
                if ch in public_graph
            )
            frontend_top_communities.append({
                "community": label,
                "channels": len(members),
                "viewers": total_community_viewers,
            })

        # --- Most connected channels (top 10) ---
        degrees = [(node, public_graph.degree(node)) for node in public_graph.nodes()]
        degrees.sort(key=lambda x: x[1], reverse=True)

        frontend_most_connected = []
        for node, deg in degrees[:10]:
            cid = partition.get(node)
            frontend_most_connected.append({
                "name": _capitalize_channel(node),
                "edges": deg,
                "community": labels.get(cid, "Unknown"),
                "color": comm_id_to_color.get(cid, "#9147FF"),
            })

        # --- Assemble final payload ---
        payload = {
            "generatedAt": datetime.now().isoformat(),
            "communities": frontend_communities,
            "channels": frontend_channels,
            "edges": frontend_edges,
            "overallStats": frontend_overall_stats,
            "topCommunitiesBySize": frontend_top_communities,
            "mostConnectedChannels": frontend_most_connected,
        }

        # Upload
        success = storage.upload_json(output_key, payload)
        if success:
            logger.info(
                "Frontend data exported: %d communities, %d channels, %d edges → %s",
                len(frontend_communities),
                len(frontend_channels),
                len(frontend_edges),
                storage.get_uri(output_key),
            )
        else:
            logger.error("Failed to upload frontend data JSON")

        return success

    except Exception as e:
        logger.error("Frontend data export failed: %s", e, exc_info=True)
        return False
