#!/usr/bin/env python3
"""Inspect a survey's Parquet batches to confirm chatters were actually captured.

A survey can report SURVEY_COMPLETED while capturing nothing: an empty author
list is a valid observation, so a broken subscription looks like a quiet channel.
This prints per-channel author counts so that case is obvious.

Usage:
    python3 scripts/inspect_survey.py <dir-of-downloaded-parquet>
    python3 scripts/inspect_survey.py s3://bucket/prefix/raw/snapshots/v2/date=.../session=.../
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

COLUMNS = [
    "batch", "rank", "channel_login", "collection_status",
    "unique_author_count", "sample_duration_seconds", "failure_reason",
]


def load(source: str) -> Path:
    if not source.startswith("s3://"):
        return Path(source)
    tmp = Path(tempfile.mkdtemp())
    subprocess.run(
        ["aws", "s3", "cp", source, str(tmp), "--recursive", "--exclude", "*",
         "--include", "*.parquet", "--include", "manifest.json"],
        check=True, capture_output=True,
    )
    return tmp


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    root = load(sys.argv[1])
    files = sorted(root.rglob("*.parquet"))
    if not files:
        print(f"No .parquet files under {root}")
        return 1

    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    print(f"{len(files)} batch file(s), {len(df)} channel rows\n")

    for manifest in sorted(root.rglob("manifest.json")):
        m = json.loads(manifest.read_text())
        print("manifest:", json.dumps(
            {k: m.get(k) for k in
             ("status", "batches_planned", "batches_completed",
              "completed", "failed", "zero_authors")},
            indent=2))
        print()

    print(df[[c for c in COLUMNS if c in df.columns]].to_string(index=False))

    completed = df[df["collection_status"] == "completed"]
    total = int(completed["unique_author_count"].sum())
    silent = int((completed["unique_author_count"] == 0).sum())

    print(f"\n{'-' * 60}")
    print(f"  completed channels     : {len(completed)}")
    print(f"  total unique authors   : {total}")
    print(f"  channels with 0 authors: {silent}")

    # Alignment is the invariant the analysis loader depends on.
    bad = 0
    for _, row in completed.iterrows():
        ids = json.loads(row["chatter_ids_json"])
        logins = json.loads(row["chatters_json"])
        if len(ids) != len(logins) or len(ids) != row["unique_author_count"]:
            bad += 1
            print(f"  MISALIGNED: {row['channel_login']} "
                  f"ids={len(ids)} logins={len(logins)} count={row['unique_author_count']}")
    print(f"  id/login arrays aligned: {'yes' if bad == 0 else f'NO ({bad} rows)'}")

    if len(completed) and silent == len(completed):
        print("\n  WARNING: every completed channel captured zero authors.")
        print("  That is the signature of subscriptions never delivering messages,")
        print("  not of genuinely quiet channels. Check the survey logs.")
        return 1

    sample = completed[completed["unique_author_count"] > 0].head(1)
    if len(sample):
        row = sample.iloc[0]
        logins = json.loads(row["chatters_json"])
        print(f"\n  sample from {row['channel_login']}: "
              f"{len(logins)} authors, first 5 = {logins[:5]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
