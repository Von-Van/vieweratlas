"""SQS FIFO task queue for distributed channel collection.

The discovery service publishes ChannelTask messages to an SQS FIFO queue.
Worker processes consume messages, collect chatter data, and write Parquet
snapshots to S3.
"""

import json
import logging
from dataclasses import dataclass, asdict
from typing import List, Optional

logger = logging.getLogger(__name__)

try:
    import boto3
    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False


@dataclass
class ChannelTask:
    """A single channel collection task published to SQS."""
    channel: str
    cycle_id: str  # e.g. "2026-03-11T14:00:00"
    utc_day: str   # e.g. "2026-03-11"

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> "ChannelTask":
        return cls(**json.loads(raw))


class SQSTaskQueue:
    """Wrapper around an SQS FIFO queue for channel collection tasks."""

    def __init__(self, queue_url: str, region: str = "us-east-1", sqs_client=None):
        if not HAS_BOTO3 and sqs_client is None:
            raise ImportError("boto3 is required for SQSTaskQueue")
        self._client = sqs_client or boto3.client("sqs", region_name=region)
        self._queue_url = queue_url

    def publish_tasks(self, tasks: List[ChannelTask]) -> int:
        """Send tasks to the FIFO queue in batches of 10.

        Uses channel name as MessageGroupId so messages for the same channel
        are processed in order.  The dedup id is ``{cycle_id}#{channel}`` so
        SQS automatically drops duplicates within its 5-minute window.

        Returns the number of successfully sent messages.
        """
        sent = 0
        for i in range(0, len(tasks), 10):
            batch = tasks[i:i + 10]
            entries = []
            for idx, task in enumerate(batch):
                entries.append({
                    "Id": str(idx),
                    "MessageBody": task.to_json(),
                    "MessageGroupId": task.channel,
                    "MessageDeduplicationId": f"{task.cycle_id}#{task.channel}",
                })
            try:
                resp = self._client.send_message_batch(
                    QueueUrl=self._queue_url,
                    Entries=entries,
                )
                sent += len(resp.get("Successful", []))
                for fail in resp.get("Failed", []):
                    logger.error("SQS send failed for entry %s: %s", fail["Id"], fail.get("Message", ""))
            except Exception as e:
                logger.error("SQS send_message_batch failed: %s", e)
                raise
        return sent

    def receive_tasks(self, max_messages: int = 1, visibility_timeout: int = 900) -> List[dict]:
        """Receive up to max_messages tasks from the queue.

        Returns a list of dicts with keys: ``task`` (ChannelTask) and
        ``receipt_handle`` (str) for later deletion.
        """
        try:
            resp = self._client.receive_message(
                QueueUrl=self._queue_url,
                MaxNumberOfMessages=min(max_messages, 10),
                VisibilityTimeout=visibility_timeout,
                WaitTimeSeconds=20,  # long polling
            )
        except Exception as e:
            logger.error("SQS receive_message failed: %s", e)
            return []

        results = []
        for msg in resp.get("Messages", []):
            try:
                task = ChannelTask.from_json(msg["Body"])
                results.append({
                    "task": task,
                    "receipt_handle": msg["ReceiptHandle"],
                })
            except Exception as e:
                logger.error("Failed to parse SQS message %s: %s", msg.get("MessageId"), e)
        return results

    def delete_task(self, receipt_handle: str) -> bool:
        """Delete a processed message from the queue."""
        try:
            self._client.delete_message(
                QueueUrl=self._queue_url,
                ReceiptHandle=receipt_handle,
            )
            return True
        except Exception as e:
            logger.error("SQS delete_message failed: %s", e)
            return False
