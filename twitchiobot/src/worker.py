"""Collector Worker — consumes channel tasks from SQS and writes Parquet snapshots.

Each worker process polls the SQS FIFO queue, collects chatter data for
assigned channels via the Twitch API, and writes consolidated Parquet
snapshots to S3.

Usage:
    python worker.py                     # Use env vars for config
    python worker.py --batch-size 50     # Override batch size
"""

import argparse
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import PipelineConfig, SQSConfig
from daily_collection_state import DynamoDBCollectionState
from sqs_task_queue import SQSTaskQueue, ChannelTask
from storage import get_storage

logger = logging.getLogger(__name__)

_shutdown_requested = False


def _handle_sigterm(signum, frame):
    global _shutdown_requested
    _shutdown_requested = True
    logger.info("SIGTERM received, finishing current batch before exit...")


signal.signal(signal.SIGTERM, _handle_sigterm)


def _fetch_stream_info(channel: str, client_id: str, oauth_token: str) -> dict | None:
    """Fetch stream info for a single channel from the Twitch Helix API."""
    url = "https://api.twitch.tv/helix/streams"
    headers = {"Client-ID": client_id, "Authorization": f"Bearer {oauth_token}"}

    for attempt in range(3):
        try:
            resp = requests.get(url, headers=headers, params={"user_login": channel}, timeout=10)
            resp.raise_for_status()
            data = resp.json().get("data", [])
            return data[0] if data else None
        except requests.exceptions.HTTPError as e:
            if resp.status_code in (401, 403):
                logger.error("[%s] Auth failure: %s", channel, e)
                return None
        except Exception as e:
            logger.warning("[%s] Attempt %d failed: %s", channel, attempt + 1, e)
        if attempt < 2:
            time.sleep(2 ** attempt)

    logger.error("[%s] Failed after 3 retries", channel)
    return None


def _collect_channel(channel: str, client_id: str, oauth_token: str) -> dict | None:
    """Collect stream info and return a Parquet-ready row dict, or None on failure."""
    info = _fetch_stream_info(channel, client_id, oauth_token)
    if not info:
        return None

    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {
        "channel": channel,
        "timestamp": timestamp,
        "viewer_count": info.get("viewer_count", 0),
        "game_name": info.get("game_name", "Unknown"),
        "title": info.get("title", ""),
        "started_at": info.get("started_at", ""),
        "language": info.get("language", info.get("broadcaster_language", "")),
        "chatters_json": "[]",  # Worker captures metadata; chatters come from IRC bot
    }


def _flush_parquet(rows: list, storage, cycle_ts: str):
    """Write collected rows as a single Parquet file to storage."""
    import pandas as pd

    df = pd.DataFrame(rows)
    buf = BytesIO()
    df.to_parquet(buf, index=False, engine="pyarrow")
    parquet_bytes = buf.getvalue()

    date_partition = datetime.now(timezone.utc).strftime("%Y/%m/%d")
    key = f"raw/snapshots/{date_partition}/worker_{cycle_ts}.parquet"
    storage.upload_parquet(key, parquet_bytes)
    logger.info("Wrote %d channels to %s", len(rows), storage.get_uri(key))


def run_worker_loop(
    queue_url: str,
    dynamodb_table: str,
    region: str = "us-east-1",
    batch_size: int = 50,
    storage=None,
    sqs_client=None,
    dynamodb_resource=None,
    max_empty_polls: int = 3,
):
    """Poll SQS and process channel tasks until queue is empty or shutdown."""
    client_id = os.getenv("TWITCH_CLIENT_ID", "")
    oauth_token = os.getenv("TWITCH_OAUTH_TOKEN", "")
    if not client_id or not oauth_token:
        logger.error("TWITCH_CLIENT_ID and TWITCH_OAUTH_TOKEN must be set")
        return

    if storage is None:
        storage = get_storage()

    queue = SQSTaskQueue(queue_url=queue_url, region=region, sqs_client=sqs_client)
    state = DynamoDBCollectionState(
        table_name=dynamodb_table,
        region=region,
        dynamodb_resource=dynamodb_resource,
    )

    empty_polls = 0
    total_processed = 0

    while not _shutdown_requested:
        messages = queue.receive_tasks(max_messages=10)
        if not messages:
            empty_polls += 1
            if empty_polls >= max_empty_polls:
                logger.info("Queue empty after %d polls, exiting", max_empty_polls)
                break
            continue
        empty_polls = 0

        parquet_rows = []
        for msg in messages:
            task: ChannelTask = msg["task"]

            # DynamoDB dedup: skip if already collected
            if not state.mark_collected("live", task.channel, task.utc_day):
                logger.debug("[%s] Already collected, skipping", task.channel)
                queue.delete_task(msg["receipt_handle"])
                continue

            row = _collect_channel(task.channel, client_id, oauth_token)
            if row:
                parquet_rows.append(row)

            queue.delete_task(msg["receipt_handle"])
            total_processed += 1

        if parquet_rows:
            cycle_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            _flush_parquet(parquet_rows, storage, cycle_ts)
            # Write heartbeat for container health check
            Path(os.getenv("LOGS_DIR", "logs"), ".heartbeat").touch()

    logger.info("Worker processed %d tasks total", total_processed)


def main():
    parser = argparse.ArgumentParser(description="ViewerAtlas collector worker")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--max-empty-polls", type=int, default=3)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    queue_url = os.getenv("SQS_CHANNEL_QUEUE_URL", "")
    if not queue_url:
        logger.error("SQS_CHANNEL_QUEUE_URL not set")
        sys.exit(1)

    dynamodb_table = os.getenv("DYNAMODB_STATE_TABLE", "vieweratlas-collection-state")
    region = os.getenv("S3_REGION", "us-east-1")

    run_worker_loop(
        queue_url=queue_url,
        dynamodb_table=dynamodb_table,
        region=region,
        batch_size=args.batch_size,
        max_empty_polls=args.max_empty_polls,
    )


if __name__ == "__main__":
    main()
