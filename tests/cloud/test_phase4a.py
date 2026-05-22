"""
Phase 4A tests — DynamoDB GSIs for TrustGraph Cloud job/auth lookups.

Covers:
- CDK template has all expected GSIs on jobs/users/api_keys tables
- DynamoDBJobStore.list_for_user uses GSI query (not scan)
- list_for_user returns newest-first
- list_for_user isolates per user
- list_for_user(None) falls back to scan for anonymous jobs
- DynamoDBUserStore.get_by_email uses email-index query
- DynamoDBApiKeyStore.get_by_hash uses key_hash-index query
- DynamoDBApiKeyStore.list_for_user uses user_id-created_at-index query
- Quota checks still work correctly via GSI-backed store
- IAM policies include dynamodb:Query on table + index resources
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

try:
    import boto3
    import moto
    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False

try:
    import aws_cdk as cdk
    from aws_cdk import assertions as cdk_assertions
    _CDK_DIR = Path(__file__).parent.parent.parent / "infra" / "cdk"
    sys.path.insert(0, str(_CDK_DIR))
    from stacks.trustgraph_worker_stack import (
        TrustGraphWorkerStack,
        API_KEYS_HASH_INDEX,
        API_KEYS_TABLE_NAME,
        API_KEYS_USER_INDEX,
        JOBS_STATUS_INDEX,
        JOBS_USER_INDEX,
        USERS_EMAIL_INDEX,
        USERS_TABLE_NAME,
        DYNAMODB_TABLE_NAME,
    )
    HAS_CDK = True
except ImportError:
    HAS_CDK = False

from trustgraph_cloud.jobs.models import Job, JobStatus

REGION = "us-east-1"
JOBS_TABLE = "test-jobs"
USERS_TABLE = "test-users"
KEYS_TABLE = "test-api-keys"


# ---------------------------------------------------------------------------
# Table creation helpers (match production schema from CDK)
# ---------------------------------------------------------------------------

def _create_jobs_table(client, table_name: str = JOBS_TABLE) -> None:
    client.create_table(
        TableName=table_name,
        KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
        AttributeDefinitions=[
            {"AttributeName": "job_id",     "AttributeType": "S"},
            {"AttributeName": "user_id",    "AttributeType": "S"},
            {"AttributeName": "created_at", "AttributeType": "S"},
            {"AttributeName": "status",     "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": JOBS_USER_INDEX if HAS_CDK else "user_id-created_at-index",
                "KeySchema": [
                    {"AttributeName": "user_id",    "KeyType": "HASH"},
                    {"AttributeName": "created_at", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                "IndexName": JOBS_STATUS_INDEX if HAS_CDK else "status-created_at-index",
                "KeySchema": [
                    {"AttributeName": "status",     "KeyType": "HASH"},
                    {"AttributeName": "created_at", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
        BillingMode="PAY_PER_REQUEST",
    )


def _create_users_table(client, table_name: str = USERS_TABLE) -> None:
    client.create_table(
        TableName=table_name,
        KeySchema=[{"AttributeName": "user_id", "KeyType": "HASH"}],
        AttributeDefinitions=[
            {"AttributeName": "user_id", "AttributeType": "S"},
            {"AttributeName": "email",   "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": USERS_EMAIL_INDEX if HAS_CDK else "email-index",
                "KeySchema": [
                    {"AttributeName": "email", "KeyType": "HASH"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
        BillingMode="PAY_PER_REQUEST",
    )


def _create_api_keys_table(client, table_name: str = KEYS_TABLE) -> None:
    client.create_table(
        TableName=table_name,
        KeySchema=[{"AttributeName": "key_id", "KeyType": "HASH"}],
        AttributeDefinitions=[
            {"AttributeName": "key_id",     "AttributeType": "S"},
            {"AttributeName": "key_hash",   "AttributeType": "S"},
            {"AttributeName": "user_id",    "AttributeType": "S"},
            {"AttributeName": "created_at", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": API_KEYS_HASH_INDEX if HAS_CDK else "key_hash-index",
                "KeySchema": [
                    {"AttributeName": "key_hash", "KeyType": "HASH"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                "IndexName": API_KEYS_USER_INDEX if HAS_CDK else "user_id-created_at-index",
                "KeySchema": [
                    {"AttributeName": "user_id",    "KeyType": "HASH"},
                    {"AttributeName": "created_at", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
        BillingMode="PAY_PER_REQUEST",
    )


# ---------------------------------------------------------------------------
# CDK GSI assertions
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_CDK, "aws-cdk-lib not installed")
class TestCDKGSIs(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        app = cdk.App()
        cls.stack = TrustGraphWorkerStack(
            app,
            "TestStack",
            env=cdk.Environment(account="123456789012", region="us-east-1"),
        )
        cls.template = cdk_assertions.Template.from_stack(cls.stack)

    # Jobs table GSIs

    def test_jobs_table_has_user_created_at_gsi(self):
        self.template.has_resource_properties(
            "AWS::DynamoDB::Table",
            cdk_assertions.Match.object_like({
                "TableName": DYNAMODB_TABLE_NAME,
                "GlobalSecondaryIndexes": cdk_assertions.Match.array_with([
                    cdk_assertions.Match.object_like({
                        "IndexName": JOBS_USER_INDEX,
                        "KeySchema": cdk_assertions.Match.array_with([
                            cdk_assertions.Match.object_like({
                                "AttributeName": "user_id",
                                "KeyType": "HASH",
                            })
                        ]),
                    })
                ]),
            }),
        )

    def test_jobs_table_has_status_created_at_gsi(self):
        self.template.has_resource_properties(
            "AWS::DynamoDB::Table",
            cdk_assertions.Match.object_like({
                "TableName": DYNAMODB_TABLE_NAME,
                "GlobalSecondaryIndexes": cdk_assertions.Match.array_with([
                    cdk_assertions.Match.object_like({
                        "IndexName": JOBS_STATUS_INDEX,
                        "KeySchema": cdk_assertions.Match.array_with([
                            cdk_assertions.Match.object_like({
                                "AttributeName": "status",
                                "KeyType": "HASH",
                            })
                        ]),
                    })
                ]),
            }),
        )

    def test_jobs_gsis_project_all(self):
        self.template.has_resource_properties(
            "AWS::DynamoDB::Table",
            cdk_assertions.Match.object_like({
                "TableName": DYNAMODB_TABLE_NAME,
                "GlobalSecondaryIndexes": cdk_assertions.Match.array_with([
                    cdk_assertions.Match.object_like({
                        "Projection": {"ProjectionType": "ALL"},
                    })
                ]),
            }),
        )

    # Users table GSI

    def test_users_table_has_email_gsi(self):
        self.template.has_resource_properties(
            "AWS::DynamoDB::Table",
            cdk_assertions.Match.object_like({
                "TableName": USERS_TABLE_NAME,
                "GlobalSecondaryIndexes": cdk_assertions.Match.array_with([
                    cdk_assertions.Match.object_like({
                        "IndexName": USERS_EMAIL_INDEX,
                        "KeySchema": cdk_assertions.Match.array_with([
                            cdk_assertions.Match.object_like({
                                "AttributeName": "email",
                                "KeyType": "HASH",
                            })
                        ]),
                    })
                ]),
            }),
        )

    # API keys table GSIs

    def test_api_keys_table_has_key_hash_gsi(self):
        self.template.has_resource_properties(
            "AWS::DynamoDB::Table",
            cdk_assertions.Match.object_like({
                "TableName": API_KEYS_TABLE_NAME,
                "GlobalSecondaryIndexes": cdk_assertions.Match.array_with([
                    cdk_assertions.Match.object_like({
                        "IndexName": API_KEYS_HASH_INDEX,
                    })
                ]),
            }),
        )

    def test_api_keys_table_has_user_created_at_gsi(self):
        self.template.has_resource_properties(
            "AWS::DynamoDB::Table",
            cdk_assertions.Match.object_like({
                "TableName": API_KEYS_TABLE_NAME,
                "GlobalSecondaryIndexes": cdk_assertions.Match.array_with([
                    cdk_assertions.Match.object_like({
                        "IndexName": API_KEYS_USER_INDEX,
                    })
                ]),
            }),
        )

    # IAM — dynamodb:Query in policies

    def test_api_task_role_has_dynamodb_query(self):
        self.template.has_resource_properties(
            "AWS::IAM::Policy",
            cdk_assertions.Match.object_like({
                "PolicyDocument": cdk_assertions.Match.object_like({
                    "Statement": cdk_assertions.Match.array_with([
                        cdk_assertions.Match.object_like({
                            "Action": cdk_assertions.Match.array_with([
                                "dynamodb:Query",
                            ]),
                            "Effect": "Allow",
                        })
                    ])
                })
            }),
        )

    def test_worker_task_role_has_dynamodb_query(self):
        self.template.has_resource_properties(
            "AWS::IAM::Policy",
            cdk_assertions.Match.object_like({
                "PolicyDocument": cdk_assertions.Match.object_like({
                    "Statement": cdk_assertions.Match.array_with([
                        cdk_assertions.Match.object_like({
                            "Sid": "DynamoDBJobStore",
                            "Action": cdk_assertions.Match.array_with([
                                "dynamodb:Query",
                            ]),
                            "Effect": "Allow",
                        })
                    ])
                })
            }),
        )


# ---------------------------------------------------------------------------
# DynamoDBJobStore — GSI query path
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_MOTO, "moto not installed")
class TestDynamoDBJobStoreListForUser(unittest.TestCase):

    def setUp(self):
        self.mock = moto.mock_aws()
        self.mock.start()
        client = boto3.session.Session().client("dynamodb", region_name=REGION)
        _create_jobs_table(client)
        from trustgraph_cloud.jobs.dynamodb_store import DynamoDBJobStore
        self.store = DynamoDBJobStore(table_name=JOBS_TABLE, region=REGION)

    def tearDown(self):
        self.mock.stop()

    def test_list_for_user_returns_only_that_users_jobs(self):
        j1 = Job(input_type="demo", user_id="user-alice")
        j2 = Job(input_type="demo", user_id="user-alice")
        j3 = Job(input_type="demo", user_id="user-bob")
        for j in (j1, j2, j3):
            self.store.create(j)
        result = self.store.list_for_user("user-alice")
        ids = {j.job_id for j in result}
        self.assertIn(j1.job_id, ids)
        self.assertIn(j2.job_id, ids)
        self.assertNotIn(j3.job_id, ids)

    def test_list_for_user_returns_empty_for_unknown_user(self):
        self.store.create(Job(input_type="demo", user_id="user-alice"))
        result = self.store.list_for_user("user-nobody")
        self.assertEqual(result, [])

    def test_list_for_user_returns_newest_first(self):
        jobs = []
        for _ in range(4):
            j = Job(input_type="demo", user_id="user-x")
            self.store.create(j)
            time.sleep(0.01)  # ensure distinct created_at timestamps
            jobs.append(j)
        result = self.store.list_for_user("user-x")
        self.assertEqual(len(result), 4)
        timestamps = [j.created_at for j in result]
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))

    def test_list_for_user_none_returns_anonymous_jobs(self):
        anon = Job(input_type="demo", user_id=None)
        owned = Job(input_type="demo", user_id="user-x")
        self.store.create(anon)
        self.store.create(owned)
        result = self.store.list_for_user(None)
        ids = {j.job_id for j in result}
        self.assertIn(anon.job_id, ids)
        self.assertNotIn(owned.job_id, ids)

    def test_list_for_user_does_not_call_scan(self):
        self.store.create(Job(input_type="demo", user_id="user-x"))
        original_scan = self.store._client.scan
        scan_calls = []
        self.store._client.scan = lambda **kw: (scan_calls.append(kw), original_scan(**kw))[1]
        self.store.list_for_user("user-x")
        self.assertEqual(len(scan_calls), 0, "list_for_user must not call Scan for authenticated users")

    def test_list_for_user_respects_status_updates(self):
        job = Job(input_type="demo", user_id="user-y")
        self.store.create(job)
        self.store.update(job.job_id, status=JobStatus.RUNNING)
        result = self.store.list_for_user("user-y")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].status, JobStatus.RUNNING)

    def test_list_for_user_multiple_users_isolated(self):
        for uid in ("u1", "u2", "u3"):
            for _ in range(3):
                self.store.create(Job(input_type="demo", user_id=uid))
        self.assertEqual(len(self.store.list_for_user("u1")), 3)
        self.assertEqual(len(self.store.list_for_user("u2")), 3)
        self.assertEqual(len(self.store.list_for_user("u3")), 3)


# ---------------------------------------------------------------------------
# DynamoDBUserStore — email-index query path
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_MOTO, "moto not installed")
class TestDynamoDBUserStoreEmailIndex(unittest.TestCase):

    def setUp(self):
        self.mock = moto.mock_aws()
        self.mock.start()
        client = boto3.session.Session().client("dynamodb", region_name=REGION)
        _create_users_table(client)
        from trustgraph_cloud.auth.dynamodb_auth import DynamoDBUserStore
        from trustgraph_cloud.auth.models import User
        self.store = DynamoDBUserStore(table_name=USERS_TABLE, region=REGION)
        self.User = User

    def tearDown(self):
        self.mock.stop()

    def _make_user(self, email: str) -> object:
        return self.User(email=email, password_hash="hash")

    def test_get_by_email_finds_created_user(self):
        user = self._make_user("alice@example.com")
        self.store.create(user)
        found = self.store.get_by_email("alice@example.com")
        self.assertIsNotNone(found)
        self.assertEqual(found.user_id, user.user_id)

    def test_get_by_email_case_insensitive(self):
        user = self._make_user("Alice@Example.COM")
        self.store.create(user)
        self.assertIsNotNone(self.store.get_by_email("alice@example.com"))
        self.assertIsNotNone(self.store.get_by_email("ALICE@EXAMPLE.COM"))

    def test_get_by_email_returns_none_for_unknown(self):
        result = self.store.get_by_email("nobody@example.com")
        self.assertIsNone(result)

    def test_get_by_email_does_not_call_scan(self):
        user = self._make_user("b@example.com")
        self.store.create(user)
        original_scan = self.store._client.scan
        scan_calls = []
        self.store._client.scan = lambda **kw: (scan_calls.append(kw), original_scan(**kw))[1]
        self.store.get_by_email("b@example.com")
        self.assertEqual(len(scan_calls), 0, "get_by_email must not call Scan")

    def test_multiple_users_no_cross_lookup(self):
        u1 = self._make_user("user1@example.com")
        u2 = self._make_user("user2@example.com")
        self.store.create(u1)
        self.store.create(u2)
        found = self.store.get_by_email("user1@example.com")
        self.assertEqual(found.user_id, u1.user_id)
        found2 = self.store.get_by_email("user2@example.com")
        self.assertEqual(found2.user_id, u2.user_id)


# ---------------------------------------------------------------------------
# DynamoDBApiKeyStore — key_hash-index and user_id-created_at-index
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_MOTO, "moto not installed")
class TestDynamoDBApiKeyStoreGSI(unittest.TestCase):

    def setUp(self):
        self.mock = moto.mock_aws()
        self.mock.start()
        client = boto3.session.Session().client("dynamodb", region_name=REGION)
        _create_api_keys_table(client)
        from trustgraph_cloud.auth.dynamodb_auth import DynamoDBApiKeyStore
        from trustgraph_cloud.auth.models import ApiKey
        self.store = DynamoDBApiKeyStore(table_name=KEYS_TABLE, region=REGION)
        self.ApiKey = ApiKey

    def tearDown(self):
        self.mock.stop()

    def _make_key(self, user_id: str = "uid-1", key_hash: str = "hash-abc") -> object:
        return self.ApiKey(
            user_id=user_id,
            key_prefix="tg_live_test12",
            key_hash=key_hash,
            name="test",
        )

    def test_get_by_hash_finds_key(self):
        key = self._make_key(key_hash="sha256hashvalue")
        self.store.create(key)
        found = self.store.get_by_hash("sha256hashvalue")
        self.assertIsNotNone(found)
        self.assertEqual(found.key_id, key.key_id)

    def test_get_by_hash_returns_none_for_unknown(self):
        result = self.store.get_by_hash("nonexistent-hash")
        self.assertIsNone(result)

    def test_get_by_hash_does_not_call_scan(self):
        key = self._make_key(key_hash="h1")
        self.store.create(key)
        original_scan = self.store._client.scan
        scan_calls = []
        self.store._client.scan = lambda **kw: (scan_calls.append(kw), original_scan(**kw))[1]
        self.store.get_by_hash("h1")
        self.assertEqual(len(scan_calls), 0, "get_by_hash must not call Scan")

    def test_list_for_user_returns_only_that_users_keys(self):
        k1 = self._make_key(user_id="u1", key_hash="h1")
        k2 = self._make_key(user_id="u1", key_hash="h2")
        k3 = self._make_key(user_id="u2", key_hash="h3")
        for k in (k1, k2, k3):
            self.store.create(k)
        result = self.store.list_for_user("u1")
        ids = {k.key_id for k in result}
        self.assertIn(k1.key_id, ids)
        self.assertIn(k2.key_id, ids)
        self.assertNotIn(k3.key_id, ids)

    def test_list_for_user_does_not_call_scan(self):
        key = self._make_key(user_id="u1", key_hash="h1")
        self.store.create(key)
        original_scan = self.store._client.scan
        scan_calls = []
        self.store._client.scan = lambda **kw: (scan_calls.append(kw), original_scan(**kw))[1]
        self.store.list_for_user("u1")
        self.assertEqual(len(scan_calls), 0, "list_for_user must not call Scan")

    def test_revoke_preserves_gsi_attributes(self):
        key = self._make_key(user_id="u1", key_hash="h-revoke")
        self.store.create(key)
        self.store.revoke(key.key_id, "u1")
        # After revoke, key should still be findable by hash
        found = self.store.get_by_hash("h-revoke")
        self.assertIsNotNone(found)
        self.assertIsNotNone(found.revoked_at)

    def test_update_last_used_preserves_gsi_attributes(self):
        key = self._make_key(user_id="u1", key_hash="h-used")
        self.store.create(key)
        self.store.update_last_used(key.key_id)
        # Key must still appear in user's list
        keys = self.store.list_for_user("u1")
        self.assertEqual(len(keys), 1)
        self.assertIsNotNone(keys[0].last_used_at)


# ---------------------------------------------------------------------------
# Quota checks still work via GSI-backed store
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_MOTO, "moto not installed")
class TestQuotaWithGSIBackedStore(unittest.TestCase):

    def setUp(self):
        self.mock = moto.mock_aws()
        self.mock.start()
        client = boto3.session.Session().client("dynamodb", region_name=REGION)
        _create_jobs_table(client)
        from trustgraph_cloud.jobs.dynamodb_store import DynamoDBJobStore
        self.store = DynamoDBJobStore(table_name=JOBS_TABLE, region=REGION)

    def tearDown(self):
        self.mock.stop()

    def test_active_job_quota_triggers_with_gsi_store(self):
        from fastapi import HTTPException
        from trustgraph_cloud.api.quota import check_quotas

        user_id = "quota-user"
        for _ in range(3):
            j = Job(input_type="demo", user_id=user_id, status=JobStatus.QUEUED)
            self.store.create(j)

        with self.assertRaises(HTTPException) as ctx:
            check_quotas(self.store, user_id, max_audits_per_day=50, max_active_jobs=3)
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertIn("Active job limit", ctx.exception.detail)

    def test_quota_passes_below_limit(self):
        from trustgraph_cloud.api.quota import check_quotas

        user_id = "under-limit"
        for _ in range(2):
            self.store.create(Job(input_type="demo", user_id=user_id))

        # Should not raise
        check_quotas(self.store, user_id, max_audits_per_day=50, max_active_jobs=3)

    def test_quota_no_op_for_anonymous_user(self):
        from trustgraph_cloud.api.quota import check_quotas

        # Flood anonymous jobs — quota should not apply
        for _ in range(100):
            self.store.create(Job(input_type="demo", user_id=None))

        check_quotas(self.store, None, max_audits_per_day=1, max_active_jobs=1)

    def test_daily_quota_triggers_with_gsi_store(self):
        from fastapi import HTTPException
        from trustgraph_cloud.api.quota import check_quotas

        user_id = "daily-user"
        for _ in range(5):
            self.store.create(Job(input_type="demo", user_id=user_id))

        with self.assertRaises(HTTPException) as ctx:
            check_quotas(self.store, user_id, max_audits_per_day=5, max_active_jobs=100)
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertIn("Daily audit limit", ctx.exception.detail)

    def test_succeeded_jobs_do_not_count_toward_active_quota(self):
        from trustgraph_cloud.api.quota import check_quotas

        user_id = "succeeded-user"
        for _ in range(5):
            j = Job(input_type="demo", user_id=user_id)
            self.store.create(j)
            self.store.update(j.job_id, status=JobStatus.SUCCEEDED)

        # All 5 are succeeded; active count = 0 → should not raise
        check_quotas(self.store, user_id, max_audits_per_day=100, max_active_jobs=3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
