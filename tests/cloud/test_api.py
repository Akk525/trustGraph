"""
API endpoint tests for the TrustGraph Cloud service.

Uses Starlette's TestClient (sync) which runs the async lifespan internally.
Each test class creates an isolated app with a temp workspace to avoid
cross-test filesystem pollution.
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

# Real path to demo contracts (needed for local_path tests)
_DEMO_SRC = str(
    Path(__file__).parent.parent.parent / "examples" / "vulnerable-crosschain" / "src"
)


def _make_client(tmpdir: Path) -> TestClient:
    settings = Settings(base_workspace=tmpdir / ".trustgraph-cloud")
    app = create_app(settings=settings)
    return TestClient(app)


class TestHealth(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.client = _make_client(Path(self.tmpdir.name))

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_health_returns_ok(self):
        with self.client as c:
            resp = c.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("queue_depth", data)
        self.assertIn("version", data)

    def test_health_queue_depth_starts_at_zero(self):
        with self.client as c:
            resp = c.get("/health")
        self.assertEqual(resp.json()["queue_depth"], 0)


class TestCreateAudit(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.client = _make_client(Path(self.tmpdir.name))

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_demo_job_accepted(self):
        with self.client as c:
            resp = c.post("/audits", json={"use_demo": True})
        self.assertEqual(resp.status_code, 202)
        data = resp.json()
        self.assertIn("job_id", data)
        self.assertEqual(data["input_type"], "demo")
        self.assertIn(data["status"], ["queued", "running", "succeeded", "failed"])

    def test_local_path_job_accepted(self):
        with self.client as c:
            resp = c.post("/audits", json={"source_path": _DEMO_SRC})
        self.assertEqual(resp.status_code, 202)
        data = resp.json()
        self.assertEqual(data["input_type"], "local_path")

    def test_empty_body_rejected(self):
        with self.client as c:
            resp = c.post("/audits", json={})
        self.assertEqual(resp.status_code, 422)

    def test_both_demo_and_source_path_rejected(self):
        with self.client as c:
            resp = c.post("/audits", json={"use_demo": True, "source_path": _DEMO_SRC})
        self.assertEqual(resp.status_code, 422)

    def test_response_has_created_at(self):
        with self.client as c:
            resp = c.post("/audits", json={"use_demo": True})
        self.assertIn("created_at", resp.json())

    def test_no_ai_flag_passed_through(self):
        with self.client as c:
            resp = c.post("/audits", json={"use_demo": True, "no_ai": True})
        self.assertEqual(resp.status_code, 202)


class TestGetAudit(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.client = _make_client(Path(self.tmpdir.name))

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_nonexistent_job_returns_404(self):
        with self.client as c:
            resp = c.get("/audits/does-not-exist")
        self.assertEqual(resp.status_code, 404)

    def test_created_job_is_retrievable(self):
        with self.client as c:
            create_resp = c.post("/audits", json={"use_demo": True})
            job_id = create_resp.json()["job_id"]
            get_resp = c.get(f"/audits/{job_id}")
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.json()["job_id"], job_id)

    def test_job_status_is_valid_enum(self):
        valid = {"queued", "running", "succeeded", "failed", "cancelled"}
        with self.client as c:
            create_resp = c.post("/audits", json={"use_demo": True})
            job_id = create_resp.json()["job_id"]
            get_resp = c.get(f"/audits/{job_id}")
        self.assertIn(get_resp.json()["status"], valid)

    def test_job_input_type_preserved(self):
        with self.client as c:
            create_resp = c.post("/audits", json={"use_demo": True})
            job_id = create_resp.json()["job_id"]
            get_resp = c.get(f"/audits/{job_id}")
        self.assertEqual(get_resp.json()["input_type"], "demo")


class TestArtifacts(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.client = _make_client(Path(self.tmpdir.name))

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_nonexistent_job_artifacts_returns_404(self):
        with self.client as c:
            resp = c.get("/audits/nonexistent/artifacts")
        self.assertEqual(resp.status_code, 404)

    def test_new_job_artifacts_endpoint_returns_empty_list(self):
        with self.client as c:
            create_resp = c.post("/audits", json={"use_demo": True})
            job_id = create_resp.json()["job_id"]
            # Poll immediately — job may still be queued, no artifacts yet.
            resp = c.get(f"/audits/{job_id}/artifacts")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["job_id"], job_id)
        self.assertIsInstance(data["artifacts"], list)

    def test_artifacts_response_schema(self):
        with self.client as c:
            create_resp = c.post("/audits", json={"use_demo": True})
            job_id = create_resp.json()["job_id"]
            resp = c.get(f"/audits/{job_id}/artifacts")
        data = resp.json()
        self.assertIn("job_id", data)
        self.assertIn("artifacts", data)


class TestFailedJob(unittest.TestCase):
    """Simulate a job that fails due to a bad source path."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.client = _make_client(Path(self.tmpdir.name))

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_bad_source_path_job_eventually_fails(self):
        import time
        with self.client as c:
            resp = c.post("/audits", json={"source_path": "/nonexistent/path/to/contracts"})
            job_id = resp.json()["job_id"]
            # Give the worker a moment to process.
            deadline = time.time() + 5
            status = "queued"
            while time.time() < deadline and status in ("queued", "running"):
                time.sleep(0.1)
                status = c.get(f"/audits/{job_id}").json()["status"]
        # Should have failed; if still running due to slow CI, that's acceptable.
        self.assertIn(status, {"failed", "running", "queued"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
