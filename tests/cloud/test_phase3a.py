"""
Phase 3A tests: presigned S3 uploads, s3_upload input type, zip safety.

All S3 interaction is mocked with moto.  No real AWS credentials required.
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

try:
    import boto3
    import moto
    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BUCKET = "tg-test-bucket"
INPUT_PREFIX = "trustgraph/inputs"
REGION = "us-east-1"


def _make_zip(members: dict[str, bytes]) -> bytes:
    """Build an in-memory ZIP; members maps member name → content."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# TestZipExtractor — pure Python, no AWS
# ---------------------------------------------------------------------------

class TestZipExtractor(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _write_zip(self, members: dict[str, bytes], name: str = "upload.zip") -> Path:
        path = self.root / name
        path.write_bytes(_make_zip(members))
        return path

    def test_normal_zip_extracts_successfully(self):
        from trustgraph_cloud.runner.zip_extractor import safe_extract
        zp = self._write_zip({"contracts/Token.sol": b"pragma solidity ^0.8.0;"})
        dest = self.root / "extracted"
        safe_extract(zp, dest)
        self.assertTrue((dest / "contracts" / "Token.sol").exists())

    def test_zip_slip_dotdot_rejected(self):
        from trustgraph_cloud.runner.zip_extractor import ZipSlipError, safe_extract
        # Manually build a ZIP with a dangerous path using ZipInfo
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../evil.sh", "rm -rf /")
        zp = self.root / "slip.zip"
        zp.write_bytes(buf.getvalue())

        with self.assertRaises(ZipSlipError):
            safe_extract(zp, self.root / "dest")

    def test_zip_slip_absolute_path_rejected(self):
        from trustgraph_cloud.runner.zip_extractor import ZipSlipError, safe_extract
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            info = zipfile.ZipInfo("/etc/passwd")
            zf.writestr(info, "root:x:0:0")
        zp = self.root / "abs.zip"
        zp.write_bytes(buf.getvalue())

        with self.assertRaises(ZipSlipError):
            safe_extract(zp, self.root / "dest")

    def test_max_file_count_rejected(self):
        from trustgraph_cloud.runner.zip_extractor import ZipTooLargeError, safe_extract
        members = {f"file_{i}.sol": b"x" for i in range(5)}
        zp = self._write_zip(members)
        with self.assertRaises(ZipTooLargeError):
            safe_extract(zp, self.root / "dest", max_files=4)

    def test_max_bytes_rejected(self):
        from trustgraph_cloud.runner.zip_extractor import ZipTooLargeError, safe_extract
        # 3 files × 50 bytes = 150 bytes uncompressed; limit is 100
        members = {f"f{i}.sol": b"x" * 50 for i in range(3)}
        zp = self._write_zip(members)
        with self.assertRaises(ZipTooLargeError):
            safe_extract(zp, self.root / "dest", max_bytes=100)

    def test_symlink_entry_rejected(self):
        from trustgraph_cloud.runner.zip_extractor import ZipSlipError, safe_extract
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            info = zipfile.ZipInfo("link.sol")
            # Set Unix symlink mode: S_ISLNK = 0o120000
            info.external_attr = 0o120777 << 16
            zf.writestr(info, "/etc/passwd")
        zp = self.root / "sym.zip"
        zp.write_bytes(buf.getvalue())

        with self.assertRaises(ZipSlipError):
            safe_extract(zp, self.root / "dest")

    def test_empty_zip_extracts_successfully(self):
        from trustgraph_cloud.runner.zip_extractor import safe_extract
        zp = self._write_zip({})
        dest = self.root / "empty"
        safe_extract(zp, dest)  # should not raise

    def test_max_files_exactly_at_limit_passes(self):
        from trustgraph_cloud.runner.zip_extractor import safe_extract
        members = {f"f{i}.sol": b"x" for i in range(5)}
        zp = self._write_zip(members)
        dest = self.root / "dest_limit"
        safe_extract(zp, dest, max_files=5)  # exactly at limit — should pass


# ---------------------------------------------------------------------------
# TestAuditRequestValidation — schema layer
# ---------------------------------------------------------------------------

class TestAuditRequestValidation(unittest.TestCase):

    def _make(self, **kwargs):
        from trustgraph_cloud.api.schemas import AuditRequest
        return AuditRequest(**kwargs)

    def test_use_demo_accepted(self):
        req = self._make(use_demo=True)
        self.assertTrue(req.use_demo)

    def test_input_s3_key_accepted(self):
        req = self._make(input_s3_key="trustgraph/inputs/uuid/upload.zip")
        self.assertEqual(req.input_s3_key, "trustgraph/inputs/uuid/upload.zip")

    def test_source_path_still_accepted(self):
        req = self._make(source_path="/some/contracts")
        self.assertEqual(req.source_path, "/some/contracts")

    def test_empty_body_rejected(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            self._make()

    def test_use_demo_and_input_s3_key_rejected(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            self._make(use_demo=True, input_s3_key="trustgraph/inputs/x/y.zip")

    def test_all_three_rejected(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            self._make(use_demo=True, source_path="/x", input_s3_key="k")

    def test_input_s3_key_and_source_path_rejected(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            self._make(source_path="/x", input_s3_key="trustgraph/inputs/x/y.zip")

    def test_use_demo_and_source_path_still_rejected(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            self._make(use_demo=True, source_path="/x")


# ---------------------------------------------------------------------------
# TestPresignedEndpoint — API endpoint (moto)
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_MOTO, "moto not installed")
class TestPresignedEndpoint(unittest.TestCase):

    def setUp(self):
        self.mock = moto.mock_aws()
        self.mock.start()
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket=BUCKET)
        self.tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)

    def tearDown(self):
        self.mock.stop()
        self.tmpdir.cleanup()

    def _make_client(self):
        from starlette.testclient import TestClient
        from trustgraph_cloud.api.main import create_app
        from trustgraph_cloud.config import Settings
        settings = Settings(
            base_workspace=Path(self.tmpdir.name) / ".tg",
            artifact_store="s3",
            s3_bucket=BUCKET,
            aws_region=REGION,
            input_s3_prefix=INPUT_PREFIX,
            upload_url_ttl_seconds=900,
            embedded_worker=False,
        )
        return TestClient(create_app(settings=settings))

    def test_presigned_returns_200(self):
        client = self._make_client()
        with client as c:
            resp = c.post("/uploads/presigned", json={
                "filename": "contracts.zip",
                "content_type": "application/zip",
            })
        self.assertEqual(resp.status_code, 200)

    def test_presigned_response_has_required_fields(self):
        client = self._make_client()
        with client as c:
            resp = c.post("/uploads/presigned", json={"filename": "project.zip"})
        data = resp.json()
        self.assertIn("upload_url", data)
        self.assertIn("input_s3_key", data)
        self.assertIn("expires_in", data)

    def test_presigned_s3_key_uses_configured_prefix(self):
        client = self._make_client()
        with client as c:
            resp = c.post("/uploads/presigned", json={"filename": "contracts.zip"})
        key = resp.json()["input_s3_key"]
        self.assertTrue(key.startswith(INPUT_PREFIX + "/"), f"key={key!r}")

    def test_presigned_s3_key_ends_with_filename(self):
        client = self._make_client()
        with client as c:
            resp = c.post("/uploads/presigned", json={"filename": "my_project.zip"})
        key = resp.json()["input_s3_key"]
        self.assertTrue(key.endswith("/my_project.zip"), f"key={key!r}")

    def test_presigned_upload_url_is_string(self):
        client = self._make_client()
        with client as c:
            resp = c.post("/uploads/presigned", json={"filename": "contracts.zip"})
        self.assertIsInstance(resp.json()["upload_url"], str)

    def test_presigned_expires_in_matches_config(self):
        client = self._make_client()
        with client as c:
            resp = c.post("/uploads/presigned", json={"filename": "contracts.zip"})
        self.assertEqual(resp.json()["expires_in"], 900)

    def test_presigned_returns_503_when_s3_not_configured(self):
        from starlette.testclient import TestClient
        from trustgraph_cloud.api.main import create_app
        from trustgraph_cloud.config import Settings
        settings = Settings(
            base_workspace=Path(self.tmpdir.name) / ".tg-local",
            artifact_store="local",
            embedded_worker=False,
        )
        client = TestClient(create_app(settings=settings))
        with client as c:
            resp = c.post("/uploads/presigned", json={"filename": "contracts.zip"})
        self.assertEqual(resp.status_code, 503)


# ---------------------------------------------------------------------------
# TestS3InputAuditRequest — POST /audits with input_s3_key (moto)
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_MOTO, "moto not installed")
class TestS3InputAuditRequest(unittest.TestCase):

    def setUp(self):
        self.mock = moto.mock_aws()
        self.mock.start()
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket=BUCKET)
        self.tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)

    def tearDown(self):
        self.mock.stop()
        self.tmpdir.cleanup()

    def _make_client(self):
        from starlette.testclient import TestClient
        from trustgraph_cloud.api.main import create_app
        from trustgraph_cloud.config import Settings
        settings = Settings(
            base_workspace=Path(self.tmpdir.name) / ".tg",
            artifact_store="s3",
            s3_bucket=BUCKET,
            aws_region=REGION,
            input_s3_prefix=INPUT_PREFIX,
            embedded_worker=False,
        )
        return TestClient(create_app(settings=settings))

    def test_s3_upload_job_accepted(self):
        client = self._make_client()
        with client as c:
            resp = c.post("/audits", json={
                "input_s3_key": f"{INPUT_PREFIX}/uuid/contracts.zip",
            })
        self.assertEqual(resp.status_code, 202)

    def test_s3_upload_input_type_recorded(self):
        client = self._make_client()
        with client as c:
            resp = c.post("/audits", json={
                "input_s3_key": f"{INPUT_PREFIX}/uuid/contracts.zip",
            })
        self.assertEqual(resp.json()["input_type"], "s3_upload")

    def test_s3_upload_rejected_when_s3_not_configured(self):
        from starlette.testclient import TestClient
        from trustgraph_cloud.api.main import create_app
        from trustgraph_cloud.config import Settings
        settings = Settings(
            base_workspace=Path(self.tmpdir.name) / ".tg-local",
            artifact_store="local",
            embedded_worker=False,
        )
        client = TestClient(create_app(settings=settings))
        with client as c:
            resp = c.post("/audits", json={"input_s3_key": "trustgraph/inputs/x/y.zip"})
        self.assertEqual(resp.status_code, 400)


# ---------------------------------------------------------------------------
# TestS3InputDownload — S3InputStore downloads from mocked S3
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_MOTO, "moto not installed")
class TestS3InputDownload(unittest.TestCase):

    def setUp(self):
        self.mock = moto.mock_aws()
        self.mock.start()
        self.s3 = boto3.session.Session().client("s3", region_name=REGION)
        self.s3.create_bucket(Bucket=BUCKET)
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.mock.stop()
        self.tmpdir.cleanup()

    def _store(self):
        from trustgraph_cloud.artifacts.s3_input_store import S3InputStore
        return S3InputStore(
            bucket=BUCKET,
            prefix=INPUT_PREFIX,
            region=REGION,
            upload_ttl=900,
        )

    def test_generate_upload_url_returns_url_and_key(self):
        store = self._store()
        url, key = store.generate_upload_url("contracts.zip", "application/zip")
        self.assertIsInstance(url, str)
        self.assertIn("contracts.zip", key)
        self.assertTrue(key.startswith(INPUT_PREFIX + "/"))

    def test_generate_upload_url_key_is_unique(self):
        store = self._store()
        _, key1 = store.generate_upload_url("a.zip", "application/zip")
        _, key2 = store.generate_upload_url("a.zip", "application/zip")
        self.assertNotEqual(key1, key2)

    def test_download_retrieves_correct_content(self):
        store = self._store()
        s3_key = f"{INPUT_PREFIX}/test-uuid/test.zip"
        content = b"hello zip content"
        self.s3.put_object(Bucket=BUCKET, Key=s3_key, Body=content)

        dest = Path(self.tmpdir.name) / "downloaded.zip"
        store.download(s3_key, dest)
        self.assertTrue(dest.exists())
        self.assertEqual(dest.read_bytes(), content)

    def test_download_creates_parent_dirs(self):
        store = self._store()
        s3_key = f"{INPUT_PREFIX}/uuid/f.zip"
        self.s3.put_object(Bucket=BUCKET, Key=s3_key, Body=b"zip")

        dest = Path(self.tmpdir.name) / "sub" / "dir" / "f.zip"
        store.download(s3_key, dest)
        self.assertTrue(dest.exists())


# ---------------------------------------------------------------------------
# TestWorkerWithS3Input — worker runs full s3_upload flow (moto + mock workflow)
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_MOTO, "moto not installed")
class TestWorkerWithS3Input(unittest.TestCase):

    def setUp(self):
        self.mock = moto.mock_aws()
        self.mock.start()
        self.s3 = boto3.session.Session().client("s3", region_name=REGION)
        self.s3.create_bucket(Bucket=BUCKET)
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

    def tearDown(self):
        self.mock.stop()
        self.tmpdir.cleanup()

    def _upload_zip(self, s3_key: str, sol_content: bytes = b"pragma solidity ^0.8.0;") -> None:
        zip_bytes = _make_zip({"Token.sol": sol_content})
        self.s3.put_object(Bucket=BUCKET, Key=s3_key, Body=zip_bytes)

    def test_audit_service_s3_upload_extracts_and_scans(self):
        from trustgraph_cloud.artifacts.s3_input_store import S3InputStore
        from trustgraph_cloud.artifacts.store import LocalArtifactStore
        from trustgraph_cloud.jobs.models import JobOptions
        from trustgraph_cloud.runner.audit_service import run_audit

        s3_key = f"{INPUT_PREFIX}/run-uuid/Token.zip"
        self._upload_zip(s3_key)

        s3_input_store = S3InputStore(
            bucket=BUCKET,
            prefix=INPUT_PREFIX,
            region=REGION,
        )
        workspace = self.root / "ws"
        artifact_store = LocalArtifactStore(self.root)

        mock_state = {"findings": [], "report_paths": [], "errors": []}
        with patch("trustgraph_cloud.runner.audit_service.run_workflow", return_value=mock_state):
            summary, artifacts = run_audit(
                job_id="s3-job-001",
                workspace=workspace,
                input_type="s3_upload",
                source_path=None,
                options=JobOptions(),
                artifact_store=artifact_store,
                input_s3_key=s3_key,
                s3_input_store=s3_input_store,
            )

        self.assertEqual(summary.total, 0)
        # Extracted file should exist inside the workspace
        extracted = workspace / "input" / "extracted" / "Token.sol"
        self.assertTrue(extracted.exists())

    def test_s3_upload_without_input_store_raises(self):
        from trustgraph_cloud.artifacts.store import LocalArtifactStore
        from trustgraph_cloud.jobs.models import JobOptions
        from trustgraph_cloud.runner.audit_service import AuditServiceError, run_audit

        workspace = self.root / "ws2"
        artifact_store = LocalArtifactStore(self.root)

        with self.assertRaises(AuditServiceError) as ctx:
            run_audit(
                job_id="s3-job-002",
                workspace=workspace,
                input_type="s3_upload",
                source_path=None,
                options=JobOptions(),
                artifact_store=artifact_store,
                input_s3_key="trustgraph/inputs/x/y.zip",
                s3_input_store=None,
            )
        self.assertIn("not configured", str(ctx.exception))

    def test_s3_upload_missing_key_raises(self):
        from trustgraph_cloud.artifacts.s3_input_store import S3InputStore
        from trustgraph_cloud.artifacts.store import LocalArtifactStore
        from trustgraph_cloud.jobs.models import JobOptions
        from trustgraph_cloud.runner.audit_service import AuditServiceError, run_audit

        s3_input_store = S3InputStore(bucket=BUCKET, prefix=INPUT_PREFIX, region=REGION)
        workspace = self.root / "ws3"
        artifact_store = LocalArtifactStore(self.root)

        with self.assertRaises(AuditServiceError):
            run_audit(
                job_id="s3-job-003",
                workspace=workspace,
                input_type="s3_upload",
                source_path=None,
                options=JobOptions(),
                artifact_store=artifact_store,
                input_s3_key=None,
                s3_input_store=s3_input_store,
            )

    def test_s3_upload_bad_zip_raises(self):
        from trustgraph_cloud.artifacts.s3_input_store import S3InputStore
        from trustgraph_cloud.artifacts.store import LocalArtifactStore
        from trustgraph_cloud.jobs.models import JobOptions
        from trustgraph_cloud.runner.audit_service import AuditServiceError, run_audit

        s3_key = f"{INPUT_PREFIX}/bad-uuid/corrupt.zip"
        # Upload something that is not a valid ZIP
        self.s3.put_object(Bucket=BUCKET, Key=s3_key, Body=b"not a zip file")

        s3_input_store = S3InputStore(bucket=BUCKET, prefix=INPUT_PREFIX, region=REGION)
        workspace = self.root / "ws4"
        artifact_store = LocalArtifactStore(self.root)

        with self.assertRaises(AuditServiceError) as ctx:
            run_audit(
                job_id="s3-job-004",
                workspace=workspace,
                input_type="s3_upload",
                source_path=None,
                options=JobOptions(),
                artifact_store=artifact_store,
                input_s3_key=s3_key,
                s3_input_store=s3_input_store,
            )
        self.assertIn("ZIP extraction failed", str(ctx.exception))

    def test_zip_slip_in_s3_upload_raises(self):
        from trustgraph_cloud.artifacts.s3_input_store import S3InputStore
        from trustgraph_cloud.artifacts.store import LocalArtifactStore
        from trustgraph_cloud.jobs.models import JobOptions
        from trustgraph_cloud.runner.audit_service import AuditServiceError, run_audit

        # Build a ZIP with a path traversal entry
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../escape.sh", "rm -rf /")
        s3_key = f"{INPUT_PREFIX}/slip-uuid/evil.zip"
        self.s3.put_object(Bucket=BUCKET, Key=s3_key, Body=buf.getvalue())

        s3_input_store = S3InputStore(bucket=BUCKET, prefix=INPUT_PREFIX, region=REGION)
        workspace = self.root / "ws5"
        artifact_store = LocalArtifactStore(self.root)

        with self.assertRaises(AuditServiceError) as ctx:
            run_audit(
                job_id="s3-job-005",
                workspace=workspace,
                input_type="s3_upload",
                source_path=None,
                options=JobOptions(),
                artifact_store=artifact_store,
                input_s3_key=s3_key,
                s3_input_store=s3_input_store,
            )
        self.assertIn("ZIP validation failed", str(ctx.exception))


# ---------------------------------------------------------------------------
# TestDemoPathUnchanged — regression: demo flow still works after 3A changes
# ---------------------------------------------------------------------------

class TestDemoPathUnchanged(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_demo_job_still_works(self):
        from trustgraph_cloud.artifacts.store import LocalArtifactStore
        from trustgraph_cloud.jobs.models import JobOptions
        from trustgraph_cloud.runner.audit_service import run_audit

        demo_dir = self.root / "demo" / "src"
        demo_dir.mkdir(parents=True)
        workspace = self.root / "ws"
        workspace.mkdir()
        artifact_store = LocalArtifactStore(self.root)

        mock_state = {"findings": [], "report_paths": [], "errors": []}
        with patch("trustgraph_cloud.runner.audit_service.run_workflow", return_value=mock_state):
            summary, _ = run_audit(
                job_id="demo-job",
                workspace=workspace,
                input_type="demo",
                source_path=None,
                options=JobOptions(),
                artifact_store=artifact_store,
                demo_source_path=str(demo_dir),
            )
        self.assertEqual(summary.total, 0)

    def test_local_path_job_still_works(self):
        from trustgraph_cloud.artifacts.store import LocalArtifactStore
        from trustgraph_cloud.jobs.models import JobOptions
        from trustgraph_cloud.runner.audit_service import run_audit

        src_dir = self.root / "contracts"
        src_dir.mkdir()
        (src_dir / "Token.sol").write_text("pragma solidity ^0.8.0;", encoding="utf-8")
        workspace = self.root / "ws2"
        workspace.mkdir()
        artifact_store = LocalArtifactStore(self.root)

        mock_state = {"findings": [], "report_paths": [], "errors": []}
        with patch("trustgraph_cloud.runner.audit_service.run_workflow", return_value=mock_state):
            summary, _ = run_audit(
                job_id="local-job",
                workspace=workspace,
                input_type="local_path",
                source_path=str(src_dir),
                options=JobOptions(),
                artifact_store=artifact_store,
            )
        self.assertEqual(summary.total, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
