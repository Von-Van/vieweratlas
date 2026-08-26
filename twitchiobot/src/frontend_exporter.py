"""
Frontend Data Exporter

Transforms pipeline analysis outputs (NetworkX graph, community partition,
labels, aggregator stats) into a JSON file matching the frontend's TypeScript
type definitions. The output is written to storage at `data/frontend-data.json`
so the React app can fetch it at runtime.
"""

import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Set, Any

import networkx as nx
import numpy as np

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
    layout_scale: int = 340
    # Communities are laid out in their own discs and then packed, rather than
    # thrown into one spring pass. A single spring layout over ~900 nodes puts
    # every community on top of every other one at the sizes the browser draws.
    community_layout: bool = True
    # Fraction of a community's disc covered by its own node discs. Lower is
    # airier; above ~0.3 members start overlapping.
    community_fill: float = 0.20


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


# Mirrors the node radius formula in NetworkGraph.tsx. Slot sizing depends on
# how large the browser actually draws a node, so these must stay in step with
# the frontend's minR/maxR.
NODE_MIN_R = 4.0
NODE_MAX_R = 16.0


def _node_radius(viewers: int, max_viewers: int) -> float:
    """Drawn radius the browser will use for a node, in layout units."""
    if max_viewers <= 0:
        return NODE_MIN_R
    return NODE_MIN_R + math.sqrt(max(0, viewers) / max_viewers) * (NODE_MAX_R - NODE_MIN_R)


def _normalize_positions(positions: dict, scale: float, percentile: float = 97.0) -> dict:
    """Recentre and scale so the `percentile`-th radius lands on `scale`.

    ``nx.spring_layout(scale=...)`` maps the single furthest node to the target,
    so one loosely attached node compresses everything else into the middle.
    Scaling on a percentile ignores that node; the few beyond the cut are eased
    inward along their own bearing instead of being clipped, which keeps their
    direction meaningful without letting them set the scale.
    """
    if not positions:
        return {}
    names = list(positions)
    pts = np.array([positions[n] for n in names], dtype=float)
    pts -= pts.mean(axis=0)

    radii = np.hypot(pts[:, 0], pts[:, 1])
    reference = float(np.percentile(radii, percentile))
    if reference <= 0:
        reference = float(radii.max()) or 1.0
    pts *= scale / reference

    radii = np.hypot(pts[:, 0], pts[:, 1])
    beyond = radii > scale
    if beyond.any():
        eased = scale * (1 + 0.12 * np.log1p((radii[beyond] - scale) / scale))
        pts[beyond] *= (eased / radii[beyond])[:, None]

    return {n: (float(pts[i, 0]), float(pts[i, 1])) for i, n in enumerate(names)}


def _pack_discs(centres: dict, radii: dict, margin: float = 1.05, rounds: int = 800) -> dict:
    """Push overlapping community discs apart, preserving their arrangement."""
    ids = list(centres)
    pos = {c: np.array(centres[c], dtype=float) for c in ids}
    for _ in range(rounds):
        moved = False
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                delta = pos[b] - pos[a]
                distance = float(np.hypot(*delta)) or 1e-6
                needed = (radii[a] + radii[b]) * margin
                if distance < needed:
                    push = (needed - distance) / 2 * (delta / distance)
                    pos[a] -= push
                    pos[b] += push
                    moved = True
        if not moved:
            break
    return {c: (float(pos[c][0]), float(pos[c][1])) for c in ids}


def _shared_count(edge_data: dict) -> int:
    """Measured shared-chatter count for the public payload.

    The graph's ``weight`` may be a normalised similarity score, but the public
    schema requires a non-negative integer and readers expect a real observed
    count, so ``shared`` is authoritative here.
    """
    value = edge_data.get("shared", edge_data.get("weight", 0))
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _capitalize_channel(name: str) -> str:
    """Best-effort display name from a lowercase channel login."""
    if not name:
        return name
    return name[0].upper() + name[1:]


def _node_viewer_count(graph: nx.Graph, node: str) -> int:
    attrs = graph.nodes[node]
    return int(attrs.get("viewer_count", attrs.get("viewers", 0)) or 0)


def _disambiguate_labels(
    labels: Dict[int, str],
    community_ids: list,
    members: Dict[int, Set[str]],
    graph: nx.Graph,
) -> Dict[int, str]:
    """Append top channels to labels that several communities would share.

    Communities are named for their dominant game and language, so genuinely
    different groups collide — a VTuber cluster and a mainstream variety cluster
    are both "Just Chatting (en)". Identical names on a map of named regions are
    worse than none, so the colliding ones get their largest channels appended.
    Names that are already unique stay short.
    """
    counts: Dict[str, int] = {}
    for cid in community_ids:
        base = labels.get(cid, f"Community {cid}")
        counts[base] = counts.get(base, 0) + 1

    resolved = {}
    for cid in community_ids:
        base = labels.get(cid, f"Community {cid}")
        if counts[base] < 2:
            resolved[cid] = base
            continue
        top = sorted(
            members[cid],
            key=lambda n: (_node_viewer_count(graph, n), n),
            reverse=True,
        )[:2]
        suffix = ", ".join(_capitalize_channel(n) for n in top)
        candidate = f"{base} · {suffix}" if suffix else base
        # The frontend validator caps a community label at 120 characters.
        resolved[cid] = candidate[:120]
    return resolved


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

    # Channels left with no overlap edge say nothing on an overlap map, and a
    # spring layout flings them to the boundary — where they set the coordinate
    # scale and squeeze the connected structure into the middle of the canvas.
    if public_graph.number_of_edges():
        connected = max(nx.connected_components(public_graph), key=len)
        dropped = public_graph.number_of_nodes() - len(connected)
        if dropped:
            public_graph = public_graph.subgraph(connected).copy()
            logger.info(
                "Public frontend graph: dropped %d channel(s) outside the connected core",
                dropped,
            )

    if public_graph.number_of_nodes() != graph.number_of_nodes() or public_graph.number_of_edges() != graph.number_of_edges():
        logger.info(
            "Public frontend graph capped: %d/%d channels, %d/%d edges",
            public_graph.number_of_nodes(),
            graph.number_of_nodes(),
            public_graph.number_of_edges(),
            graph.number_of_edges(),
        )

    return public_graph


def _compute_layout(
    graph: nx.Graph,
    scale: int,
    partition: Dict[str, int] | None = None,
    config: "FrontendExportConfig | None" = None,
) -> Dict[str, Dict[str, float]]:
    """Compute a stable, readable layout for the public graph.

    With ``community_layout`` enabled this lays out the community meta-graph,
    gives each community a disc sized to the node area it has to hold, packs
    those discs apart, then lays out each community's members inside its own
    disc. One flat spring pass over the whole graph technically separates the
    communities but leaves them overlapping at the scale the browser draws
    nodes, so the map reads as a single blob.
    """
    if graph.number_of_nodes() == 0:
        return {}

    config = config or FrontendExportConfig()

    def _emit(positions: dict) -> Dict[str, Dict[str, float]]:
        return {
            node: {"x": round(float(xy[0]), 3), "y": round(float(xy[1]), 3)}
            for node, xy in positions.items()
        }

    try:
        if config.community_layout and partition:
            return _emit(_community_layout(graph, scale, partition, config))
        flat = nx.spring_layout(
            graph,
            seed=42,
            weight="weight",
            iterations=300,
            k=2.2 / math.sqrt(max(1, graph.number_of_nodes())),
        )
        return _emit(_normalize_positions({n: tuple(p) for n, p in flat.items()}, scale))
    except Exception as exc:
        logger.warning("Frontend layout generation failed; falling back to circle layout: %s", exc)
        positions = nx.circular_layout(graph, scale=scale)
        return _emit({n: tuple(p) for n, p in positions.items()})


def _community_layout(
    graph: nx.Graph,
    scale: int,
    partition: Dict[str, int],
    config: FrontendExportConfig,
) -> dict:
    """Lay each community out in its own disc, then pack the discs."""
    members: Dict[int, list] = {}
    for node in graph.nodes():
        members.setdefault(partition.get(node, -1), []).append(node)

    max_viewers = max((_node_viewer_count(graph, n) for n in graph.nodes()), default=0)

    # A disc big enough that its members cover `community_fill` of its area.
    radii = {}
    for cid, group in members.items():
        node_area = sum(
            _node_radius(_node_viewer_count(graph, n), max_viewers) ** 2 for n in group
        )
        radii[cid] = math.sqrt(node_area / config.community_fill)

    # Meta-graph: communities joined by the weight crossing between them.
    meta = nx.Graph()
    meta.add_nodes_from(members)
    for u, v, weight in graph.edges(data="weight"):
        cu, cv = partition.get(u, -1), partition.get(v, -1)
        if cu != cv:
            existing = meta.get_edge_data(cu, cv, {"weight": 0})["weight"]
            meta.add_edge(cu, cv, weight=existing + int(weight or 0))

    if meta.number_of_nodes() == 1:
        centres = {next(iter(members)): (0.0, 0.0)}
    else:
        meta_pos = nx.spring_layout(meta, seed=42, weight="weight", iterations=400, scale=scale)
        centres = _pack_discs({c: tuple(p) for c, p in meta_pos.items()}, radii)

    positions = {}
    for cid, group in members.items():
        cx, cy = centres[cid]
        if len(group) == 1:
            positions[group[0]] = (cx, cy)
            continue
        local = nx.spring_layout(
            graph.subgraph(group), seed=42, weight="weight", iterations=150
        )
        # The same percentile guard applies within a community: one peripheral
        # member would otherwise collapse the rest into the centre of the disc.
        local = _normalize_positions(
            {n: tuple(p) for n, p in local.items()}, radii[cid] * 0.9, percentile=90.0
        )
        for node, (lx, ly) in local.items():
            positions[node] = (cx + lx, cy + ly)

    return positions


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
        layout = _compute_layout(public_graph, config.layout_scale, partition, config)

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

        display_labels = _disambiguate_labels(
            labels, sorted_comm_ids, public_communities, public_graph
        )

        frontend_communities = []
        for rank, cid in enumerate(sorted_comm_ids):
            label = display_labels[cid]
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
            game = attrs.get("game_name") or "Unknown"
            viewer_count = attrs.get("viewer_count", attrs.get("viewers", 0))
            language = attrs.get("language", "")
            community_label = display_labels.get(cid, labels.get(cid, "Unknown"))

            # Top overlaps: neighbors sorted by edge weight desc
            neighbors = []
            for neighbor in public_graph.neighbors(node):
                edge = public_graph[node][neighbor]
                neighbors.append((neighbor, _shared_count(edge)))
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
            {"source": u, "target": v, "weight": _shared_count(d)}
            for u, v, d in public_graph.edges(data=True)
        ]

        # --- Overall stats ---
        total_viewers = aggregator_stats.get("total_unique_viewers_across_all", 0)
        edge_weights = [_shared_count(d) for _, _, d in public_graph.edges(data=True)]
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
