"""
Phase 3C tests: job listing, pagination, per-user quotas, and auth rate limiting.

All tests use local in-memory stores — no AWS required.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from starlette.testclient import TestClient

from trustgraph_cloud.api.main import create_app
from trustgraph_cloud.config import Settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_JWT_SECRET = "test-secret-must-be-at-least-this-long-for-safety"


def _make_client(
    tmpdir: Path,
    auth_required: bool = True,
    embedded_worker: bool = False,
    **settings_kwargs,
) -> TestClient:
    settings = Settings(
        base_workspace=tmpdir / ".trustgraph-cloud",
        auth_required=auth_required,
        jwt_secret=_JWT_SECRET if auth_required else "",
        jwt_ttl_seconds=3600,
        auth_store="local",
        embedded_worker=embedded_worker,
        **settings_kwargs,
    )
    return TestClient(create_app(settings=settings))


def _signup(c: TestClient, email: str = "user@example.com", password: str = "password123") -> dict:
    resp = c.post("/auth/signup", json={"email": email, "password": password})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _login(c: TestClient, email: str = "user@example.com", password: str = "password123") -> dict:
    resp = c.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _create_job(c: TestClient, headers: dict) -> dict:
    resp = c.post("/audits", json={"use_demo": True}, headers=headers)
    assert resp.status_code == 202, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# TestListAudits — GET /audits
# ---------------------------------------------------------------------------

class TestListAuditsAuthRequired(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        # Set quotas high so listing/pagination tests never hit them.
        self.client = _make_client(
            Path(self.tmpdir.name),
            max_active_jobs=50,
            max_audits_per_day=200,
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_list_audits_requires_auth_when_auth_enabled(self):
        with self.client as c:
            resp = c.get("/audits")
        self.assertEqual(resp.status_code, 401)

    def test_list_audits_returns_empty_list_for_new_user(self):
        with self.client as c:
            tok = _signup(c)["access_token"]
            resp = c.get("/audits", headers=_auth_header(tok))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["jobs"], [])
        self.assertEqual(data["total"], 0)
        self.assertFalse(data["has_more"])

    def test_list_audits_returns_own_jobs(self):
        with self.client as c:
            tok = _signup(c)["access_token"]
            _create_job(c, _auth_header(tok))
            _create_job(c, _auth_header(tok))
            resp = c.get("/audits", headers=_auth_header(tok))
        data = resp.json()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(data["total"], 2)
        self.assertEqual(len(data["jobs"]), 2)

    def test_list_audits_excludes_other_users_jobs(self):
        with self.client as c:
            tok_a = _signup(c, "a@example.com")["access_token"]
            tok_b = _signup(c, "b@example.com")["access_token"]
            _create_job(c, _auth_header(tok_a))
            _create_job(c, _auth_header(tok_a))
            _create_job(c, _auth_header(tok_b))
            resp_a = c.get("/audits", headers=_auth_header(tok_a))
            resp_b = c.get("/audits", headers=_auth_header(tok_b))
        self.assertEqual(resp_a.json()["total"], 2)
        self.assertEqual(resp_b.json()["total"], 1)
        # No ID overlap
        ids_a = {j["job_id"] for j in resp_a.json()["jobs"]}
        ids_b = {j["job_id"] for j in resp_b.json()["jobs"]}
        self.assertEqual(len(ids_a & ids_b), 0)

    def test_list_audits_sorted_newest_first(self):
        with self.client as c:
            tok = _signup(c)["access_token"]
            j1 = _create_job(c, _auth_header(tok))
            j2 = _create_job(c, _auth_header(tok))
            j3 = _create_job(c, _auth_header(tok))
            resp = c.get("/audits", headers=_auth_header(tok))
        jobs = resp.json()["jobs"]
        self.assertEqual(len(jobs), 3)
        self.assertEqual(jobs[0]["job_id"], j3["job_id"])
        self.assertEqual(jobs[2]["job_id"], j1["job_id"])

    def test_list_audits_limit_trims_results(self):
        with self.client as c:
            tok = _signup(c)["access_token"]
            for _ in range(5):
                _create_job(c, _auth_header(tok))
            resp = c.get("/audits?limit=3", headers=_auth_header(tok))
        data = resp.json()
        self.assertEqual(len(data["jobs"]), 3)
        self.assertEqual(data["total"], 5)
        self.assertEqual(data["limit"], 3)
        self.assertTrue(data["has_more"])

    def test_list_audits_offset_pagination_no_overlap(self):
        with self.client as c:
            tok = _signup(c)["access_token"]
            for _ in range(4):
                _create_job(c, _auth_header(tok))
            page1 = c.get("/audits?limit=2&offset=0", headers=_auth_header(tok)).json()
            page2 = c.get("/audits?limit=2&offset=2", headers=_auth_header(tok)).json()
        ids_p1 = {j["job_id"] for j in page1["jobs"]}
        ids_p2 = {j["job_id"] for j in page2["jobs"]}
        # Pages are disjoint and together cover all 4 jobs
        self.assertEqual(len(ids_p1 & ids_p2), 0)
        self.assertEqual(len(ids_p1 | ids_p2), 4)
        self.assertFalse(page2["has_more"])

    def test_list_audits_has_more_false_on_last_page(self):
        with self.client as c:
            tok = _signup(c)["access_token"]
            _create_job(c, _auth_header(tok))
            resp = c.get("/audits?limit=100", headers=_auth_header(tok))
        self.assertFalse(resp.json()["has_more"])

    def test_list_audits_limit_out_of_range_returns_422(self):
        with self.client as c:
            tok = _signup(c)["access_token"]
            resp_zero = c.get("/audits?limit=0", headers=_auth_header(tok))
            resp_over = c.get("/audits?limit=101", headers=_auth_header(tok))
        self.assertEqual(resp_zero.status_code, 422)
        self.assertEqual(resp_over.status_code, 422)

    def test_list_audits_response_includes_artifact_count(self):
        with self.client as c:
            tok = _signup(c)["access_token"]
            _create_job(c, _auth_header(tok))
            resp = c.get("/audits", headers=_auth_header(tok))
        job = resp.json()["jobs"][0]
        self.assertIn("artifact_count", job)
        self.assertEqual(job["artifact_count"], 0)

    def test_single_audit_response_includes_artifact_count(self):
        with self.client as c:
            tok = _signup(c)["access_token"]
            job_id = _create_job(c, _auth_header(tok))["job_id"]
            resp = c.get(f"/audits/{job_id}", headers=_auth_header(tok))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("artifact_count", resp.json())


class TestListAuditsAuthDisabled(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.client = _make_client(Path(self.tmpdir.name), auth_required=False)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_list_audits_returns_anonymous_jobs_when_auth_disabled(self):
        with self.client as c:
            c.post("/audits", json={"use_demo": True})
            c.post("/audits", json={"use_demo": True})
            c.post("/audits", json={"use_demo": True})
            resp = c.get("/audits")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total"], 3)
        self.assertEqual(len(data["jobs"]), 3)

    def test_list_audits_no_auth_header_required(self):
        with self.client as c:
            resp = c.get("/audits")
        self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# TestQuotas — active jobs and daily limits
# ---------------------------------------------------------------------------

class TestActiveJobsQuota(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.client = _make_client(
            Path(self.tmpdir.name),
            max_active_jobs=1,
            max_audits_per_day=100,
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_active_jobs_quota_blocks_second_submission(self):
        with self.client as c:
            tok = _signup(c)["access_token"]
            resp1 = c.post("/audits", json={"use_demo": True}, headers=_auth_header(tok))
            resp2 = c.post("/audits", json={"use_demo": True}, headers=_auth_header(tok))
        self.assertEqual(resp1.status_code, 202)
        self.assertEqual(resp2.status_code, 429)
        self.assertIn("Active job limit", resp2.json()["detail"])

    def test_active_jobs_quota_is_per_user(self):
        """Quota exhaustion for user A does not block user B."""
        with self.client as c:
            tok_a = _signup(c, "a@test.com")["access_token"]
            tok_b = _signup(c, "b@test.com")["access_token"]
            c.post("/audits", json={"use_demo": True}, headers=_auth_header(tok_a))
            resp_b = c.post("/audits", json={"use_demo": True}, headers=_auth_header(tok_b))
        self.assertEqual(resp_b.status_code, 202)


class TestDailyQuota(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.client = _make_client(
            Path(self.tmpdir.name),
            max_active_jobs=100,
            max_audits_per_day=2,
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_daily_quota_blocks_third_submission(self):
        with self.client as c:
            tok = _signup(c)["access_token"]
            resp1 = c.post("/audits", json={"use_demo": True}, headers=_auth_header(tok))
            resp2 = c.post("/audits", json={"use_demo": True}, headers=_auth_header(tok))
            resp3 = c.post("/audits", json={"use_demo": True}, headers=_auth_header(tok))
        self.assertEqual(resp1.status_code, 202)
        self.assertEqual(resp2.status_code, 202)
        self.assertEqual(resp3.status_code, 429)
        self.assertIn("Daily audit limit", resp3.json()["detail"])

    def test_daily_quota_is_per_user(self):
        with self.client as c:
            tok_a = _signup(c, "a@test.com")["access_token"]
            tok_b = _signup(c, "b@test.com")["access_token"]
            c.post("/audits", json={"use_demo": True}, headers=_auth_header(tok_a))
            c.post("/audits", json={"use_demo": True}, headers=_auth_header(tok_a))
            resp_b = c.post("/audits", json={"use_demo": True}, headers=_auth_header(tok_b))
        self.assertEqual(resp_b.status_code, 202)


class TestQuotaBypassWhenAuthDisabled(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.client = _make_client(
            Path(self.tmpdir.name),
            auth_required=False,
            max_active_jobs=1,
            max_audits_per_day=1,
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_quotas_not_enforced_when_auth_disabled(self):
        """user_id=None with auth disabled → check_quotas is a no-op."""
        with self.client as c:
            for _ in range(5):
                resp = c.post("/audits", json={"use_demo": True})
                self.assertEqual(resp.status_code, 202)


# ---------------------------------------------------------------------------
# TestRateLimit — per-endpoint sliding-window limits
# ---------------------------------------------------------------------------

class TestRateLimitSignup(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.client = _make_client(
            Path(self.tmpdir.name),
            auth_rate_limit_per_minute=1,
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_signup_blocked_on_second_attempt(self):
        with self.client as c:
            resp1 = c.post("/auth/signup", json={"email": "a@test.com", "password": "password123"})
            resp2 = c.post("/auth/signup", json={"email": "b@test.com", "password": "password123"})
        self.assertEqual(resp1.status_code, 201)
        self.assertEqual(resp2.status_code, 429)
        self.assertIn("Too many signup", resp2.json()["detail"])

    def test_signup_and_login_use_independent_buckets(self):
        """Exhausting the signup bucket does not block login."""
        with self.client as c:
            # First signup: uses up signup bucket (limit=1)
            resp_signup = c.post("/auth/signup", json={"email": "a@test.com", "password": "password123"})
            self.assertEqual(resp_signup.status_code, 201)
            # Login bucket is still empty — should succeed
            resp_login = c.post("/auth/login", json={"email": "a@test.com", "password": "password123"})
        self.assertEqual(resp_login.status_code, 200)


class TestRateLimitLogin(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        # limit=2: allows 2 logins per minute before blocking
        self.client = _make_client(
            Path(self.tmpdir.name),
            auth_rate_limit_per_minute=2,
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_login_blocked_after_limit_exceeded(self):
        with self.client as c:
            _signup(c)
            resp1 = c.post("/auth/login", json={"email": "user@example.com", "password": "password123"})
            resp2 = c.post("/auth/login", json={"email": "user@example.com", "password": "password123"})
            resp3 = c.post("/auth/login", json={"email": "user@example.com", "password": "password123"})
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp3.status_code, 429)
        self.assertIn("Too many login", resp3.json()["detail"])


class TestRateLimitApiKeyCreate(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        # limit=2: allows 2 api-key creations per minute
        self.client = _make_client(
            Path(self.tmpdir.name),
            auth_rate_limit_per_minute=2,
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_api_key_create_blocked_after_limit_exceeded(self):
        with self.client as c:
            tok = _signup(c)["access_token"]
            headers = _auth_header(tok)
            resp1 = c.post("/api-keys", json={"name": "key1"}, headers=headers)
            resp2 = c.post("/api-keys", json={"name": "key2"}, headers=headers)
            resp3 = c.post("/api-keys", json={"name": "key3"}, headers=headers)
        self.assertEqual(resp1.status_code, 201)
        self.assertEqual(resp2.status_code, 201)
        self.assertEqual(resp3.status_code, 429)
        self.assertIn("Too many", resp3.json()["detail"])

    def test_api_key_bucket_independent_from_auth_buckets(self):
        """Using up signup bucket does not affect api-key creation bucket."""
        with self.client as c:
            # signup: uses 1 of 2 from signup bucket
            tok = _signup(c)["access_token"]
            headers = _auth_header(tok)
            resp = c.post("/api-keys", json={"name": "key1"}, headers=headers)
        self.assertEqual(resp.status_code, 201)
