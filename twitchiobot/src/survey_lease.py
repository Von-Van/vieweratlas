"""Mutual-exclusion lease for one-shot ViewerAtlas survey tasks."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Callable, Protocol

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:  # pragma: no cover - boto3 is part of the production image
    boto3 = None
    ClientError = Exception


logger = logging.getLogger(__name__)


class SurveyLease(Protocol):
    """Lease contract used by the survey runner."""

    def acquire(self, owner: str) -> bool: ...

    def renew(self, owner: str) -> None: ...

    def release(self, owner: str) -> None: ...


@dataclass
class NullSurveyLease:
    """No-op lease for local development and unit tests."""

    def acquire(self, owner: str) -> bool:
        return True

    def renew(self, owner: str) -> None:
        return None

    def release(self, owner: str) -> None:
        return None


class DynamoDBSurveyLease:
    """A renewable global lease stored in the collection-state table."""

    KEY = "lease#collector"

    def __init__(
        self,
        table_name: str,
        *,
        region: str = "us-east-1",
        lease_seconds: int = 7500,
        table=None,
        now: Callable[[], float] = time.time,
    ):
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if table is None:
            if boto3 is None:
                raise ImportError("boto3 is required for a DynamoDB survey lease")
            table = boto3.resource("dynamodb", region_name=region).Table(table_name)
        self.table = table
        self.lease_seconds = lease_seconds
        self._now = now

    def _times(self) -> tuple[int, int, int]:
        current = int(self._now())
        expires_at = current + self.lease_seconds
        # Keep the released/expired lock item briefly for operational inspection.
        ttl = expires_at + 86400
        return current, expires_at, ttl

    def acquire(self, owner: str) -> bool:
        if not owner:
            raise ValueError("lease owner must be non-empty")
        current, expires_at, ttl = self._times()
        try:
            self.table.put_item(
                Item={
                    "pk": self.KEY,
                    "lease_owner": owner,
                    "expires_at": expires_at,
                    "ttl": ttl,
                },
                ConditionExpression="attribute_not_exists(pk) OR expires_at < :now",
                ExpressionAttributeValues={":now": current},
            )
            logger.info("Survey lease acquired by %s", owner)
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                logger.warning("Survey lease is already held; owner %s will not run", owner)
                return False
            raise

    def renew(self, owner: str) -> None:
        _, expires_at, ttl = self._times()
        self.table.update_item(
            Key={"pk": self.KEY},
            UpdateExpression="SET expires_at = :expires, #ttl = :ttl",
            ConditionExpression="lease_owner = :owner",
            ExpressionAttributeNames={"#ttl": "ttl"},
            ExpressionAttributeValues={
                ":expires": expires_at,
                ":ttl": ttl,
                ":owner": owner,
            },
        )
        logger.debug("Survey lease renewed by %s", owner)

    def release(self, owner: str) -> None:
        try:
            self.table.delete_item(
                Key={"pk": self.KEY},
                ConditionExpression="lease_owner = :owner",
                ExpressionAttributeValues={":owner": owner},
            )
            logger.info("Survey lease released by %s", owner)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                logger.warning("Survey lease was no longer owned by %s", owner)
                return
            raise


def get_survey_lease() -> SurveyLease:
    """Build the production lease when DynamoDB is configured, else a local no-op."""

    table_name = os.getenv("DYNAMODB_STATE_TABLE", "").strip()
    storage_type = os.getenv("STORAGE_TYPE", "file").strip().lower()
    if storage_type != "s3" or not table_name:
        return NullSurveyLease()

    lease_seconds = int(os.getenv("SURVEY_LEASE_SECONDS", "7500"))
    return DynamoDBSurveyLease(
        table_name,
        region=os.getenv("AWS_REGION", os.getenv("S3_REGION", "us-east-1")),
        lease_seconds=lease_seconds,
    )
