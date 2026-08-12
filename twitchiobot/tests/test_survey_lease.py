from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from survey_lease import DynamoDBSurveyLease, NullSurveyLease, get_survey_lease


def conditional_failure(operation: str = "PutItem") -> ClientError:
    return ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "held"}},
        operation,
    )


class FakeTable:
    def __init__(self):
        self.put_calls = []
        self.update_calls = []
        self.delete_calls = []
        self.put_error = None
        self.delete_error = None

    def put_item(self, **kwargs):
        self.put_calls.append(kwargs)
        if self.put_error:
            raise self.put_error

    def update_item(self, **kwargs):
        self.update_calls.append(kwargs)

    def delete_item(self, **kwargs):
        self.delete_calls.append(kwargs)
        if self.delete_error:
            raise self.delete_error


def test_acquire_uses_expiring_conditional_lease():
    table = FakeTable()
    lease = DynamoDBSurveyLease("state", lease_seconds=100, table=table, now=lambda: 1000)

    assert lease.acquire("session-1") is True
    call = table.put_calls[0]
    assert call["Item"]["pk"] == "lease#collector"
    assert call["Item"]["lease_owner"] == "session-1"
    assert call["Item"]["expires_at"] == 1100
    assert "expires_at < :now" in call["ConditionExpression"]


def test_acquire_returns_false_when_another_task_holds_lease():
    table = FakeTable()
    table.put_error = conditional_failure()
    lease = DynamoDBSurveyLease("state", table=table)
    assert lease.acquire("session-2") is False


def test_renew_and_release_require_same_owner():
    table = FakeTable()
    lease = DynamoDBSurveyLease("state", table=table, now=lambda: 10)

    lease.renew("session-3")
    lease.release("session-3")

    assert table.update_calls[0]["ExpressionAttributeValues"][":owner"] == "session-3"
    assert table.delete_calls[0]["ExpressionAttributeValues"][":owner"] == "session-3"


def test_release_ignores_lost_lease():
    table = FakeTable()
    table.delete_error = conditional_failure("DeleteItem")
    lease = DynamoDBSurveyLease("state", table=table)
    lease.release("old-owner")


def test_local_environment_uses_noop_lease(monkeypatch):
    monkeypatch.setenv("STORAGE_TYPE", "file")
    monkeypatch.delenv("DYNAMODB_STATE_TABLE", raising=False)
    lease = get_survey_lease()
    assert isinstance(lease, NullSurveyLease)
    assert lease.acquire("local") is True
