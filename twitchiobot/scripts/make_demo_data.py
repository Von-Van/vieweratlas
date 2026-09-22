#!/usr/bin/env python3
"""Write synthetic EventSub surveys so the analysis can run without Twitch.

Real survey files are personal data and never leave the private bucket, so a
fresh clone has nothing to analyse. This writes invented surveys in the exact
private v2 layout the collector produces (Parquet batches plus a completed
manifest per session), which lets `main.py analyze` and the frontend run end
to end on any machine.

The data is synthetic by construction: every channel and chatter is generated
(`demo_*` logins), audiences are drawn from planted communities, and a few
automated accounts are mixed in so the bot filter has something to remove.
Nothing here describes real Twitch activity, and results from it say only that
the pipeline works.

Usage (from twitchiobot/):
    python scripts/make_demo_data.py                  # writes demo_data/
    STORAGE_TYPE=file LOGS_DIR=demo_data python src/main.py analyze rigorous
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pandas as pd

# (category, broadcaster language) per planted community. Two are deliberately
# multi-game so the tagger's "Variety (lang)" path is exercised as well.
COMMUNITIES = [
    (("Chess",), "en"),
    (("Minecraft",), "en"),
    (("VALORANT",), "en"),
    (("Just Chatting",), "es"),
    (("League of Legends",), "ko"),
    (("Art", "Music", "Software and Game Development", "Retro"), "ja"),
    (("Counter-Strike",), "pt"),
    (("Just Chatting", "Grand Theft Auto V", "Fortnite", "EA Sports FC 26"), "de"),
]
CHANNELS_PER_COMMUNITY = 24
POOL_PER_COMMUNITY = 800        # regular chatters of one community
CASUAL_POOL = 20000             # chatters who wander between communities
SHARED_WITH_NEIGHBOUR = 0.10    # pool overlap with the next community (a ring)
CASUAL_SHARE = 0.08             # share of each sample drawn from casual chatters
MAX_PER_WINDOW = 2              # a person keeps up with at most two chats at once

SURVEY_HOURS_UTC = (10, 18, 2)  # roughly 6 AM, 2 PM and 10 PM Eastern
BATCH_SIZE = 100
WINDOW_SECONDS = 300
PRESENCE_RATE = 0.85            # chance a channel is live and ranked in a survey

KNOWN_BOTS = {"nightbot": 0.5, "streamelements": 0.4}
FARM_ACCOUNTS = 5               # each active in six chats per window
FARM_CHANNELS_PER_WINDOW = 6


def _pools(rng: random.Random) -> list[list[str]]:
    """Regular chatters per community, with a slice shared with the next one."""
    own = [
        [f"demo_c{index}_viewer{n:04d}" for n in range(POOL_PER_COMMUNITY)]
        for index in range(len(COMMUNITIES))
    ]
    shared = int(POOL_PER_COMMUNITY * SHARED_WITH_NEIGHBOUR)
    pools = []
    for index, members in enumerate(own):
        neighbour = own[(index + 1) % len(own)]
        pools.append(members + rng.sample(neighbour, shared))
    return pools


def _channels(rng: random.Random) -> list[dict]:
    channels = []
    for community, (games, language) in enumerate(COMMUNITIES):
        for n in range(CHANNELS_PER_COMMUNITY):
            channels.append({
                "login": f"demo_c{community}_ch{n:02d}",
                "user_id": str(700_000_000 + community * 100 + n),
                "community": community,
                "games": games,
                "language": language,
                # Log-normal audience sizes, as on Twitch: a few large channels
                # and a long tail.
                "base_viewers": int(rng.lognormvariate(7.5, 0.9)),
                "chat_rate": rng.uniform(0.6, 1.4),
            })
    return channels


def _draw(rng, pool, cum_weights, count, used, cap):
    """Draw up to ``count`` distinct chatters who are under the window cap."""
    chosen: set[str] = set()
    for _ in range(10):
        if len(chosen) >= count:
            break
        for login in rng.choices(pool, cum_weights=cum_weights, k=count * 2):
            if len(chosen) >= count:
                break
            if login not in chosen and used.get(login, 0) < cap:
                chosen.add(login)
    for login in chosen:
        used[login] = used.get(login, 0) + 1
    return chosen


def _row(session_id, batch, rank, channel, started, ended, authors, ids,
         game, viewers, discovered):
    aligned = sorted((ids[login], login) for login in authors)
    return {
        "schema_version": 2,
        "survey_session_id": session_id,
        "batch": batch,
        "rank": rank,
        "selection_source": "top_ranked",
        "channel_id": channel["user_id"],
        "channel": channel["login"],
        "channel_login": channel["login"],
        "viewer_count": viewers,
        "game_id": "",
        "game_name": game,
        "language": channel["language"],
        "title": "Synthetic demo stream",
        "started_at": discovered,
        "discovered_at": discovered,
        "timestamp": started,
        "sample_started_at": started,
        "sample_ended_at": ended,
        "sample_duration_seconds": WINDOW_SECONDS,
        "collection_status": "completed",
        "failure_reason": "",
        "unique_author_count": len(aligned),
        "chatter_ids_json": json.dumps([i for i, _ in aligned], separators=(",", ":")),
        "chatters_json": json.dumps([login for _, login in aligned], separators=(",", ":")),
        "_source": "live",
    }


def generate(out: Path, days: int, end: date, seed: int) -> tuple[int, int]:
    rng = random.Random(seed)
    pools = _pools(rng)
    casual = [f"demo_casual{n:05d}" for n in range(CASUAL_POOL)]
    farm = [f"demo_farm{n}" for n in range(FARM_ACCOUNTS)]
    channels = _channels(rng)

    # Zipf-like weights: a community's regulars chat far more often than its
    # tail. Cumulative, because random.choices re-sums plain weights every call.
    weights = [
        list(itertools.accumulate(1.0 / (1 + rank) ** 0.8 for rank in range(len(pool))))
        for pool in pools
    ]
    casual_weights = list(itertools.accumulate([1.0] * len(casual)))

    everyone = sorted({login for pool in pools for login in pool} | set(casual)
                      | set(farm) | set(KNOWN_BOTS))
    ids = {login: str(900_000_000 + index) for index, login in enumerate(everyone)}

    sessions = rows_written = 0
    for offset in range(days):
        day = end - timedelta(days=days - 1 - offset)
        for hour in SURVEY_HOURS_UTC:
            started_at = datetime.combine(day, time(hour, 0), tzinfo=timezone.utc)
            session_id = started_at.strftime("%Y%m%dT%H%M%S%fZ")
            discovered = started_at.isoformat()

            live = []
            for channel in channels:
                if rng.random() > PRESENCE_RATE:
                    continue
                viewers = max(1, int(channel["base_viewers"] * rng.uniform(0.7, 1.3)))
                game = channel["games"][0]
                if len(channel["games"]) > 1 or rng.random() < 0.1:
                    game = rng.choice(channel["games"] + ("Just Chatting",))
                live.append((viewers, channel, game))
            # The collector freezes the cohort by Helix rank, i.e. by viewers.
            live.sort(key=lambda item: (-item[0], item[1]["login"]))

            prefix = out / "raw" / "snapshots" / "v2" / f"date={day.isoformat()}" / f"session={session_id}"
            prefix.mkdir(parents=True, exist_ok=True)
            manifest_batches = []
            batches = [live[i:i + BATCH_SIZE] for i in range(0, len(live), BATCH_SIZE)]
            for batch_index, batch in enumerate(batches, start=1):
                window_start = started_at + timedelta(minutes=7 * (batch_index - 1))
                window_end = window_start + timedelta(seconds=WINDOW_SECONDS)
                used: dict[str, int] = {}
                rows = []
                for position, (viewers, channel, game) in enumerate(batch):
                    rank = (batch_index - 1) * BATCH_SIZE + position + 1
                    count = max(3, int(rng.gauss(32, 8) * channel["chat_rate"]))
                    community = channel["community"]
                    regulars = _draw(rng, pools[community], weights[community],
                                     int(count * (1 - CASUAL_SHARE)), used, MAX_PER_WINDOW)
                    casuals = _draw(rng, casual, casual_weights,
                                    int(count * CASUAL_SHARE), used, MAX_PER_WINDOW)
                    authors = regulars | casuals
                    authors |= {bot for bot, rate in KNOWN_BOTS.items() if rng.random() < rate}
                    rows.append([viewers, channel, game, rank, authors])
                # The farm chats in step across several channels of one window,
                # which is exactly what the concurrency rule looks for.
                for account in farm:
                    for row in rng.sample(rows, min(FARM_CHANNELS_PER_WINDOW, len(rows))):
                        row[4].add(account)

                frame = pd.DataFrame([
                    _row(session_id, batch_index, rank, channel,
                         window_start.isoformat(), window_end.isoformat(), authors, ids,
                         game, viewers, discovered)
                    for viewers, channel, game, rank, authors in rows
                ])
                key = prefix / f"batch={batch_index:02d}.parquet"
                frame.to_parquet(key, index=False, engine="pyarrow")
                rows_written += len(frame)
                manifest_batches.append({
                    "batch": batch_index,
                    "status": "complete",
                    "planned": len(batch),
                    "completed": len(batch),
                    "failed": 0,
                    "zero_authors": 0,
                    "object_key": str(key.relative_to(out)).replace("\\", "/"),
                })

            completed_at = started_at + timedelta(minutes=7 * len(batches))
            manifest = {
                "schema_version": 2,
                "survey_session_id": session_id,
                "status": "complete",
                "started_at": started_at.isoformat(),
                "completed_at": completed_at.isoformat(),
                "target_limit": 1200,
                "batch_size": BATCH_SIZE,
                "window_seconds": WINDOW_SECONDS,
                "timeout_seconds": 7200,
                "planned": len(live),
                "attempted": len(live),
                "completed": len(live),
                "failed": 0,
                "zero_authors": 0,
                "batches_planned": len(batches),
                "batches_completed": len(batches),
                "batches": manifest_batches,
                "synthetic": True,
            }
            (prefix / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            sessions += 1
    return sessions, rows_written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", default="demo_data",
                        help="Storage root to write into (default: demo_data)")
    parser.add_argument("--days", type=int, default=30,
                        help="Days of surveys, three per day (default: 30)")
    parser.add_argument("--end-date", type=date.fromisoformat, default=date(2026, 1, 31),
                        help="Last survey day, YYYY-MM-DD (default: 2026-01-31)")
    parser.add_argument("--seed", type=int, default=7, help="Random seed (default: 7)")
    args = parser.parse_args()
    if args.days < 1:
        parser.error("--days must be at least 1")

    out = Path(args.out)
    existing = out / "raw" / "snapshots" / "v2"
    if existing.exists() and any(existing.iterdir()):
        print(f"{existing} already holds surveys. Choose an empty --out so synthetic "
              "data never mixes with real collections.", file=sys.stderr)
        return 1

    sessions, rows = generate(out, args.days, args.end_date, args.seed)
    print(f"Wrote {sessions} synthetic surveys ({rows:,} channel rows) under {existing}")
    print("Next, from twitchiobot/:")
    print(f"  STORAGE_TYPE=file LOGS_DIR={out} python src/main.py analyze rigorous")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
