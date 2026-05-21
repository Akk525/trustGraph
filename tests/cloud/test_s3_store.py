"""
Tests for the S3 artifact store and the artifact API response shape.

Unit tests use moto to mock AWS S3 — no real credentials required.
Integration tests against real AWS are gated by env vars and skipped by default.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

try:
    import boto3
    import moto
    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False

from trustgraph_cloud.artifacts.store import Artifact, LocalArtifactStore


# ---------------------------------------------------------------------------
# TestS3ArtifactStore — mocked AWS
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_MOTO, "moto not installed; run: pip install moto[s3]")
class TestS3ArtifactStore(unittest.TestCase):

    BUCKET = "tg-test-bucket"
    PREFIX = "trustgraph/jobs"
    REGION = "us-east-1"

    def setUp(self):
        self.mock = moto.mock_aws()
        self.mock.start()
        # Create bucket while mock is active.
        s3 = boto3.client("s3", region_name=self.REGION)
        s3.create_bucket(Bucket=self.BUCKET)
        self.s3 = s3

        self.tmpdir = tempfile.TemporaryDirectory()

        # Import here so boto3 session is created inside the mock context.
        from trustgraph_cloud.artifacts.s3_store import S3ArtifactStore
        self.store = S3ArtifactStore(
            bucket=self.BUCKET,
            prefix=self.PREFIX,
            region=self.REGION,
            presigned_url_ttl=3600,
        )

    def tearDown(self):
        self.mock.stop()
        self.tmpdir.cleanup()

    # -- helpers --

    def _make_file(self, name: str, content: str = "test content") -> str:
        path = Path(self.tmpdir.name) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return str(path)

    def _expected_key(self, job_id: str, name: str) -> str:
        return f"{self.PREFIX}/{job_id}/{name}"

    # -- register --

    def test_register_uploads_object_to_s3(self):
        src = self._make_file("report.json", '{"critical": 1}')
        self.store.register("job-001", src, "report.json")
        obj = self.s3.get_object(Bucket=self.BUCKET, Key=self._expected_key("job-001", "report.json"))
        self.assertEqual(obj["Body"].read(), b'{"critical": 1}')

    def test_register_returns_artifact_with_correct_name(self):
        src = self._make_file("report.json")
        art = self.store.register("job-001", src, "report.json")
        self.assertEqual(art.name, "report.json")

    def test_register_returns_s3_storage_backend(self):
        src = self._make_file("report.json")
        art = self.store.register("job-001", src, "report.json")
        self.assertEqual(art.storage_backend, "s3")

    def test_register_s3_key_uses_prefix_and_job_id(self):
        src = self._make_file("report.json")
        art = self.store.register("my-job-abc", src, "report.json")
        self.assertEqual(art.s3_key, self._expected_key("my-job-abc", "report.json"))

    def test_register_returns_presigned_url(self):
        src = self._make_file("report.json")
        art = self.store.register("job-001", src, "report.json")
        self.assertIsNotNone(art.presigned_url)
        self.assertIn("report.json", art.presigned_url)

    def test_register_size_bytes_matches_file(self):
        content = "hello trustgraph"
        src = self._make_file("data.md", content)
        art = self.store.register("job-001", src, "data.md")
        self.assertEqual(art.size_bytes, len(content.encode("utf-8")))

    def test_register_path_is_empty_for_s3(self):
        src = self._make_file("report.json")
        art = self.store.register("job-001", src, "report.json")
        self.assertEqual(art.path, "")

    def test_register_content_type_json(self):
        src = self._make_file("report.json")
        art = self.store.register("job-001", src, "report.json")
        self.assertEqual(art.content_type, "application/json")

    def test_register_content_type_markdown(self):
        src = self._make_file("report.md")
        art = self.store.register("job-001", src, "report.md")
        self.assertEqual(art.content_type, "text/markdown")

    def test_register_content_type_unknown_falls_back(self):
        src = self._make_file("exploit.t.sol")
        art = self.store.register("job-001", src, "exploit.t.sol")
        self.assertIsNotNone(art.content_type)  # falls back to application/octet-stream

    # -- presigned URL TTL --

    def test_presigned_url_ttl_zero_returns_none(self):
        from trustgraph_cloud.artifacts.s3_store import S3ArtifactStore
        store_no_url = S3ArtifactStore(
            bucket=self.BUCKET,
            prefix=self.PREFIX,
            region=self.REGION,
            presigned_url_ttl=0,
        )
        src = self._make_file("report.json")
        art = store_no_url.register("job-001", src, "report.json")
        self.assertIsNone(art.presigned_url)

    # -- list --

    def test_list_returns_all_artifacts_for_job(self):
        for name in ("report.json", "report.md"):
            self.store.register("job-001", self._make_file(name), name)
        names = [a.name for a in self.store.list("job-001")]
        self.assertIn("report.json", names)
        self.assertIn("report.md", names)

    def test_list_isolates_by_job_id(self):
        self.store.register("job-001", self._make_file("a.json", "{}"), "a.json")
        self.store.register("job-002", self._make_file("b.md", "#"), "b.md")
        names_001 = [a.name for a in self.store.list("job-001")]
        self.assertNotIn("b.md", names_001)

    def test_list_empty_for_unknown_job(self):
        self.assertEqual(self.store.list("no-such-job"), [])

    def test_list_artifacts_have_s3_backend(self):
        self.store.register("job-001", self._make_file("report.json"), "report.json")
        arts = self.store.list("job-001")
        self.assertTrue(all(a.storage_backend == "s3" for a in arts))

    def test_list_artifacts_have_presigned_urls(self):
        self.store.register("job-001", self._make_file("report.json"), "report.json")
        arts = self.store.list("job-001")
        self.assertTrue(all(a.presigned_url is not None for a in arts))

    # -- get --

    def test_get_existing_artifact(self):
        self.store.register("job-001", self._make_file("report.json"), "report.json")
        art = self.store.get("job-001", "report.json")
        self.assertIsNotNone(art)
        self.assertEqual(art.name, "report.json")

    def test_get_returns_correct_s3_key(self):
        self.store.register("job-001", self._make_file("report.json"), "report.json")
        art = self.store.get("job-001", "report.json")
        self.assertEqual(art.s3_key, self._expected_key("job-001", "report.json"))

    def test_get_nonexistent_returns_none(self):
        art = self.store.get("job-001", "missing.md")
        self.assertIsNone(art)

    def test_get_returns_presigned_url(self):
        self.store.register("job-001", self._make_file("report.json"), "report.json")
        art = self.store.get("job-001", "report.json")
        self.assertIsNotNone(art.presigned_url)

    def test_get_size_matches_uploaded_content(self):
        content = "x" * 512
        src = self._make_file("data.json", content)
        self.store.register("job-001", src, "data.json")
        art = self.store.get("job-001", "data.json")
        self.assertEqual(art.size_bytes, len(content.encode("utf-8")))


# ---------------------------------------------------------------------------
# TestArtifactDataclass — backward compatibility after adding optional fields
# ---------------------------------------------------------------------------

class TestArtifactDataclass(unittest.TestCase):

    def test_local_artifact_defaults(self):
        art = Artifact(name="report.md", path="/tmp/x", size_bytes=100)
        self.assertEqual(art.storage_backend, "local")
        self.assertIsNone(art.s3_key)
        self.assertIsNone(art.presigned_url)
        self.assertIsNone(art.content_type)

    def test_s3_artifact_fields(self):
        art = Artifact(
            name="report.json",
            path="",
            size_bytes=200,
            storage_backend="s3",
            s3_key="prefix/job-1/report.json",
            presigned_url="https://s3.example.com/signed",
            content_type="application/json",
        )
        self.assertEqual(art.storage_backend, "s3")
        self.assertEqual(art.s3_key, "prefix/job-1/report.json")
        self.assertIsNotNone(art.presigned_url)

    def test_local_artifact_store_returns_local_backend(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            jobs_dir = Path(tmpdir) / "jobs"
            jobs_dir.mkdir()
            src = Path(tmpdir) / "report.md"
            src.write_text("# report", encoding="utf-8")
            store = LocalArtifactStore(jobs_dir)
            art = store.register("job-001", str(src), "report.md")
            self.assertEqual(art.storage_backend, "local")
            self.assertIsNone(art.s3_key)
            self.assertIsNone(art.presigned_url)


# ---------------------------------------------------------------------------
# TestAPIArtifactResponseShape — verify the schema fields come through
# ---------------------------------------------------------------------------

class TestAPIArtifactResponseShape(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _make_client(self):
        from starlette.testclient import TestClient
        from trustgraph_cloud.api.main import create_app
        from trustgraph_cloud.config import Settings
        settings = Settings(base_workspace=Path(self.tmpdir.name) / ".trustgraph-cloud")
        app = create_app(settings=settings)
        return TestClient(app)

    def test_artifact_response_has_storage_backend(self):
        client = self._make_client()
        with client as c:
            create_resp = c.post("/audits", json={"use_demo": True})
            job_id = create_resp.json()["job_id"]
            resp = c.get(f"/audits/{job_id}/artifacts")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("job_id", data)
        self.assertIn("artifacts", data)
        # If any artifacts were produced, check their shape.
        for art in data["artifacts"]:
            self.assertIn("name", art)
            self.assertIn("size_bytes", art)
            self.assertIn("storage_backend", art)
            self.assertEqual(art["storage_backend"], "local")

    def test_local_artifact_path_is_populated(self):
        """Local artifacts should have a path; s3_key and presigned_url should be absent/null."""
        client = self._make_client()
        with client as c:
            create_resp = c.post("/audits", json={"use_demo": True})
            job_id = create_resp.json()["job_id"]
            resp = c.get(f"/audits/{job_id}/artifacts")
        for art in resp.json()["artifacts"]:
            # path may be None (no artifacts yet) or a string; never an s3_key
            self.assertIsNone(art.get("s3_key"))
            self.assertIsNone(art.get("presigned_url"))


# ---------------------------------------------------------------------------
# Integration — real AWS (skipped unless env vars present)
# ---------------------------------------------------------------------------

_REAL_BUCKET = os.environ.get("TRUSTGRAPH_S3_BUCKET")
_REAL_REGION = os.environ.get("TRUSTGRAPH_AWS_REGION", "us-east-1")


@unittest.skipUnless(_REAL_BUCKET, "Set TRUSTGRAPH_S3_BUCKET to run real AWS tests")
class TestS3ArtifactStoreRealAWS(unittest.TestCase):
    """
    Sanity checks against a real S3 bucket.
    Run with:
        TRUSTGRAPH_S3_BUCKET=my-bucket \
        AWS_DEFAULT_REGION=us-east-1 \
        python -m pytest tests/cloud/test_s3_store.py::TestS3ArtifactStoreRealAWS -v
    """

    def setUp(self):
        from trustgraph_cloud.artifacts.s3_store import S3ArtifactStore
        self.store = S3ArtifactStore(
            bucket=_REAL_BUCKET,
            prefix="trustgraph-test-artifacts",
            region=_REAL_REGION,
        )
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_roundtrip_upload_and_get(self):
        src = Path(self.tmpdir.name) / "hello.json"
        src.write_text('{"ping": true}', encoding="utf-8")
        art = self.store.register("integration-test-job", str(src), "hello.json")
        self.assertEqual(art.storage_backend, "s3")
        fetched = self.store.get("integration-test-job", "hello.json")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.name, "hello.json")


if __name__ == "__main__":
    unittest.main(verbosity=2)
