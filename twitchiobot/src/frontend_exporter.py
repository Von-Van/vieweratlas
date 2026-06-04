"""
Frontend Data Exporter

Transforms pipeline analysis outputs (NetworkX graph, community partition,
labels, aggregator stats) into a JSON file matching the frontend's TypeScript
type definitions. The output is written to storage at `data/frontend-data.json`
so the React app can fetch it at runtime.
"""

import logging
import re
from datetime import datetime
from typing import Dict, Set, Any, Optional

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


def _slugify(text: str) -> str:
    """Convert a label like 'FPS English' to 'fps-english'."""
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def _capitalize_channel(name: str) -> str:
    """Best-effort display name from a lowercase channel login."""
    if not name:
        return name
    return name[0].upper() + name[1:]


def export_frontend_data(
    graph: nx.Graph,
    partition: Dict[str, int],
    communities: Dict[int, Set[str]],
    labels: Dict[int, str],
    detection_stats: dict,
    aggregator_stats: dict,
    storage: Any,
    output_key: str = "data/frontend-data.json",
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
        logger.info("Generating frontend data JSON...")

        # --- Sort communities by size for stable color assignment ---
        sorted_comm_ids = sorted(
            communities.keys(),
            key=lambda cid: len(communities[cid]),
            reverse=True,
        )

        comm_id_to_slug: Dict[int, str] = {}
        comm_id_to_color: Dict[int, str] = {}

        frontend_communities = []
        for rank, cid in enumerate(sorted_comm_ids):
            label = labels.get(cid, f"Community {cid}")
            slug = _slugify(label)
            color = COMMUNITY_COLORS[rank % len(COMMUNITY_COLORS)]

            comm_id_to_slug[cid] = slug
            comm_id_to_color[cid] = color

            frontend_communities.append({
                "id": slug,
                "label": label,
                "color": color,
                "nodeCount": len(communities[cid]),
                "description": f"{label} community — {len(communities[cid])} channels",
            })

        # --- Build channels array ---
        analysis_date = datetime.now().strftime("%b %d")
        frontend_channels = []

        for node, attrs in graph.nodes(data=True):
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
            for neighbor in graph.neighbors(node):
                weight = graph[node][neighbor].get("weight", 0)
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
            degree = graph.degree(node)
            if degree > 0:
                intra = sum(
                    1
                    for nb in graph.neighbors(node)
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
            })

        # Sort channels by viewer count descending
        frontend_channels.sort(key=lambda c: c["viewers"], reverse=True)

        # --- Build edges array ---
        frontend_edges = [
            {"source": u, "target": v, "weight": d.get("weight", 0)}
            for u, v, d in graph.edges(data=True)
        ]

        # --- Overall stats ---
        total_viewers = aggregator_stats.get("total_unique_viewers_across_all", 0)
        edge_weights = [d.get("weight", 0) for _, _, d in graph.edges(data=True)]
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
        }

        # --- Top communities by size (top 8) ---
        frontend_top_communities = []
        for rank, cid in enumerate(sorted_comm_ids[:8]):
            label = labels.get(cid, f"Community {cid}")
            members = communities[cid]
            total_community_viewers = sum(
                graph.nodes[ch].get("viewer_count", graph.nodes[ch].get("viewers", 0))
                for ch in members
                if ch in graph
            )
            frontend_top_communities.append({
                "community": label,
                "channels": len(members),
                "viewers": total_community_viewers,
            })

        # --- Most connected channels (top 10) ---
        degrees = [(node, graph.degree(node)) for node in graph.nodes()]
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
