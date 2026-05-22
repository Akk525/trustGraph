"""
Phase 3D tests — trustgraph-cloud CLI.

Covers:
- ZIP creation and exclusion rules
- login command stores token
- audit command: invalid path, missing auth, presign+upload+submit flow
- jobs command: table output, empty list
- status command: shows job info, handles 404
- download command: writes artifacts, handles empty list
- missing auth gives useful error messages
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, call, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from typer.testing import CliRunner

from trustgraph_cloud.cli import app, _zip_folder
from trustgraph_cloud.cli_client import CloudAPIError

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_job(
    job_id: str = "job-abc",
    status: str = "succeeded",
    input_type: str = "s3_upload",
    artifact_count: int = 2,
    artifact_names: Optional[list] = None,
    findings_summary: Optional[dict] = None,
    error_message: Optional[str] = None,
) -> dict:
    return {
        "job_id": job_id,
        "status": status,
        "created_at": "2024-01-15T10:30:00",
        "started_at": "2024-01-15T10:30:05",
        "completed_at": "2024-01-15T10:31:00",
        "input_type": input_type,
        "artifact_count": artifact_count,
        "artifact_names": artifact_names or ["report.md", "report.json"],
        "findings_summary": findings_summary,
        "error_message": error_message,
    }


def _unzip(data: bytes) -> set[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return set(zf.namelist())


# ---------------------------------------------------------------------------
# ZIP exclusion
# ---------------------------------------------------------------------------

class TestZipFolder(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _create(self, rel_path: str, content: str = "x") -> None:
        p = self.root / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    def test_includes_solidity_files(self):
        self._create("src/Token.sol")
        self._create("src/Vault.sol")
        names = _unzip(_zip_folder(self.root))
        self.assertIn("src/Token.sol", names)
        self.assertIn("src/Vault.sol", names)

    def test_excludes_git_directory(self):
        self._create("src/Token.sol")
        self._create(".git/config")
        self._create(".git/objects/abc")
        names = _unzip(_zip_folder(self.root))
        self.assertTrue(all(".git" not in n for n in names))
        self.assertIn("src/Token.sol", names)

    def test_excludes_node_modules(self):
        self._create("contracts/Token.sol")
        self._create("node_modules/hardhat/index.js")
        names = _unzip(_zip_folder(self.root))
        self.assertTrue(all("node_modules" not in n for n in names))

    def test_excludes_venv(self):
        self._create("Token.sol")
        self._create(".venv/lib/python3.12/site.py")
        names = _unzip(_zip_folder(self.root))
        self.assertTrue(all(".venv" not in n for n in names))

    def test_excludes_pycache(self):
        self._create("script.py")
        self._create("__pycache__/script.cpython-312.pyc")
        names = _unzip(_zip_folder(self.root))
        self.assertTrue(all("__pycache__" not in n for n in names))

    def test_excludes_pyc_suffix(self):
        self._create("script.pyc")
        self._create("script.py")
        names = _unzip(_zip_folder(self.root))
        self.assertNotIn("script.pyc", names)
        self.assertIn("script.py", names)

    def test_excludes_ds_store(self):
        self._create(".DS_Store")
        self._create("Token.sol")
        names = _unzip(_zip_folder(self.root))
        self.assertNotIn(".DS_Store", names)
        self.assertIn("Token.sol", names)

    def test_excludes_out_directory(self):
        self._create("Token.sol")
        self._create("out/Token.sol/Token.json")
        names = _unzip(_zip_folder(self.root))
        self.assertTrue(all(not n.startswith("out/") for n in names))

    def test_excludes_nested_ignored_dir(self):
        self._create("packages/core/src/Token.sol")
        self._create("packages/core/node_modules/dep/index.js")
        names = _unzip(_zip_folder(self.root))
        self.assertIn("packages/core/src/Token.sol", names)
        self.assertTrue(all("node_modules" not in n for n in names))

    def test_empty_directory_produces_empty_zip(self):
        data = _zip_folder(self.root)
        names = _unzip(data)
        self.assertEqual(names, set())


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------

class TestLoginCommand(unittest.TestCase):

    def test_login_stores_token_on_success(self):
        with patch("trustgraph_cloud.cli_client.login") as mock_login, \
             patch("trustgraph_cloud.cli_config.save_login") as mock_save:
            mock_login.return_value = {"access_token": "tok123", "expires_in": 3600}
            result = runner.invoke(app, [
                "login",
                "--api-url", "http://api.example.com",
                "--email", "a@b.com",
                "--password", "pass123",
            ])
        self.assertEqual(result.exit_code, 0)
        mock_save.assert_called_once_with("http://api.example.com", "tok123")
        self.assertIn("Logged in", result.output)

    def test_login_wrong_password_prints_error_and_exits_1(self):
        with patch("trustgraph_cloud.cli_client.login") as mock_login:
            mock_login.side_effect = CloudAPIError(401, "Invalid email or password")
            result = runner.invoke(app, [
                "login",
                "--api-url", "http://api.example.com",
                "--email", "a@b.com",
                "--password", "wrong",
            ])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Login failed", result.output)
        self.assertIn("Invalid email or password", result.output)

    def test_login_uses_prompt_when_options_omitted(self):
        with patch("trustgraph_cloud.cli_client.login") as mock_login, \
             patch("trustgraph_cloud.cli_config.save_login"):
            mock_login.return_value = {"access_token": "t", "expires_in": 3600}
            result = runner.invoke(
                app,
                ["login"],
                input="http://api.example.com\nuser@example.com\nmypassword\n",
            )
        self.assertEqual(result.exit_code, 0)
        mock_login.assert_called_once_with(
            "http://api.example.com", "user@example.com", "mypassword"
        )


# ---------------------------------------------------------------------------
# api-key create
# ---------------------------------------------------------------------------

class TestApiKeyCreateCommand(unittest.TestCase):

    def _auth_patches(self):
        return [
            patch("trustgraph_cloud.cli_config.get_api_url", return_value="http://api.example.com"),
            patch("trustgraph_cloud.cli_config.get_token", return_value="tok"),
        ]

    def test_create_api_key_prints_raw_key(self):
        with patch("trustgraph_cloud.cli_config.get_api_url", return_value="http://api.example.com"), \
             patch("trustgraph_cloud.cli_config.get_token", return_value="tok"), \
             patch("trustgraph_cloud.cli_client.create_api_key") as mock_create:
            mock_create.return_value = {
                "key_id": "kid1",
                "name": "ci-key",
                "key_prefix": "tg_live_abc",
                "raw_key": "tg_live_abc123supersecret",
                "created_at": "2024-01-15T10:00:00",
            }
            result = runner.invoke(app, ["api-key", "create", "--name", "ci-key"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("tg_live_abc123supersecret", result.output)
        self.assertIn("ci-key", result.output)

    def test_create_api_key_no_auth_prints_useful_error(self):
        with patch("trustgraph_cloud.cli_config.get_api_url", return_value=None), \
             patch("trustgraph_cloud.cli_config.get_token", return_value=None):
            result = runner.invoke(app, ["api-key", "create", "--name", "x"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("No API URL", result.output)


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------

class TestAuditCommand(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.src = Path(self.tmpdir.name) / "contracts"
        self.src.mkdir()
        (self.src / "Token.sol").write_text("contract Token {}")

    def tearDown(self):
        self.tmpdir.cleanup()

    def _auth(self):
        return [
            patch("trustgraph_cloud.cli_config.get_api_url", return_value="http://api.example.com"),
            patch("trustgraph_cloud.cli_config.get_token", return_value="tok"),
        ]

    def test_invalid_path_exits_1(self):
        with patch("trustgraph_cloud.cli_config.get_api_url", return_value="http://api.example.com"), \
             patch("trustgraph_cloud.cli_config.get_token", return_value="tok"):
            result = runner.invoke(app, ["audit", "/nonexistent/path/that/does/not/exist"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("does not exist", result.output)

    def test_no_auth_gives_useful_error(self):
        with patch("trustgraph_cloud.cli_config.get_api_url", return_value=None), \
             patch("trustgraph_cloud.cli_config.get_token", return_value=None):
            result = runner.invoke(app, ["audit", str(self.src)])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("No API URL", result.output)
        self.assertIn("trustgraph-cloud login", result.output)

    def test_no_token_gives_useful_error(self):
        with patch("trustgraph_cloud.cli_config.get_api_url", return_value="http://api.example.com"), \
             patch("trustgraph_cloud.cli_config.get_token", return_value=None):
            result = runner.invoke(app, ["audit", str(self.src)])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Not authenticated", result.output)

    def test_audit_calls_presign_upload_submit_in_order(self):
        with patch("trustgraph_cloud.cli_config.get_api_url", return_value="http://api.example.com"), \
             patch("trustgraph_cloud.cli_config.get_token", return_value="tok"), \
             patch("trustgraph_cloud.cli_client.presigned_upload") as mock_presign, \
             patch("trustgraph_cloud.cli_client.upload_to_s3") as mock_upload, \
             patch("trustgraph_cloud.cli_client.submit_audit") as mock_submit:

            mock_presign.return_value = {
                "upload_url": "https://s3.example.com/upload",
                "input_s3_key": "inputs/uuid/contracts.zip",
                "expires_in": 900,
            }
            mock_upload.return_value = None
            mock_submit.return_value = _make_job(job_id="job-xyz", status="queued")

            result = runner.invoke(app, ["audit", str(self.src)])

        self.assertEqual(result.exit_code, 0, result.output)
        mock_presign.assert_called_once()
        mock_upload.assert_called_once()
        mock_submit.assert_called_once_with(
            "http://api.example.com", "tok", "inputs/uuid/contracts.zip"
        )
        self.assertIn("job-xyz", result.output)

    def test_audit_presign_passes_zip_bytes_to_upload(self):
        uploaded_bytes: list[bytes] = []

        def capture_upload(url, data):
            uploaded_bytes.append(data)

        with patch("trustgraph_cloud.cli_config.get_api_url", return_value="http://api.example.com"), \
             patch("trustgraph_cloud.cli_config.get_token", return_value="tok"), \
             patch("trustgraph_cloud.cli_client.presigned_upload") as mock_presign, \
             patch("trustgraph_cloud.cli_client.upload_to_s3", side_effect=capture_upload), \
             patch("trustgraph_cloud.cli_client.submit_audit") as mock_submit:

            mock_presign.return_value = {
                "upload_url": "https://s3.example.com/upload",
                "input_s3_key": "inputs/uuid/contracts.zip",
                "expires_in": 900,
            }
            mock_submit.return_value = _make_job(status="queued")
            runner.invoke(app, ["audit", str(self.src)])

        self.assertEqual(len(uploaded_bytes), 1)
        # Verify it's a valid ZIP containing the sol file
        names = _unzip(uploaded_bytes[0])
        self.assertIn("Token.sol", names)

    def test_audit_presign_error_exits_1(self):
        with patch("trustgraph_cloud.cli_config.get_api_url", return_value="http://api.example.com"), \
             patch("trustgraph_cloud.cli_config.get_token", return_value="tok"), \
             patch("trustgraph_cloud.cli_client.presigned_upload") as mock_presign:
            mock_presign.side_effect = CloudAPIError(503, "S3 not configured")
            result = runner.invoke(app, ["audit", str(self.src)])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("S3 not configured", result.output)

    def test_audit_wait_polls_until_succeeded(self):
        job_states = [
            _make_job(status="queued"),
            _make_job(status="running"),
            _make_job(status="succeeded", findings_summary={"critical": 1, "medium": 0, "total": 1}),
        ]

        with patch("trustgraph_cloud.cli_config.get_api_url", return_value="http://api.example.com"), \
             patch("trustgraph_cloud.cli_config.get_token", return_value="tok"), \
             patch("trustgraph_cloud.cli_client.presigned_upload") as mock_presign, \
             patch("trustgraph_cloud.cli_client.upload_to_s3"), \
             patch("trustgraph_cloud.cli_client.submit_audit") as mock_submit, \
             patch("trustgraph_cloud.cli_client.get_job", side_effect=job_states) as mock_get, \
             patch("time.sleep"):

            mock_presign.return_value = {
                "upload_url": "https://s3.example.com/upload",
                "input_s3_key": "inputs/uuid/contracts.zip",
                "expires_in": 900,
            }
            mock_submit.return_value = _make_job(status="queued")
            result = runner.invoke(app, ["audit", str(self.src), "--wait", "--poll-interval", "0"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(mock_get.call_count, 3)
        self.assertIn("succeeded", result.output)

    def test_audit_wait_exits_1_on_failure(self):
        with patch("trustgraph_cloud.cli_config.get_api_url", return_value="http://api.example.com"), \
             patch("trustgraph_cloud.cli_config.get_token", return_value="tok"), \
             patch("trustgraph_cloud.cli_client.presigned_upload") as mock_presign, \
             patch("trustgraph_cloud.cli_client.upload_to_s3"), \
             patch("trustgraph_cloud.cli_client.submit_audit") as mock_submit, \
             patch("trustgraph_cloud.cli_client.get_job") as mock_get, \
             patch("time.sleep"):

            mock_presign.return_value = {
                "upload_url": "https://s3.example.com/upload",
                "input_s3_key": "k",
                "expires_in": 900,
            }
            mock_submit.return_value = _make_job(status="queued")
            mock_get.return_value = _make_job(
                status="failed", error_message="out of memory"
            )
            result = runner.invoke(app, ["audit", str(self.src), "--wait", "--poll-interval", "0"])

        self.assertEqual(result.exit_code, 1)
        self.assertIn("out of memory", result.output)


# ---------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------

class TestJobsCommand(unittest.TestCase):

    def test_jobs_shows_table_with_job_info(self):
        jobs_response = {
            "jobs": [
                _make_job(job_id="job-1", status="succeeded"),
                _make_job(job_id="job-2", status="running"),
            ],
            "total": 2,
            "limit": 20,
            "offset": 0,
            "has_more": False,
        }
        with patch("trustgraph_cloud.cli_config.get_api_url", return_value="http://api.example.com"), \
             patch("trustgraph_cloud.cli_config.get_token", return_value="tok"), \
             patch("trustgraph_cloud.cli_client.list_jobs", return_value=jobs_response):
            result = runner.invoke(app, ["jobs"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("job-1", result.output)
        self.assertIn("job-2", result.output)
        self.assertIn("succeeded", result.output)
        self.assertIn("running", result.output)

    def test_jobs_empty_prints_message(self):
        empty = {"jobs": [], "total": 0, "limit": 20, "offset": 0, "has_more": False}
        with patch("trustgraph_cloud.cli_config.get_api_url", return_value="http://api.example.com"), \
             patch("trustgraph_cloud.cli_config.get_token", return_value="tok"), \
             patch("trustgraph_cloud.cli_client.list_jobs", return_value=empty):
            result = runner.invoke(app, ["jobs"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("No jobs", result.output)

    def test_jobs_shows_pagination_hint_when_has_more(self):
        response = {
            "jobs": [_make_job(job_id=f"job-{i}") for i in range(5)],
            "total": 50,
            "limit": 5,
            "offset": 0,
            "has_more": True,
        }
        with patch("trustgraph_cloud.cli_config.get_api_url", return_value="http://api.example.com"), \
             patch("trustgraph_cloud.cli_config.get_token", return_value="tok"), \
             patch("trustgraph_cloud.cli_client.list_jobs", return_value=response):
            result = runner.invoke(app, ["jobs"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("--offset", result.output)

    def test_jobs_no_auth_exits_1(self):
        with patch("trustgraph_cloud.cli_config.get_api_url", return_value=None), \
             patch("trustgraph_cloud.cli_config.get_token", return_value=None):
            result = runner.invoke(app, ["jobs"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("trustgraph-cloud login", result.output)

    def test_jobs_api_error_exits_1(self):
        with patch("trustgraph_cloud.cli_config.get_api_url", return_value="http://api.example.com"), \
             patch("trustgraph_cloud.cli_config.get_token", return_value="tok"), \
             patch("trustgraph_cloud.cli_client.list_jobs") as mock:
            mock.side_effect = CloudAPIError(401, "Token expired")
            result = runner.invoke(app, ["jobs"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Token expired", result.output)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

class TestStatusCommand(unittest.TestCase):

    def test_status_shows_job_info(self):
        job = _make_job(
            job_id="job-abc",
            status="succeeded",
            findings_summary={"critical": 2, "medium": 1, "total": 3},
        )
        with patch("trustgraph_cloud.cli_config.get_api_url", return_value="http://api.example.com"), \
             patch("trustgraph_cloud.cli_config.get_token", return_value="tok"), \
             patch("trustgraph_cloud.cli_client.get_job", return_value=job):
            result = runner.invoke(app, ["status", "job-abc"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("job-abc", result.output)
        self.assertIn("succeeded", result.output)
        self.assertIn("2 critical", result.output)

    def test_status_not_found_exits_1(self):
        with patch("trustgraph_cloud.cli_config.get_api_url", return_value="http://api.example.com"), \
             patch("trustgraph_cloud.cli_config.get_token", return_value="tok"), \
             patch("trustgraph_cloud.cli_client.get_job") as mock:
            mock.side_effect = CloudAPIError(404, "Job not found")
            result = runner.invoke(app, ["status", "ghost-id"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Job not found", result.output)

    def test_status_failed_job_shows_error_message(self):
        job = _make_job(
            status="failed",
            error_message="Docker OOM: container killed",
        )
        with patch("trustgraph_cloud.cli_config.get_api_url", return_value="http://api.example.com"), \
             patch("trustgraph_cloud.cli_config.get_token", return_value="tok"), \
             patch("trustgraph_cloud.cli_client.get_job", return_value=job):
            result = runner.invoke(app, ["status", "job-abc"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Docker OOM", result.output)

    def test_status_no_auth_exits_1(self):
        with patch("trustgraph_cloud.cli_config.get_api_url", return_value=None), \
             patch("trustgraph_cloud.cli_config.get_token", return_value=None):
            result = runner.invoke(app, ["status", "job-abc"])
        self.assertEqual(result.exit_code, 1)


# ---------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------

class TestDownloadCommand(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_download_writes_artifacts_to_output_dir(self):
        artifacts_response = {
            "job_id": "job-abc",
            "artifacts": [
                {
                    "name": "report.md",
                    "size_bytes": 500,
                    "storage_backend": "s3",
                    "presigned_url": "https://s3.example.com/report.md?sig=x",
                    "content_type": "text/markdown",
                },
                {
                    "name": "report.json",
                    "size_bytes": 200,
                    "storage_backend": "s3",
                    "presigned_url": "https://s3.example.com/report.json?sig=y",
                    "content_type": "application/json",
                },
            ],
        }
        file_contents = {
            "https://s3.example.com/report.md?sig=x": b"# Report",
            "https://s3.example.com/report.json?sig=y": b'{"findings": []}',
        }

        out_dir = str(Path(self.tmpdir.name) / "output")

        with patch("trustgraph_cloud.cli_config.get_api_url", return_value="http://api.example.com"), \
             patch("trustgraph_cloud.cli_config.get_token", return_value="tok"), \
             patch("trustgraph_cloud.cli_client.list_artifacts", return_value=artifacts_response), \
             patch("trustgraph_cloud.cli_client.download_artifact", side_effect=lambda url: file_contents[url]):
            result = runner.invoke(app, ["download", "job-abc", "--out", out_dir])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue((Path(out_dir) / "report.md").exists())
        self.assertEqual((Path(out_dir) / "report.md").read_bytes(), b"# Report")
        self.assertEqual((Path(out_dir) / "report.json").read_bytes(), b'{"findings": []}')
        self.assertIn("2/2", result.output)

    def test_download_no_artifacts_prints_message(self):
        with patch("trustgraph_cloud.cli_config.get_api_url", return_value="http://api.example.com"), \
             patch("trustgraph_cloud.cli_config.get_token", return_value="tok"), \
             patch("trustgraph_cloud.cli_client.list_artifacts", return_value={"job_id": "j", "artifacts": []}):
            result = runner.invoke(app, ["download", "job-abc", "--out", self.tmpdir.name])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("No artifacts", result.output)

    def test_download_job_not_found_exits_1(self):
        with patch("trustgraph_cloud.cli_config.get_api_url", return_value="http://api.example.com"), \
             patch("trustgraph_cloud.cli_config.get_token", return_value="tok"), \
             patch("trustgraph_cloud.cli_client.list_artifacts") as mock:
            mock.side_effect = CloudAPIError(404, "Job not found")
            result = runner.invoke(app, ["download", "ghost-id", "--out", self.tmpdir.name])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Job not found", result.output)

    def test_download_creates_output_dir_if_missing(self):
        out_dir = Path(self.tmpdir.name) / "new" / "nested" / "dir"
        self.assertFalse(out_dir.exists())

        with patch("trustgraph_cloud.cli_config.get_api_url", return_value="http://api.example.com"), \
             patch("trustgraph_cloud.cli_config.get_token", return_value="tok"), \
             patch("trustgraph_cloud.cli_client.list_artifacts", return_value={"job_id": "j", "artifacts": []}):
            runner.invoke(app, ["download", "job-abc", "--out", str(out_dir)])

        self.assertTrue(out_dir.exists())

    def test_download_skips_artifact_without_url(self):
        artifacts_response = {
            "job_id": "job-abc",
            "artifacts": [
                {
                    "name": "mystery.bin",
                    "size_bytes": 0,
                    "storage_backend": "local",
                    "presigned_url": None,
                    "path": None,
                },
            ],
        }
        out_dir = str(Path(self.tmpdir.name) / "output")

        with patch("trustgraph_cloud.cli_config.get_api_url", return_value="http://api.example.com"), \
             patch("trustgraph_cloud.cli_config.get_token", return_value="tok"), \
             patch("trustgraph_cloud.cli_client.list_artifacts", return_value=artifacts_response):
            result = runner.invoke(app, ["download", "job-abc", "--out", out_dir])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Skipping", result.output)

    def test_download_no_auth_exits_1(self):
        with patch("trustgraph_cloud.cli_config.get_api_url", return_value=None), \
             patch("trustgraph_cloud.cli_config.get_token", return_value=None):
            result = runner.invoke(app, ["download", "job-abc"])
        self.assertEqual(result.exit_code, 1)


# ---------------------------------------------------------------------------
# cli_config unit tests
# ---------------------------------------------------------------------------

class TestCliConfig(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.config_file = Path(self.tmpdir.name) / "config.json"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_save_login_writes_url_and_token(self):
        with patch("trustgraph_cloud.cli_config.CONFIG_FILE", self.config_file), \
             patch("trustgraph_cloud.cli_config.CONFIG_DIR", self.config_file.parent):
            from trustgraph_cloud.cli_config import save_login
            save_login("http://api.example.com", "tok123")

        data = json.loads(self.config_file.read_text())
        self.assertEqual(data["api_url"], "http://api.example.com")
        self.assertEqual(data["access_token"], "tok123")

    def test_get_api_url_prefers_env_var(self):
        with patch("trustgraph_cloud.cli_config.CONFIG_FILE", self.config_file), \
             patch.dict(os.environ, {"TRUSTGRAPH_API_URL": "http://env.example.com"}):
            from trustgraph_cloud.cli_config import get_api_url
            self.assertEqual(get_api_url(), "http://env.example.com")

    def test_get_token_prefers_api_key_env_over_jwt(self):
        with patch.dict(os.environ, {
            "TRUSTGRAPH_API_KEY": "tg_live_abc",
            "TRUSTGRAPH_API_TOKEN": "eyJhbGci",
        }):
            from trustgraph_cloud.cli_config import get_token
            self.assertEqual(get_token(), "tg_live_abc")

    def test_get_token_falls_back_to_jwt_env(self):
        env = {"TRUSTGRAPH_API_TOKEN": "eyJhbGci"}
        # Ensure API key env is not set
        with patch.dict(os.environ, env):
            os.environ.pop("TRUSTGRAPH_API_KEY", None)
            from trustgraph_cloud.cli_config import get_token
            self.assertEqual(get_token(), "eyJhbGci")

    def test_get_token_returns_none_when_nothing_configured(self):
        with patch("trustgraph_cloud.cli_config.CONFIG_FILE", self.config_file):
            env_keys = ["TRUSTGRAPH_API_KEY", "TRUSTGRAPH_API_TOKEN"]
            clean_env = {k: "" for k in env_keys}
            with patch.dict(os.environ, clean_env):
                for k in env_keys:
                    os.environ.pop(k, None)
                from trustgraph_cloud.cli_config import get_token
                result = get_token()
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
