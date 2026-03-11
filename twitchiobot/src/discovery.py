"""Discovery Service — publishes channel collection tasks to SQS.

Designed to run on a schedule (EventBridge every hour) or as a standalone
process.  Fetches top Twitch channels, filters already-collected channels
via DynamoDB, and publishes remaining channels to the SQS FIFO queue.

Usage:
    python discovery.py                     # Use env vars for config
    python discovery.py --limit 5000        # Override channel limit
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Ensure sibling modules are importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import PipelineConfig, SQSConfig
from daily_collection_state import DynamoDBCollectionState
from sqs_task_queue import SQSTaskQueue, ChannelTask
from update_channels import fetch_top_channels

logger = logging.getLogger(__name__)


def run_discovery(
    channel_limit: int = 5000,
    queue_url: str = "",
    dynamodb_table: str = "vieweratlas-collection-state",
    region: str = "us-east-1",
    sqs_client=None,
    dynamodb_resource=None,
) -> dict:
    """Discover live channels and publish collection tasks.

    Returns a summary dict with keys: discovered, skipped, published.
    """
    now = datetime.now(timezone.utc)
    utc_day = now.date().isoformat()
    cycle_id = now.isoformat(timespec="seconds")

    # 1. Fetch top channels from Twitch API
    logger.info("Fetching top %d channels from Twitch...", channel_limit)
    channels = fetch_top_channels(limit=channel_limit)
    logger.info("Discovered %d live channels", len(channels))

    # 2. Filter already-collected channels
    state = DynamoDBCollectionState(
        table_name=dynamodb_table,
        region=region,
        dynamodb_resource=dynamodb_resource,
    )
    new_channels = []
    skipped = 0
    for ch in channels:
        if state.has_collected("live", ch, utc_day):
            skipped += 1
        else:
            new_channels.append(ch)
    logger.info("Channels to collect: %d (skipped %d already collected)", len(new_channels), skipped)

    if not new_channels:
        logger.info("No new channels to collect this cycle")
        return {"discovered": len(channels), "skipped": skipped, "published": 0}

    # 3. Publish tasks to SQS
    queue = SQSTaskQueue(queue_url=queue_url, region=region, sqs_client=sqs_client)
    tasks = [ChannelTask(channel=ch, cycle_id=cycle_id, utc_day=utc_day) for ch in new_channels]
    sent = queue.publish_tasks(tasks)
    logger.info("Published %d tasks to SQS", sent)

    return {"discovered": len(channels), "skipped": skipped, "published": sent}


def main():
    parser = argparse.ArgumentParser(description="ViewerAtlas channel discovery service")
    parser.add_argument("--limit", type=int, default=5000, help="Max channels to fetch")
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

    result = run_discovery(
        channel_limit=args.limit,
        queue_url=queue_url,
        dynamodb_table=dynamodb_table,
        region=region,
    )
    logger.info("Discovery complete: %s", result)


if __name__ == "__main__":
    main()
