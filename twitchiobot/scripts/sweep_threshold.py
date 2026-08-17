#!/usr/bin/env python3
"""Sweep overlap_threshold against real survey data and report graph quality.

The shipped default of 10 was calibrated for continuous IRC collection. EventSub
surveys observe a 5-minute window three times a day, so the overlap distribution
is different and the old value is unlikely to be right. This measures it rather
than guessing.

Prints only aggregate statistics — no author IDs or logins leave the machine.

Usage:
    python3 scripts/sweep_threshold.py <dir-or-s3-uri> [--window-days 30]
"""
import argparse
import logging
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from community_detector import CommunityDetector  # noqa: E402
from data_aggregator import DataAggregator  # noqa: E402
from graph_builder import GraphBuilder  # noqa: E402
from storage import FileStorage  # noqa: E402


def _size_note(detector, ncomm: int, args) -> str:
    """Flag rows where min_community_size, not graph quality, shaped the result."""
    dropped = len(getattr(detector, "discarded_channels", ()) or ())
    if ncomm == 0:
        return f"all < size {args.min_community_size}"
    if dropped:
        return f"-{dropped} ch < size"
    return ""


def fetch(source: str) -> Path:
    """Return a storage root containing raw/snapshots/… .

    `aws s3 cp --recursive` strips the source prefix, so downloading
    s3://bucket/p/raw/snapshots/v2/ yields bare date=… folders. The aggregator
    lists keys under "raw/snapshots", so the prefix has to be rebuilt locally.
    """
    if not source.startswith("s3://"):
        return Path(source)

    tmp = Path(tempfile.mkdtemp())
    without_scheme = source[len("s3://"):]
    key = without_scheme.split("/", 1)[1] if "/" in without_scheme else ""
    marker = "raw/snapshots"
    if marker in key:
        # Recreate everything from raw/snapshots onward beneath the temp root.
        destination = tmp / key[key.index(marker):].rstrip("/")
    else:
        destination = tmp / marker
    destination.mkdir(parents=True, exist_ok=True)

    print(f"Downloading {source}\n         -> {destination}")
    subprocess.run(["aws", "s3", "cp", source, str(destination), "--recursive"],
                   check=True, capture_output=True)
    return tmp


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="Directory or s3:// URI holding raw/snapshots/v2/…")
    ap.add_argument("--window-days", type=int, default=None)
    ap.add_argument("--resolution", type=float, default=1.2)
    ap.add_argument("--min-community-size", type=int, default=3)
    ap.add_argument("--min-authors", type=int, default=0,
                    help="Drop channels with fewer than N unique authors "
                         "(rigorous uses 10); also removes degenerate tiny-set "
                         "pairs that score 1.0 under normalised modes")
    ap.add_argument("--min-observations", type=int, default=1,
                    help="Drop channels sampled fewer than N times before graphing")
    ap.add_argument("--mode", default="all",
                    choices=["all", "shared_count", "jaccard", "overlap_coef"])
    args = ap.parse_args()
    # Per-row detector warnings would interleave with the table.
    logging.getLogger("community_detector").setLevel(logging.ERROR)

    root = fetch(args.source)
    # The aggregator expects keys relative to the storage root, so point it at
    # whichever ancestor actually contains raw/snapshots.
    base = root
    while base != base.parent and not (base / "raw" / "snapshots").exists():
        found = list(base.rglob("raw/snapshots"))
        if found:
            base = found[0].parent.parent
            break
        break

    agg = DataAggregator(str(base), storage=FileStorage(base_dir=str(base)),
                         window_days=args.window_days)
    loaded = agg.load_parquet_snapshots()
    viewers = agg.get_channel_viewers()
    if not viewers:
        print("No channel rows loaded. Check the path points at raw/snapshots/v2/…")
        return 1

    if args.min_observations > 1:
        before = len(viewers)
        keep = agg.filter_channels_by_observations(args.min_observations)
        viewers = {c: v for c, v in viewers.items() if c in keep}
        print(f"observation filter: {before} -> {len(viewers)} channels "
              f"(min {args.min_observations} observations)")
        if not viewers:
            print("Nothing left after the observation filter.")
            return 1

    if args.min_authors > 0:
        before = len(viewers)
        viewers = {c: v for c, v in viewers.items() if len(v) >= args.min_authors}
        print(f"author filter: {before} -> {len(viewers)} channels "
              f"(min {args.min_authors} authors)")
        if not viewers:
            print("Nothing left after the author filter.")
            return 1

    obs = agg.get_channel_observations()
    counts = sorted(obs.get(c, 0) for c in viewers)
    print(f"observations per channel: min {counts[0]} | "
          f"median {statistics.median(counts):.0f} | max {counts[-1]}")

    sizes = sorted(len(v) for v in viewers.values())
    total_unique = len({v for s in viewers.values() for v in s})
    print(f"\n{loaded} channel rows | {len(viewers)} channels | "
          f"{total_unique} distinct authors")
    print(f"authors per channel: min {sizes[0]} | median {statistics.median(sizes):.0f} "
          f"| mean {statistics.mean(sizes):.0f} | max {sizes[-1]}")
    if agg.window_start:
        print(f"window: {agg.window_start} .. {agg.window_end}")

    # Compute overlaps once at threshold 1, then re-threshold in memory.
    base_graph = GraphBuilder(overlap_threshold=1).build_graph(viewers)
    weights = sorted(d["weight"] for _, _, d in base_graph.edges(data=True))
    if not weights:
        print("\nNo channel pair shares a single author. Nothing to tune.")
        return 1

    n = base_graph.number_of_nodes()
    possible = n * (n - 1) / 2
    print(f"\noverlap weights: {len(weights)} pairs | median {statistics.median(weights):.0f} "
          f"| p90 {weights[int(len(weights) * 0.9)]} | max {weights[-1]}")

    candidates = sorted({1, 2, 5, 10, 25, 50, 100, 200, 300, 500,
                         int(statistics.median(weights)) or 1,
                         weights[int(len(weights) * 0.75)],
                         weights[int(len(weights) * 0.9)],
                         weights[int(len(weights) * 0.95)]})

    print(f"\n{'threshold':>10} {'edges':>8} {'density':>9} {'connected':>9} "
          f"{'communities':>12} {'modularity':>11} {'note':>16}")
    print("-" * 64)
    best = []
    for t in candidates:
        edges = [(u, v, w) for u, v, w in
                 ((u, v, d["weight"]) for u, v, d in base_graph.edges(data=True)) if w >= t]
        g = base_graph.__class__()
        g.add_nodes_from(base_graph.nodes(data=True))
        g.add_edges_from((u, v, {"weight": w}) for u, v, w in edges)
        if not edges:
            print(f"{t:>10} {0:>8} {0.0:>9.5f} {'0%':>9} {'-':>12} {'-':>11} "
                  f"{'no edges':>16}")
            continue
        isolated = sum(1 for node in g if g.degree(node) == 0)
        det = CommunityDetector(resolution=args.resolution,
                                min_community_size=args.min_community_size)
        det.detect_communities(g)
        ncomm, mod = len(det.get_communities()), det.get_modularity()
        density = len(edges) / possible
        print(f"{t:>10} {len(edges):>8} {density:>9.5f} {(n-isolated)/n:>9.0%} "
              f"{ncomm:>12} {mod:>11.3f} {_size_note(det, ncomm, args):>16}")
        # Judge on connected coverage, not absolute density: a real graph of
        # thousands of channels is inherently sparse, and a fixed density band
        # calibrated on small graphs can never be satisfied at that scale.
        connected_fraction = (n - isolated) / n if n else 0.0
        if connected_fraction >= 0.25 and ncomm >= 3:
            best.append((mod, t, density, ncomm, connected_fraction))

    print("\n" + "-" * 64)
    if best:
        best.sort(reverse=True)
        mod, t, density, ncomm, cov = best[0]
        print(f"Suggested overlap_threshold (shared_count): {t}")
        print(f"  modularity {mod:.3f} | {cov:.0%} of channels connected | "
              f"{ncomm} communities")
    else:
        print("No shared_count threshold kept 25%+ of channels connected "
              "with 3+ communities.")

    # Normalised modes: raw counts favour channels that were simply sampled more
    # often, so compare them on the same data before committing to a config.
    modes = (["jaccard", "overlap_coef"] if args.mode == "all"
             else [] if args.mode == "shared_count" else [args.mode])
    for mode in modes:
        g_all = GraphBuilder(overlap_threshold=1, weighting_mode=mode).build_graph(viewers)
        scores = sorted(d["weight"] for _, _, d in g_all.edges(data=True))
        if not scores:
            continue
        print(f"\n=== {mode} ===")
        print(f"similarity: median {statistics.median(scores):.4f} "
              f"| p90 {scores[int(len(scores) * 0.9)]:.4f} | max {scores[-1]:.4f}")
        print(f"{'norm thr':>10} {'edges':>8} {'density':>9} {'connected':>9} "
              f"{'communities':>12} {'modularity':>11} {'note':>16}")
        print("-" * 64)
        cuts = sorted({round(v, 4) for v in (
            0.01, 0.02, 0.05, 0.10, 0.20,
            statistics.median(scores),
            scores[int(len(scores) * 0.75)],
            scores[int(len(scores) * 0.9)],
            scores[int(len(scores) * 0.95)],
        )})
        for cut in cuts:
            kept = [(u, v, d["weight"]) for u, v, d in g_all.edges(data=True)
                    if d["weight"] >= cut]
            if not kept:
                print(f"{cut:>10.4f} {0:>8} {0.0:>9.5f} {'0%':>9} {'-':>12} "
                      f"{'-':>11} {'no edges':>16}")
                continue
            g = g_all.__class__()
            g.add_nodes_from(g_all.nodes(data=True))
            g.add_edges_from((u, v, {"weight": w}) for u, v, w in kept)
            isolated = sum(1 for node in g if g.degree(node) == 0)
            det = CommunityDetector(resolution=args.resolution,
                                    min_community_size=args.min_community_size)
            det.detect_communities(g)
            ncomm = len(det.get_communities())
            print(f"{cut:>10.4f} {len(kept):>8} {len(kept) / possible:>9.5f} "
                  f"{(n - isolated) / n:>9.0%} {ncomm:>12} "
                  f"{det.get_modularity():>11.3f} {_size_note(det, ncomm, args):>16}")

    print("\nSet weighting_mode / overlap_threshold / normalized_overlap_threshold")
    print("in get_rigorous_config() (config.py), then redeploy the analysis image.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
