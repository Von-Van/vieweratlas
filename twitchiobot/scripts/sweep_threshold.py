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


def fetch(source: str) -> Path:
    if not source.startswith("s3://"):
        return Path(source)
    tmp = Path(tempfile.mkdtemp())
    print(f"Downloading {source} -> {tmp}")
    subprocess.run(["aws", "s3", "cp", source, str(tmp), "--recursive"],
                   check=True, capture_output=True)
    return tmp


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="Directory or s3:// URI holding raw/snapshots/v2/…")
    ap.add_argument("--window-days", type=int, default=None)
    ap.add_argument("--resolution", type=float, default=1.2)
    ap.add_argument("--min-community-size", type=int, default=3)
    args = ap.parse_args()

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

    print(f"\n{'threshold':>10} {'edges':>8} {'density':>9} {'isolated':>9} "
          f"{'communities':>12} {'modularity':>11}")
    print("-" * 64)
    best = []
    for t in candidates:
        edges = [(u, v, w) for u, v, w in
                 ((u, v, d["weight"]) for u, v, d in base_graph.edges(data=True)) if w >= t]
        g = base_graph.__class__()
        g.add_nodes_from(base_graph.nodes(data=True))
        g.add_edges_from((u, v, {"weight": w}) for u, v, w in edges)
        if not edges:
            print(f"{t:>10} {0:>8} {0.0:>9.3f} {n:>9} {'-':>12} {'-':>11}")
            continue
        isolated = sum(1 for node in g if g.degree(node) == 0)
        det = CommunityDetector(resolution=args.resolution,
                                min_community_size=args.min_community_size)
        det.detect_communities(g)
        ncomm, mod = len(det.get_communities()), det.get_modularity()
        density = len(edges) / possible
        print(f"{t:>10} {len(edges):>8} {density:>9.3f} {isolated:>9} "
              f"{ncomm:>12} {mod:>11.3f}")
        # Prefer high modularity with a readable, non-saturated graph.
        if 0.02 <= density <= 0.30 and ncomm >= 3 and isolated < n * 0.5:
            best.append((mod, t, density, ncomm))

    print("\n" + "-" * 64)
    if best:
        best.sort(reverse=True)
        mod, t, density, ncomm = best[0]
        print(f"Suggested overlap_threshold: {t}")
        print(f"  modularity {mod:.3f} | density {density:.3f} | {ncomm} communities")
        print("\nSet it in config/config.yaml, then redeploy the analysis image.")
    else:
        print("No candidate produced a readable graph (density 0.02–0.30, 3+ communities).")
        print("Collect more survey days, or reconsider min_channel_viewers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
