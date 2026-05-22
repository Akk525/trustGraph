"""
Phase 4B tests — daily quota range query optimisation.

Covers:
- DynamoDBJobStore.count_for_user_since returns correct count
- Range condition limits results to jobs on or after `since`
- Select=COUNT is used (no item data transferred)
- No Scan is called
- LocalJobStore.count_for_user_since matches the same semantics
- quota.py daily check uses count_for_user_since
- quota.py active check still works via list_for_user
- anonymous user bypasses all quota checks
"""
from __future__ import annotations

import os
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

try:
    import boto3
    import moto
    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False

from trustgraph_cloud.jobs.models import Job, JobStatus


REGION = "us-east-1"
JOBS_TABLE = "test-jobs-4b"

_USER_INDEX = "user_id-created_at-index"
_STATUS_INDEX = "status-created_at-index"


def _create_jobs_table(client):
    client.create_table(
        TableName=JOBS_TABLE,
        KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
        AttributeDefinitions=[
            {"AttributeName": "job_id",     "AttributeType": "S"},
            {"AttributeName": "user_id",    "AttributeType": "S"},
            {"AttributeName": "created_at", "AttributeType": "S"},
            {"AttributeName": "status",     "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": _USER_INDEX,
                "KeySchema": [
                    {"AttributeName": "user_id",    "KeyType": "HASH"},
                    {"AttributeName": "created_at", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                "IndexName": _STATUS_INDEX,
                "KeySchema": [
                    {"AttributeName": "status",     "KeyType": "HASH"},
                    {"AttributeName": "created_at", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
        BillingMode="PAY_PER_REQUEST",
    )


def _job_at(user_id: str, ts: datetime) -> Job:
    """Create a Job with a specific created_at timestamp."""
    return Job(input_type="demo", user_id=user_id, created_at=ts)


# ---------------------------------------------------------------------------
# DynamoDBJobStore.count_for_user_since
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_MOTO, "moto not installed")
class TestDynamoDBCountForUserSince(unittest.TestCase):

    def setUp(self):
        self.mock = moto.mock_aws()
        self.mock.start()
        client = boto3.session.Session().client("dynamodb", region_name=REGION)
        _create_jobs_table(client)
        from trustgraph_cloud.jobs.dynamodb_store import DynamoDBJobStore
        self.store = DynamoDBJobStore(table_name=JOBS_TABLE, region=REGION)
        self.now = datetime.now(tz=timezone.utc)

    def tearDown(self):
        self.mock.stop()

    def _create_at(self, user_id: str, ts: datetime) -> Job:
        j = _job_at(user_id, ts)
        self.store.create(j)
        return j

    def test_count_returns_only_jobs_on_or_after_since(self):
        uid = "user-range"
        yesterday = self.now - timedelta(days=1)
        today_start = self.now.replace(hour=0, minute=0, second=0, microsecond=0)

        self._create_at(uid, yesterday)        # before since — should NOT count
        self._create_at(uid, self.now)          # after since — should count
        self._create_at(uid, self.now + timedelta(seconds=1))  # after — should count

        count = self.store.count_for_user_since(uid, today_start)
        self.assertEqual(count, 2)

    def test_count_includes_job_created_exactly_at_since(self):
        uid = "user-exact"
        since = self.now.replace(hour=0, minute=0, second=0, microsecond=0)
        self._create_at(uid, since)
        count = self.store.count_for_user_since(uid, since)
        self.assertEqual(count, 1)

    def test_count_returns_zero_when_all_jobs_before_since(self):
        uid = "user-old"
        yesterday = self.now - timedelta(days=2)
        self._create_at(uid, yesterday)
        self._create_at(uid, yesterday + timedelta(hours=1))

        since = self.now.replace(hour=0, minute=0, second=0, microsecond=0)
        count = self.store.count_for_user_since(uid, since)
        self.assertEqual(count, 0)

    def test_count_returns_zero_for_unknown_user(self):
        uid = "user-known"
        self._create_at(uid, self.now)
        count = self.store.count_for_user_since("user-unknown", self.now - timedelta(days=1))
        self.assertEqual(count, 0)

    def test_count_isolates_by_user(self):
        since = self.now - timedelta(hours=1)
        self._create_at("alice", self.now)
        self._create_at("alice", self.now)
        self._create_at("bob", self.now)

        self.assertEqual(self.store.count_for_user_since("alice", since), 2)
        self.assertEqual(self.store.count_for_user_since("bob", since), 1)

    def test_count_does_not_call_scan(self):
        uid = "user-noscan"
        self._create_at(uid, self.now)

        original_scan = self.store._client.scan
        scan_calls = []
        self.store._client.scan = lambda **kw: (scan_calls.append(kw), original_scan(**kw))[1]

        self.store.count_for_user_since(uid, self.now - timedelta(days=1))
        self.assertEqual(len(scan_calls), 0, "count_for_user_since must not call Scan")

    def test_count_uses_select_count(self):
        uid = "user-selectcount"
        self._create_at(uid, self.now)

        captured_kwargs = []
        original_query = self.store._client.query

        def capturing_query(**kwargs):
            captured_kwargs.append(kwargs)
            return original_query(**kwargs)

        self.store._client.query = capturing_query
        self.store.count_for_user_since(uid, self.now - timedelta(days=1))

        self.assertTrue(len(captured_kwargs) >= 1)
        self.assertEqual(captured_kwargs[0].get("Select"), "COUNT",
                         "count_for_user_since must use Select=COUNT")

    def test_count_uses_created_at_range_condition(self):
        uid = "user-rangecond"
        self._create_at(uid, self.now)

        captured_kwargs = []
        original_query = self.store._client.query

        def capturing_query(**kwargs):
            captured_kwargs.append(kwargs)
            return original_query(**kwargs)

        self.store._client.query = capturing_query
        since = self.now - timedelta(days=1)
        self.store.count_for_user_since(uid, since)

        expr = captured_kwargs[0].get("KeyConditionExpression", "")
        self.assertIn("created_at", expr,
                      "KeyConditionExpression must reference created_at")
        self.assertIn(">= :since", expr)

    def test_count_multiple_jobs_correct_total(self):
        uid = "user-many"
        since = self.now - timedelta(hours=1)
        for _ in range(7):
            self._create_at(uid, self.now)
        self.assertEqual(self.store.count_for_user_since(uid, since), 7)


# ---------------------------------------------------------------------------
# LocalJobStore.count_for_user_since
# ---------------------------------------------------------------------------

class TestLocalJobStoreCountForUserSince(unittest.TestCase):

    def setUp(self):
        import tempfile
        from pathlib import Path
        from trustgraph_cloud.jobs.store import LocalJobStore
        self._tmpdir = tempfile.TemporaryDirectory()
        self.store = LocalJobStore(Path(self._tmpdir.name))
        self.now = datetime.now(tz=timezone.utc)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _create_at(self, user_id: str, ts: datetime) -> Job:
        j = _job_at(user_id, ts)
        self.store.create(j)
        return j

    def test_count_returns_jobs_on_or_after_since(self):
        uid = "local-user"
        since = self.now.replace(hour=0, minute=0, second=0, microsecond=0)
        self._create_at(uid, self.now - timedelta(days=1))  # excluded
        self._create_at(uid, self.now)                       # included
        self._create_at(uid, self.now)                       # included
        self.assertEqual(self.store.count_for_user_since(uid, since), 2)

    def test_count_returns_zero_when_no_jobs_after_since(self):
        uid = "local-old"
        self._create_at(uid, self.now - timedelta(days=3))
        since = self.now - timedelta(days=1)
        self.assertEqual(self.store.count_for_user_since(uid, since), 0)

    def test_count_returns_zero_for_empty_store(self):
        self.assertEqual(
            self.store.count_for_user_since("nobody", self.now - timedelta(days=1)),
            0,
        )

    def test_count_isolates_by_user(self):
        since = self.now - timedelta(hours=1)
        self._create_at("alice", self.now)
        self._create_at("alice", self.now)
        self._create_at("bob", self.now)
        self.assertEqual(self.store.count_for_user_since("alice", since), 2)
        self.assertEqual(self.store.count_for_user_since("bob", since), 1)

    def test_count_exact_at_since_is_included(self):
        uid = "local-exact"
        since = self.now.replace(microsecond=0)
        self._create_at(uid, since)
        self.assertEqual(self.store.count_for_user_since(uid, since), 1)


# ---------------------------------------------------------------------------
# quota.py — daily check uses count_for_user_since
# ---------------------------------------------------------------------------

class TestQuotaDailyUsesRangeQuery(unittest.TestCase):
    """
    Unit tests against a mock job_store to verify quota.py calls the right
    methods. No DynamoDB required.
    """

    def _make_store(self, jobs: list[Job]) -> MagicMock:
        store = MagicMock()
        store.list_for_user.return_value = jobs
        store.count_for_user_since.return_value = len(jobs)
        return store

    def test_daily_quota_calls_count_for_user_since(self):
        from trustgraph_cloud.api.quota import check_quotas

        store = self._make_store([])
        check_quotas(store, "uid-1", max_audits_per_day=10, max_active_jobs=5)

        store.count_for_user_since.assert_called_once()
        args = store.count_for_user_since.call_args
        user_id_arg, since_arg = args[0]
        self.assertEqual(user_id_arg, "uid-1")
        self.assertIsInstance(since_arg, datetime)
        # since should be start of today UTC
        today_start = datetime.now(tz=timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        self.assertEqual(since_arg.date(), today_start.date())
        self.assertEqual(since_arg.hour, 0)
        self.assertEqual(since_arg.minute, 0)
        self.assertEqual(since_arg.second, 0)

    def test_active_quota_calls_list_for_user(self):
        from trustgraph_cloud.api.quota import check_quotas

        store = self._make_store([])
        check_quotas(store, "uid-2", max_audits_per_day=10, max_active_jobs=5)

        store.list_for_user.assert_called_once_with("uid-2")

    def test_anonymous_user_calls_neither(self):
        from trustgraph_cloud.api.quota import check_quotas

        store = self._make_store([])
        check_quotas(store, None, max_audits_per_day=1, max_active_jobs=1)

        store.list_for_user.assert_not_called()
        store.count_for_user_since.assert_not_called()

    def test_daily_quota_raises_429_when_limit_reached(self):
        from fastapi import HTTPException
        from trustgraph_cloud.api.quota import check_quotas

        store = MagicMock()
        store.list_for_user.return_value = []          # no active jobs
        store.count_for_user_since.return_value = 5    # at limit

        with self.assertRaises(HTTPException) as ctx:
            check_quotas(store, "uid-3", max_audits_per_day=5, max_active_jobs=10)
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertIn("Daily audit limit", ctx.exception.detail)

    def test_active_quota_raises_429_before_daily_check(self):
        """Active check fires first; count_for_user_since is not called."""
        from fastapi import HTTPException
        from trustgraph_cloud.api.quota import check_quotas

        active_jobs = [
            Job(input_type="demo", user_id="uid-4", status=JobStatus.QUEUED)
            for _ in range(3)
        ]
        store = MagicMock()
        store.list_for_user.return_value = active_jobs
        store.count_for_user_since.return_value = 0

        with self.assertRaises(HTTPException) as ctx:
            check_quotas(store, "uid-4", max_audits_per_day=100, max_active_jobs=3)
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertIn("Active job limit", ctx.exception.detail)
        store.count_for_user_since.assert_not_called()

    def test_daily_limit_not_triggered_when_under_limit(self):
        from trustgraph_cloud.api.quota import check_quotas

        store = MagicMock()
        store.list_for_user.return_value = []
        store.count_for_user_since.return_value = 4   # under limit of 5

        # Should not raise
        check_quotas(store, "uid-5", max_audits_per_day=5, max_active_jobs=10)


# ---------------------------------------------------------------------------
# Integration: daily quota with DynamoDB-backed store
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_MOTO, "moto not installed")
class TestDailyQuotaIntegration(unittest.TestCase):

    def setUp(self):
        self.mock = moto.mock_aws()
        self.mock.start()
        client = boto3.session.Session().client("dynamodb", region_name=REGION)
        _create_jobs_table(client)
        from trustgraph_cloud.jobs.dynamodb_store import DynamoDBJobStore
        self.store = DynamoDBJobStore(table_name=JOBS_TABLE, region=REGION)
        self.now = datetime.now(tz=timezone.utc)

    def tearDown(self):
        self.mock.stop()

    def test_daily_quota_blocks_when_limit_reached(self):
        from fastapi import HTTPException
        from trustgraph_cloud.api.quota import check_quotas

        uid = "daily-block"
        for _ in range(3):
            j = Job(input_type="demo", user_id=uid)
            self.store.create(j)

        with self.assertRaises(HTTPException) as ctx:
            check_quotas(self.store, uid, max_audits_per_day=3, max_active_jobs=100)
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertIn("Daily audit limit", ctx.exception.detail)

    def test_old_jobs_do_not_count_toward_daily_quota(self):
        from trustgraph_cloud.api.quota import check_quotas

        uid = "daily-old"
        yesterday = self.now - timedelta(days=1)
        # Create 10 jobs "yesterday" by inserting with backdated created_at
        for _ in range(10):
            j = _job_at(uid, yesterday)
            self.store.create(j)

        # Daily limit is 3 — but all jobs are from yesterday, so should not raise
        check_quotas(self.store, uid, max_audits_per_day=3, max_active_jobs=100)

    def test_active_quota_still_works_with_dynamo_store(self):
        from fastapi import HTTPException
        from trustgraph_cloud.api.quota import check_quotas

        uid = "active-check"
        for _ in range(3):
            j = Job(input_type="demo", user_id=uid, status=JobStatus.RUNNING)
            self.store.create(j)

        with self.assertRaises(HTTPException) as ctx:
            check_quotas(self.store, uid, max_audits_per_day=100, max_active_jobs=3)
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertIn("Active job limit", ctx.exception.detail)

    def test_succeeded_jobs_excluded_from_active_but_count_toward_daily(self):
        from fastapi import HTTPException
        from trustgraph_cloud.api.quota import check_quotas

        uid = "mixed-status"
        for _ in range(3):
            j = Job(input_type="demo", user_id=uid)
            self.store.create(j)
            self.store.update(j.job_id, status=JobStatus.SUCCEEDED)

        # Active: 0 (all succeeded) → no active quota violation
        # Daily: 3 (all created today) → triggers daily limit
        with self.assertRaises(HTTPException) as ctx:
            check_quotas(self.store, uid, max_audits_per_day=3, max_active_jobs=10)
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertIn("Daily audit limit", ctx.exception.detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
