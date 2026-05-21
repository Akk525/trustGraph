"""
Unit and integration tests for the Docker-based audit runner.

Unit tests mock subprocess so no Docker daemon is required.
Integration tests are skipped when Docker is unavailable.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from trustgraph_cloud.artifacts.store import LocalArtifactStore
from trustgraph_cloud.config import Settings
from trustgraph_cloud.jobs.models import FindingsSummary, Job, JobOptions, JobStatus
from trustgraph_cloud.jobs.store import LocalJobStore
from trustgraph_cloud.runner.docker_runner import (
    DockerNotAvailableError,
    DockerRunnerError,
    DockerTimeoutError,
    _check_docker_available,
    _collect_artifacts,
    _parse_findings_summary,
    _prepare_workspace,
    run_audit_in_docker,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_report(critical: int = 1, medium: int = 0, foundry_ran: bool = False) -> dict:
    findings = []
    for _ in range(critical):
        findings.append({
            "severity": "Critical",
            "foundry": {"ran": foundry_ran, "passed": False} if foundry_ran else None,
        })
    for _ in range(medium):
        findings.append({"severity": "Medium", "foundry": None})
    return {
        "tool": "TrustGraph",
        "summary": {"critical": critical, "medium": medium},
        "findings": findings,
    }


def _write_report(output_dir: Path, data: dict) -> None:
    (output_dir / "report.json").write_text(json.dumps(data))


# ---------------------------------------------------------------------------
# TestCheckDockerAvailable
# ---------------------------------------------------------------------------

class TestCheckDockerAvailable(unittest.TestCase):

    @patch("trustgraph_cloud.runner.docker_runner.shutil.which", return_value="/usr/bin/docker")
    def test_returns_path_when_docker_present(self, _):
        self.assertEqual(_check_docker_available(), "/usr/bin/docker")

    @patch("trustgraph_cloud.runner.docker_runner.shutil.which", return_value=None)
    def test_raises_when_docker_missing(self, _):
        with self.assertRaises(DockerNotAvailableError):
            _check_docker_available()

    @patch("trustgraph_cloud.runner.docker_runner.shutil.which", return_value=None)
    def test_not_available_is_subclass_of_docker_runner_error(self, _):
        with self.assertRaises(DockerRunnerError):
            _check_docker_available()


# ---------------------------------------------------------------------------
# TestPrepareWorkspace
# ---------------------------------------------------------------------------

class TestPrepareWorkspace(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmpdir.name) / "workspace"
        # Source contracts are a sibling dir so they never overlap with workspace.
        self.src_root = Path(self.tmpdir.name) / "contracts"
        self.src_root.mkdir()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_local_path_dir_is_copied(self):
        (self.src_root / "Foo.sol").write_text("// sol")
        input_dir = _prepare_workspace(self.workspace, "local_path", str(self.src_root))
        self.assertTrue((input_dir / "contracts" / "Foo.sol").exists())

    def test_local_path_file_is_copied(self):
        sol = self.src_root / "Foo.sol"
        sol.write_text("// sol")
        input_dir = _prepare_workspace(self.workspace, "local_path", str(sol))
        self.assertTrue((input_dir / "Foo.sol").exists())

    def test_missing_source_path_raises(self):
        with self.assertRaises(DockerRunnerError):
            _prepare_workspace(self.workspace, "local_path", "/does/not/exist")

    def test_no_source_path_raises(self):
        with self.assertRaises(DockerRunnerError):
            _prepare_workspace(self.workspace, "local_path", None)

    def test_unknown_input_type_raises(self):
        with self.assertRaises(DockerRunnerError):
            _prepare_workspace(self.workspace, "upload", None)


# ---------------------------------------------------------------------------
# TestParseFindingsSummary
# ---------------------------------------------------------------------------

class TestParseFindingsSummary(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.output_dir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_missing_report_returns_empty_summary(self):
        summary = _parse_findings_summary(self.output_dir)
        self.assertEqual(summary.critical, 0)
        self.assertEqual(summary.medium, 0)
        self.assertFalse(summary.foundry_ran)

    def test_critical_count_parsed(self):
        _write_report(self.output_dir, _make_report(critical=2))
        summary = _parse_findings_summary(self.output_dir)
        self.assertEqual(summary.critical, 2)
        self.assertEqual(summary.medium, 0)

    def test_medium_count_parsed(self):
        _write_report(self.output_dir, _make_report(critical=0, medium=3))
        summary = _parse_findings_summary(self.output_dir)
        self.assertEqual(summary.critical, 0)
        self.assertEqual(summary.medium, 3)

    def test_total_is_sum(self):
        _write_report(self.output_dir, _make_report(critical=1, medium=2))
        summary = _parse_findings_summary(self.output_dir)
        self.assertEqual(summary.total, 3)

    def test_foundry_ran_detected(self):
        _write_report(self.output_dir, _make_report(critical=1, foundry_ran=True))
        summary = _parse_findings_summary(self.output_dir)
        self.assertTrue(summary.foundry_ran)

    def test_no_foundry_when_all_null(self):
        _write_report(self.output_dir, _make_report(critical=1, foundry_ran=False))
        summary = _parse_findings_summary(self.output_dir)
        self.assertFalse(summary.foundry_ran)

    def test_corrupt_json_raises(self):
        (self.output_dir / "report.json").write_text("not json {{{")
        with self.assertRaises(DockerRunnerError):
            _parse_findings_summary(self.output_dir)


# ---------------------------------------------------------------------------
# TestCollectArtifacts
# ---------------------------------------------------------------------------

class TestCollectArtifacts(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.jobs_dir = Path(self.tmpdir.name) / "jobs"
        self.jobs_dir.mkdir()
        self.output_dir = Path(self.tmpdir.name) / "output"
        self.output_dir.mkdir()
        self.store = LocalArtifactStore(self.jobs_dir)
        self.job_id = "test-job-001"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_json_file_collected(self):
        (self.output_dir / "report.json").write_text("{}")
        names = _collect_artifacts(self.job_id, self.output_dir, self.store)
        self.assertIn("report.json", names)

    def test_md_file_collected(self):
        (self.output_dir / "report.md").write_text("# report")
        names = _collect_artifacts(self.job_id, self.output_dir, self.store)
        self.assertIn("report.md", names)

    def test_sol_test_collected(self):
        tests_dir = self.output_dir / "tests"
        tests_dir.mkdir()
        (tests_dir / "ExploitTest.t.sol").write_text("// test")
        names = _collect_artifacts(self.job_id, self.output_dir, self.store)
        self.assertIn("ExploitTest.t.sol", names)

    def test_unknown_extension_ignored(self):
        (self.output_dir / "notes.txt").write_text("ignored")
        names = _collect_artifacts(self.job_id, self.output_dir, self.store)
        self.assertNotIn("notes.txt", names)

    def test_empty_output_dir_returns_empty_list(self):
        names = _collect_artifacts(self.job_id, self.output_dir, self.store)
        self.assertEqual(names, [])


# ---------------------------------------------------------------------------
# TestRunAuditInDockerCommandConstruction
# ---------------------------------------------------------------------------

class TestRunAuditInDockerCommandConstruction(unittest.TestCase):
    """Verify docker run command construction without executing Docker."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmpdir.name) / "workspace"
        self.jobs_dir = Path(self.tmpdir.name) / "jobs"
        self.jobs_dir.mkdir()
        # Contracts dir is a sibling of workspace — never overlaps on copy.
        self.contracts = Path(self.tmpdir.name) / "contracts"
        self.contracts.mkdir()
        (self.contracts / "Foo.sol").write_text("// sol")

    def tearDown(self):
        self.tmpdir.cleanup()

    def _run_with_mock(self, options: JobOptions, **overrides):
        store = LocalArtifactStore(self.jobs_dir)
        # Pre-create output dir and a valid report.json so post-run parsing succeeds.
        output_dir = self.workspace / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        _write_report(output_dir, _make_report())

        defaults = dict(
            job_id="cmd-test",
            workspace=self.workspace,
            input_type="local_path",
            source_path=str(self.contracts),
            options=options,
            artifact_store=store,
            image="trustgraph-worker:latest",
            memory_limit="512m",
            cpu_limit="0.5",
            timeout_seconds=60,
            disable_network=True,
        )
        defaults.update(overrides)

        captured: dict = {}

        def fake_run(cmd, **_kwargs):
            captured["cmd"] = cmd
            return MagicMock(returncode=0, stdout="ok", stderr="")

        with patch("trustgraph_cloud.runner.docker_runner.shutil.which",
                   return_value="/usr/bin/docker"), \
             patch("trustgraph_cloud.runner.docker_runner.subprocess.run",
                   side_effect=fake_run):
            run_audit_in_docker(**defaults)

        return captured["cmd"]

    def test_network_none_when_disabled(self):
        cmd = self._run_with_mock(JobOptions(), disable_network=True)
        self.assertIn("--network", cmd)
        self.assertEqual(cmd[cmd.index("--network") + 1], "none")

    def test_network_flag_absent_when_enabled(self):
        cmd = self._run_with_mock(JobOptions(), disable_network=False)
        self.assertNotIn("none", cmd)

    def test_memory_flag_set(self):
        cmd = self._run_with_mock(JobOptions(), memory_limit="256m")
        self.assertIn("--memory", cmd)
        self.assertIn("256m", cmd)

    def test_cpu_flag_set(self):
        cmd = self._run_with_mock(JobOptions(), cpu_limit="1.0")
        self.assertIn("--cpus", cmd)
        self.assertIn("1.0", cmd)

    def test_no_ai_flag_forwarded(self):
        cmd = self._run_with_mock(JobOptions(no_ai=True))
        self.assertIn("--no-ai", cmd)

    def test_no_ai_not_present_when_false(self):
        cmd = self._run_with_mock(JobOptions(no_ai=False))
        self.assertNotIn("--no-ai", cmd)

    def test_no_generate_test_forwarded(self):
        cmd = self._run_with_mock(JobOptions(generate_test=False))
        self.assertIn("--no-generate-test", cmd)

    def test_run_foundry_forwarded(self):
        cmd = self._run_with_mock(JobOptions(run_foundry=True))
        self.assertIn("--run-foundry", cmd)

    def test_report_format_always_both(self):
        cmd = self._run_with_mock(JobOptions())
        self.assertIn("--report-format", cmd)
        self.assertEqual(cmd[cmd.index("--report-format") + 1], "both")

    def test_output_dir_is_container_path(self):
        cmd = self._run_with_mock(JobOptions())
        self.assertIn("--output-dir", cmd)
        self.assertEqual(cmd[cmd.index("--output-dir") + 1], "/work/output")

    def test_scan_path_is_container_input(self):
        cmd = self._run_with_mock(JobOptions())
        self.assertIn("/work/input", cmd)


# ---------------------------------------------------------------------------
# TestRunAuditInDockerErrors
# ---------------------------------------------------------------------------

class TestRunAuditInDockerErrors(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmpdir.name) / "workspace"
        self.jobs_dir = Path(self.tmpdir.name) / "jobs"
        self.jobs_dir.mkdir()
        self.contracts = Path(self.tmpdir.name) / "contracts"
        self.contracts.mkdir()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _call(self, **overrides):
        store = LocalArtifactStore(self.jobs_dir)
        defaults = dict(
            job_id="err-test",
            workspace=self.workspace,
            input_type="local_path",
            source_path=str(self.contracts),
            options=JobOptions(),
            artifact_store=store,
            image="trustgraph-worker:latest",
            memory_limit="512m",
            cpu_limit="0.5",
            timeout_seconds=60,
            disable_network=True,
        )
        defaults.update(overrides)
        return run_audit_in_docker(**defaults)

    @patch("trustgraph_cloud.runner.docker_runner.shutil.which", return_value=None)
    def test_missing_docker_raises_not_available(self, _):
        with self.assertRaises(DockerNotAvailableError):
            self._call()

    @patch("trustgraph_cloud.runner.docker_runner.shutil.which", return_value="/usr/bin/docker")
    @patch("trustgraph_cloud.runner.docker_runner.subprocess.run",
           return_value=MagicMock(returncode=1, stdout="error output", stderr=""))
    def test_nonzero_exit_raises_docker_runner_error(self, _mock_run, _mock_which):
        with self.assertRaises(DockerRunnerError):
            self._call()

    @patch("trustgraph_cloud.runner.docker_runner.shutil.which", return_value="/usr/bin/docker")
    @patch("trustgraph_cloud.runner.docker_runner.subprocess.run",
           side_effect=__import__("subprocess").TimeoutExpired(cmd=[], timeout=60, output=""))
    def test_timeout_raises_docker_timeout_error(self, _mock_run, _mock_which):
        with self.assertRaises(DockerTimeoutError):
            self._call()

    @patch("trustgraph_cloud.runner.docker_runner.shutil.which", return_value="/usr/bin/docker")
    @patch("trustgraph_cloud.runner.docker_runner.subprocess.run",
           side_effect=OSError("docker daemon not running"))
    def test_os_error_raises_docker_runner_error(self, _mock_run, _mock_which):
        with self.assertRaises(DockerRunnerError):
            self._call()

    @patch("trustgraph_cloud.runner.docker_runner.shutil.which", return_value="/usr/bin/docker")
    def test_nonzero_exit_is_subclass_of_audit_service_error(self, _):
        from trustgraph_cloud.runner.audit_service import AuditServiceError
        with patch("trustgraph_cloud.runner.docker_runner.subprocess.run",
                   return_value=MagicMock(returncode=1, stdout="", stderr="")):
            with self.assertRaises(AuditServiceError):
                self._call()


# ---------------------------------------------------------------------------
# TestWorkerModeDispatch
# ---------------------------------------------------------------------------

class TestWorkerModeDispatch(unittest.TestCase):
    """Verify Worker._process dispatches to the correct runner."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _make_worker_and_job(self, execution_mode: str):
        from trustgraph_cloud.jobs.queue import LocalJobQueue
        from trustgraph_cloud.jobs.worker import Worker

        settings = Settings(
            base_workspace=Path(self.tmpdir.name) / ".trustgraph-cloud",
            execution_mode=execution_mode,
        )
        settings.jobs_dir.mkdir(parents=True, exist_ok=True)

        job_store = LocalJobStore(settings.jobs_dir)
        artifact_store = LocalArtifactStore(settings.jobs_dir)
        queue = LocalJobQueue()
        worker = Worker(
            queue=queue,
            job_store=job_store,
            artifact_store=artifact_store,
            settings=settings,
        )
        job = Job(input_type="local_path", source_path="/nonexistent")
        job_store.create(job)
        return worker, job.job_id

    def test_local_mode_calls_run_audit(self):
        worker, job_id = self._make_worker_and_job("local_host")
        real_summary = FindingsSummary(critical=0, medium=0)
        try:
            with patch("trustgraph_cloud.jobs.worker.run_audit",
                       return_value=(real_summary, [])) as mock_local, \
                 patch("trustgraph_cloud.jobs.worker.run_audit_in_docker") as mock_docker:
                asyncio.run(worker._process(job_id))
            mock_local.assert_called_once()
            mock_docker.assert_not_called()
        finally:
            worker.close()

    def test_docker_mode_calls_run_audit_in_docker(self):
        worker, job_id = self._make_worker_and_job("docker")
        real_summary = FindingsSummary(critical=0, medium=0)
        try:
            with patch("trustgraph_cloud.jobs.worker.run_audit") as mock_local, \
                 patch("trustgraph_cloud.jobs.worker.run_audit_in_docker",
                       return_value=(real_summary, [])) as mock_docker:
                asyncio.run(worker._process(job_id))
            mock_docker.assert_called_once()
            mock_local.assert_not_called()
        finally:
            worker.close()

    def test_docker_runner_error_marks_job_failed(self):
        worker, job_id = self._make_worker_and_job("docker")
        try:
            with patch("trustgraph_cloud.jobs.worker.run_audit_in_docker",
                       side_effect=DockerRunnerError("container failed")):
                asyncio.run(worker._process(job_id))
            job = worker._job_store.get(job_id)
            self.assertEqual(job.status, JobStatus.FAILED)
            self.assertIn("container failed", job.error_message)
        finally:
            worker.close()


# ---------------------------------------------------------------------------
# Integration — skipped if Docker daemon unavailable
# ---------------------------------------------------------------------------

def _docker_daemon_running() -> bool:
    """Return True only if both the docker binary and daemon are accessible."""
    if not shutil.which("docker"):
        return False
    import subprocess
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


@unittest.skipUnless(_docker_daemon_running(), "Docker daemon not available or not running")
class TestDockerIntegration(unittest.TestCase):
    """Sanity checks that run only when the Docker daemon is accessible."""

    def test_docker_daemon_accessible(self):
        import subprocess
        result = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
