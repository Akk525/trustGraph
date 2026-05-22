"""
Phase 4C tests — cursor-based pagination for GET /audits and the CLI jobs command.

Covers:
- encode_cursor / decode_cursor round-trip
- decode_cursor raises ValueError on malformed input
- LocalJobStore.list_for_user_page: first page, next page, all pages, ordering, empty
- DynamoDBJobStore.list_for_user_page: ExclusiveStartKey used, no scan, ordering
- GET /audits cursor mode: first page has next_cursor, second page continues
- GET /audits cursor + offset together → 400
- GET /audits malformed cursor → 400
- GET /audits offset mode still works (backward compat)
- CLI jobs --cursor fetches that page
- CLI jobs --all follows all cursors
- CLI jobs shows next_cursor hint when present
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

try:
    import boto3
    import moto
    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False

from trustgraph_cloud.jobs.models import Job, JobStatus
from trustgraph_cloud.jobs.store import decode_cursor, encode_cursor

REGION = "us-east-1"
JOBS_TABLE = "test-jobs-4c"

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


# ---------------------------------------------------------------------------
# Cursor encoding helpers
# ---------------------------------------------------------------------------

class TestCursorEncoding(unittest.TestCase):

    def test_encode_decode_roundtrip(self):
        payload = {"offset": 42}
        self.assertEqual(decode_cursor(encode_cursor(payload)), payload)

    def test_encode_decode_dynamo_key(self):
        lek = {
            "job_id": {"S": "abc-123"},
            "user_id": {"S": "uid-xyz"},
            "created_at": {"S": "2024-01-15T10:30:00+00:00"},
        }
        self.assertEqual(decode_cursor(encode_cursor(lek)), lek)

    def test_decode_malformed_raises_value_error(self):
        with self.assertRaises(ValueError):
            decode_cursor("not-valid-base64!!!")

    def test_decode_valid_base64_but_not_json_raises_value_error(self):
        import base64
        bad = base64.urlsafe_b64encode(b"not json").decode().rstrip("=")
        with self.assertRaises(ValueError):
            decode_cursor(bad)

    def test_cursor_is_url_safe(self):
        cursor = encode_cursor({"key": "value/with+special?chars"})
        # Should only contain URL-safe base64 characters
        import re
        self.assertRegex(cursor, r"^[A-Za-z0-9_=-]*$")


# ---------------------------------------------------------------------------
# LocalJobStore.list_for_user_page
# ---------------------------------------------------------------------------

class TestLocalJobStorePageCursor(unittest.TestCase):

    def setUp(self):
        from trustgraph_cloud.jobs.store import LocalJobStore
        self._tmpdir = tempfile.TemporaryDirectory()
        self.store = LocalJobStore(Path(self._tmpdir.name))

    def tearDown(self):
        self._tmpdir.cleanup()

    def _create_n(self, n: int, user_id: str = "uid-1") -> list[Job]:
        jobs = []
        for _ in range(n):
            j = Job(input_type="demo", user_id=user_id)
            self.store.create(j)
            time.sleep(0.005)  # distinct timestamps
            jobs.append(j)
        return jobs

    def test_first_page_returns_next_cursor_when_more(self):
        self._create_n(5)
        page = self.store.list_for_user_page("uid-1", limit=2)
        self.assertEqual(len(page.items), 2)
        self.assertIsNotNone(page.next_cursor)
        self.assertTrue(page.has_more)

    def test_second_page_returns_next_set(self):
        jobs = self._create_n(4)
        p1 = self.store.list_for_user_page("uid-1", limit=2)
        p2 = self.store.list_for_user_page("uid-1", limit=2, cursor=p1.next_cursor)
        ids_1 = {j.job_id for j in p1.items}
        ids_2 = {j.job_id for j in p2.items}
        self.assertEqual(len(ids_1 & ids_2), 0)
        self.assertEqual(len(ids_1 | ids_2), 4)
        self.assertFalse(p2.has_more)
        self.assertIsNone(p2.next_cursor)

    def test_all_pages_cover_all_items(self):
        self._create_n(7)
        all_ids: set = set()
        cursor = None
        while True:
            page = self.store.list_for_user_page("uid-1", limit=3, cursor=cursor)
            all_ids.update(j.job_id for j in page.items)
            cursor = page.next_cursor
            if not cursor:
                break
        self.assertEqual(len(all_ids), 7)

    def test_newest_first_ordering(self):
        self._create_n(4)
        page = self.store.list_for_user_page("uid-1", limit=4)
        timestamps = [j.created_at for j in page.items]
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))

    def test_no_next_cursor_when_fits_on_one_page(self):
        self._create_n(3)
        page = self.store.list_for_user_page("uid-1", limit=10)
        self.assertIsNone(page.next_cursor)
        self.assertFalse(page.has_more)

    def test_empty_store_returns_empty_page(self):
        page = self.store.list_for_user_page("uid-1", limit=10)
        self.assertEqual(page.items, [])
        self.assertIsNone(page.next_cursor)
        self.assertFalse(page.has_more)

    def test_total_reflects_all_user_jobs(self):
        self._create_n(5)
        page = self.store.list_for_user_page("uid-1", limit=2)
        self.assertEqual(page.total, 5)

    def test_malformed_cursor_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.store.list_for_user_page("uid-1", limit=2, cursor="!!!bad!!!")

    def test_isolates_by_user(self):
        self._create_n(3, user_id="alice")
        self._create_n(2, user_id="bob")
        page_alice = self.store.list_for_user_page("alice", limit=10)
        page_bob = self.store.list_for_user_page("bob", limit=10)
        self.assertEqual(len(page_alice.items), 3)
        self.assertEqual(len(page_bob.items), 2)

    def test_anonymous_user_pagination(self):
        for _ in range(3):
            self.store.create(Job(input_type="demo", user_id=None))
        page = self.store.list_for_user_page(None, limit=2)
        self.assertEqual(len(page.items), 2)
        self.assertIsNotNone(page.next_cursor)


# ---------------------------------------------------------------------------
# DynamoDBJobStore.list_for_user_page
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_MOTO, "moto not installed")
class TestDynamoDBJobStorePageCursor(unittest.TestCase):

    def setUp(self):
        self.mock = moto.mock_aws()
        self.mock.start()
        client = boto3.session.Session().client("dynamodb", region_name=REGION)
        _create_jobs_table(client)
        from trustgraph_cloud.jobs.dynamodb_store import DynamoDBJobStore
        self.store = DynamoDBJobStore(table_name=JOBS_TABLE, region=REGION)

    def tearDown(self):
        self.mock.stop()

    def _create_n(self, n: int, user_id: str = "uid-1") -> list[Job]:
        jobs = []
        for _ in range(n):
            j = Job(input_type="demo", user_id=user_id)
            self.store.create(j)
            time.sleep(0.005)
            jobs.append(j)
        return jobs

    def test_first_page_returns_next_cursor_when_more(self):
        self._create_n(5)
        page = self.store.list_for_user_page("uid-1", limit=2)
        self.assertEqual(len(page.items), 2)
        self.assertIsNotNone(page.next_cursor)
        self.assertTrue(page.has_more)

    def test_second_page_returns_different_items(self):
        self._create_n(4)
        p1 = self.store.list_for_user_page("uid-1", limit=2)
        p2 = self.store.list_for_user_page("uid-1", limit=2, cursor=p1.next_cursor)
        ids_1 = {j.job_id for j in p1.items}
        ids_2 = {j.job_id for j in p2.items}
        self.assertEqual(len(ids_1 & ids_2), 0, "Pages must not overlap")
        self.assertEqual(len(ids_1 | ids_2), 4, "Pages must cover all items")

    def test_all_pages_cover_all_items(self):
        self._create_n(6)
        all_ids: set = set()
        cursor = None
        while True:
            page = self.store.list_for_user_page("uid-1", limit=2, cursor=cursor)
            all_ids.update(j.job_id for j in page.items)
            cursor = page.next_cursor
            if not cursor:
                break
        self.assertEqual(len(all_ids), 6)

    def test_newest_first_ordering(self):
        self._create_n(4)
        page = self.store.list_for_user_page("uid-1", limit=4)
        timestamps = [j.created_at for j in page.items]
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))

    def test_exclusive_start_key_is_used(self):
        """Verify ExclusiveStartKey is passed to the DynamoDB query when cursor set."""
        self._create_n(3)
        p1 = self.store.list_for_user_page("uid-1", limit=1)

        query_calls = []
        original_query = self.store._client.query

        def capturing_query(**kwargs):
            query_calls.append(kwargs)
            return original_query(**kwargs)

        self.store._client.query = capturing_query
        self.store.list_for_user_page("uid-1", limit=1, cursor=p1.next_cursor)

        self.assertTrue(len(query_calls) >= 1)
        self.assertIn("ExclusiveStartKey", query_calls[0],
                      "DynamoDB query must use ExclusiveStartKey when cursor provided")

    def test_no_scan_called(self):
        self._create_n(3)
        original_scan = self.store._client.scan
        scan_calls = []
        self.store._client.scan = lambda **kw: (scan_calls.append(kw), original_scan(**kw))[1]
        self.store.list_for_user_page("uid-1", limit=2)
        self.assertEqual(len(scan_calls), 0, "list_for_user_page must not call Scan")

    def test_malformed_cursor_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.store.list_for_user_page("uid-1", limit=2, cursor="bad!!!cursor")

    def test_isolates_by_user(self):
        self._create_n(3, user_id="alice")
        self._create_n(2, user_id="bob")
        page_alice = self.store.list_for_user_page("alice", limit=10)
        page_bob = self.store.list_for_user_page("bob", limit=10)
        self.assertEqual(len(page_alice.items), 3)
        self.assertEqual(len(page_bob.items), 2)


# ---------------------------------------------------------------------------
# GET /audits — cursor mode via TestClient + LocalJobStore
# ---------------------------------------------------------------------------

class TestAuditListCursorRoute(unittest.TestCase):

    def setUp(self):
        from starlette.testclient import TestClient
        from trustgraph_cloud.api.main import create_app
        from trustgraph_cloud.config import Settings
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        settings = Settings(
            base_workspace=Path(self._tmpdir.name) / ".trustgraph-cloud",
            max_active_jobs=50,
            max_audits_per_day=200,
        )
        self.client = TestClient(create_app(settings=settings))

    def tearDown(self):
        self._tmpdir.cleanup()

    def _create_jobs(self, n: int) -> list[str]:
        ids = []
        with self.client as c:
            for _ in range(n):
                resp = c.post("/audits", json={"use_demo": True})
                ids.append(resp.json()["job_id"])
                time.sleep(0.005)
        return ids

    def test_cursor_mode_returns_next_cursor_when_more(self):
        self._create_jobs(5)
        with self.client as c:
            resp = c.get("/audits?limit=2")
        data = resp.json()
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(data["next_cursor"])
        self.assertTrue(data["has_more"])
        self.assertEqual(len(data["jobs"]), 2)

    def test_cursor_mode_second_page_is_disjoint(self):
        self._create_jobs(4)
        with self.client as c:
            p1 = c.get("/audits?limit=2").json()
            cursor = p1["next_cursor"]
            p2 = c.get(f"/audits?limit=2&cursor={cursor}").json()
        ids_1 = {j["job_id"] for j in p1["jobs"]}
        ids_2 = {j["job_id"] for j in p2["jobs"]}
        self.assertEqual(len(ids_1 & ids_2), 0)
        self.assertEqual(len(ids_1 | ids_2), 4)
        self.assertFalse(p2["has_more"])
        self.assertIsNone(p2["next_cursor"])

    def test_cursor_mode_newest_first(self):
        self._create_jobs(3)
        with self.client as c:
            resp = c.get("/audits?limit=3")
        jobs = resp.json()["jobs"]
        timestamps = [j["created_at"] for j in jobs]
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))

    def test_cursor_and_offset_together_returns_400(self):
        with self.client as c:
            resp = c.get("/audits?cursor=abc&offset=0")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("cursor", resp.json()["detail"].lower())

    def test_malformed_cursor_returns_400(self):
        with self.client as c:
            resp = c.get("/audits?cursor=!!!invalid!!!")
        self.assertEqual(resp.status_code, 400)

    def test_offset_mode_still_works(self):
        self._create_jobs(4)
        with self.client as c:
            p1 = c.get("/audits?limit=2&offset=0").json()
            p2 = c.get("/audits?limit=2&offset=2").json()
        ids_1 = {j["job_id"] for j in p1["jobs"]}
        ids_2 = {j["job_id"] for j in p2["jobs"]}
        self.assertEqual(len(ids_1 & ids_2), 0)
        self.assertEqual(len(ids_1 | ids_2), 4)
        self.assertIsNotNone(p1["total"])  # offset mode provides total
        self.assertFalse(p2["has_more"])

    def test_cursor_mode_response_has_required_fields(self):
        with self.client as c:
            resp = c.get("/audits")
        data = resp.json()
        self.assertIn("jobs", data)
        self.assertIn("limit", data)
        self.assertIn("has_more", data)
        self.assertIn("next_cursor", data)

    def test_no_cursor_for_last_page(self):
        self._create_jobs(2)
        with self.client as c:
            resp = c.get("/audits?limit=10")
        data = resp.json()
        self.assertFalse(data["has_more"])
        self.assertIsNone(data["next_cursor"])


# ---------------------------------------------------------------------------
# CLI jobs command — cursor flags
# ---------------------------------------------------------------------------

class TestCLIJobsCursor(unittest.TestCase):

    def _jobs_response(
        self,
        job_ids: list[str],
        next_cursor: str | None = None,
        has_more: bool = False,
    ) -> dict:
        return {
            "jobs": [
                {
                    "job_id": jid,
                    "status": "succeeded",
                    "created_at": "2024-01-15T10:30:00",
                    "input_type": "s3_upload",
                    "artifact_count": 1,
                }
                for jid in job_ids
            ],
            "next_cursor": next_cursor,
            "has_more": has_more,
            "limit": 2,
            "total": None,
            "offset": None,
        }

    def test_jobs_shows_cursor_hint_when_next_cursor_present(self):
        from trustgraph_cloud.cli import app
        from typer.testing import CliRunner
        runner = CliRunner()
        response = self._jobs_response(["job-1"], next_cursor="abc123", has_more=True)
        with patch("trustgraph_cloud.cli_config.get_api_url", return_value="http://api.test"), \
             patch("trustgraph_cloud.cli_config.get_token", return_value="tok"), \
             patch("trustgraph_cloud.cli_client.list_jobs", return_value=response):
            result = runner.invoke(app, ["jobs"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("--cursor", result.output)
        self.assertIn("abc123", result.output)

    def test_jobs_cursor_flag_is_passed_to_api(self):
        from trustgraph_cloud.cli import app
        from typer.testing import CliRunner
        runner = CliRunner()
        response = self._jobs_response(["job-2"])
        with patch("trustgraph_cloud.cli_config.get_api_url", return_value="http://api.test"), \
             patch("trustgraph_cloud.cli_config.get_token", return_value="tok"), \
             patch("trustgraph_cloud.cli_client.list_jobs", return_value=response) as mock_list:
            runner.invoke(app, ["jobs", "--cursor", "cursor-xyz"])
        mock_list.assert_called_once()
        call_kwargs = mock_list.call_args[1]
        self.assertEqual(call_kwargs.get("cursor"), "cursor-xyz")

    def test_jobs_all_follows_cursors(self):
        from trustgraph_cloud.cli import app
        from typer.testing import CliRunner
        runner = CliRunner()

        page1 = self._jobs_response(["job-1", "job-2"], next_cursor="cur2", has_more=True)
        page2 = self._jobs_response(["job-3", "job-4"], next_cursor="cur3", has_more=True)
        page3 = self._jobs_response(["job-5"], next_cursor=None, has_more=False)

        pages = [page1, page2, page3]
        call_count = 0

        def mock_list_jobs(api_url, token, limit, cursor):
            nonlocal call_count
            c = call_count
            call_count += 1
            return pages[c]

        with patch("trustgraph_cloud.cli_config.get_api_url", return_value="http://api.test"), \
             patch("trustgraph_cloud.cli_config.get_token", return_value="tok"), \
             patch("trustgraph_cloud.cli_client.list_jobs", side_effect=mock_list_jobs):
            result = runner.invoke(app, ["jobs", "--all"])

        self.assertEqual(result.exit_code, 0)
        # All 5 jobs should appear in output
        for jid in ("job-1", "job-2", "job-3", "job-4", "job-5"):
            self.assertIn(jid, result.output)
        # All 3 pages fetched
        self.assertEqual(call_count, 3)

    def test_jobs_all_passes_cursor_between_pages(self):
        from trustgraph_cloud.cli import app
        from typer.testing import CliRunner
        runner = CliRunner()

        captured_cursors = []

        def mock_list_jobs(api_url, token, limit, cursor):
            captured_cursors.append(cursor)
            if cursor is None:
                return self._jobs_response(["job-1"], next_cursor="c2", has_more=True)
            return self._jobs_response(["job-2"], next_cursor=None, has_more=False)

        with patch("trustgraph_cloud.cli_config.get_api_url", return_value="http://api.test"), \
             patch("trustgraph_cloud.cli_config.get_token", return_value="tok"), \
             patch("trustgraph_cloud.cli_client.list_jobs", side_effect=mock_list_jobs):
            runner.invoke(app, ["jobs", "--all"])

        self.assertEqual(captured_cursors[0], None)
        self.assertEqual(captured_cursors[1], "c2")

    def test_jobs_empty_still_works(self):
        from trustgraph_cloud.cli import app
        from typer.testing import CliRunner
        runner = CliRunner()
        empty = {"jobs": [], "next_cursor": None, "has_more": False, "limit": 20,
                 "total": None, "offset": None}
        with patch("trustgraph_cloud.cli_config.get_api_url", return_value="http://api.test"), \
             patch("trustgraph_cloud.cli_config.get_token", return_value="tok"), \
             patch("trustgraph_cloud.cli_client.list_jobs", return_value=empty):
            result = runner.invoke(app, ["jobs"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("No jobs", result.output)


if __name__ == "__main__":
    unittest.main(verbosity=2)
