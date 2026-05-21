"""
Unit tests for the audit service runner.

Mocks run_workflow so no actual Solidity analysis or Foundry execution occurs.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from trustgraph_cloud.artifacts.store import LocalArtifactStore
from trustgraph_cloud.jobs.models import JobOptions
from trustgraph_cloud.runner.audit_service import AuditServiceError, run_audit

_MOCK_STATE_CLEAN = {
    "findings": [],
    "report_paths": [],
    "errors": [],
}

_MOCK_STATE_TWO_FINDINGS = {
    "findings": [
        {"risk_level": "Critical", "foundry_result": {"ran": True, "passed": True}},
        {"risk_level": "Medium", "foundry_result": None},
    ],
    "report_paths": [],
    "errors": [],
}


class TestRunAuditDemo(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.workspace = self.root / "job-001"
        self.workspace.mkdir()
        self.artifact_store = LocalArtifactStore(self.root)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_demo_returns_empty_summary_on_no_findings(self):
        with patch("trustgraph_cloud.runner.audit_service.run_workflow",
                   return_value=_MOCK_STATE_CLEAN):
            summary, artifacts = run_audit(
                job_id="job-001",
                workspace=self.workspace,
                input_type="demo",
                source_path=None,
                options=JobOptions(),
                artifact_store=self.artifact_store,
            )
        self.assertEqual(summary.critical, 0)
        self.assertEqual(summary.medium, 0)
        self.assertEqual(summary.total, 0)
        self.assertEqual(artifacts, [])

    def test_demo_counts_findings_correctly(self):
        with patch("trustgraph_cloud.runner.audit_service.run_workflow",
                   return_value=_MOCK_STATE_TWO_FINDINGS):
            summary, _ = run_audit(
                job_id="job-001",
                workspace=self.workspace,
                input_type="demo",
                source_path=None,
                options=JobOptions(),
                artifact_store=self.artifact_store,
            )
        self.assertEqual(summary.critical, 1)
        self.assertEqual(summary.medium, 1)
        self.assertEqual(summary.total, 2)

    def test_foundry_ran_aggregated_from_findings(self):
        with patch("trustgraph_cloud.runner.audit_service.run_workflow",
                   return_value=_MOCK_STATE_TWO_FINDINGS):
            summary, _ = run_audit(
                job_id="job-001",
                workspace=self.workspace,
                input_type="demo",
                source_path=None,
                options=JobOptions(),
                artifact_store=self.artifact_store,
            )
        self.assertTrue(summary.foundry_ran)
        self.assertTrue(summary.foundry_passed)

    def test_artifacts_registered_when_report_files_exist(self):
        output_dir = self.workspace / "output"
        output_dir.mkdir(parents=True)
        (output_dir / "report.md").write_text("# report", encoding="utf-8")
        (output_dir / "report.json").write_text("{}", encoding="utf-8")

        state = {
            "findings": [],
            "report_paths": [
                str(output_dir / "report.md"),
                str(output_dir / "report.json"),
            ],
            "errors": [],
        }
        with patch("trustgraph_cloud.runner.audit_service.run_workflow", return_value=state):
            _, artifacts = run_audit(
                job_id="job-001",
                workspace=self.workspace,
                input_type="demo",
                source_path=None,
                options=JobOptions(),
                artifact_store=self.artifact_store,
            )
        self.assertIn("report.md", artifacts)
        self.assertIn("report.json", artifacts)

    def test_sol_tests_registered_as_artifacts(self):
        output_dir = self.workspace / "output"
        tests_dir = output_dir / "tests"
        tests_dir.mkdir(parents=True)
        (tests_dir / "VulnerableReceiverExploit.t.sol").write_text("// sol", encoding="utf-8")

        with patch("trustgraph_cloud.runner.audit_service.run_workflow",
                   return_value=_MOCK_STATE_CLEAN):
            _, artifacts = run_audit(
                job_id="job-001",
                workspace=self.workspace,
                input_type="demo",
                source_path=None,
                options=JobOptions(),
                artifact_store=self.artifact_store,
            )
        self.assertIn("VulnerableReceiverExploit.t.sol", artifacts)


class TestRunAuditLocalPath(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.workspace = self.root / "job-002"
        self.workspace.mkdir()
        self.artifact_store = LocalArtifactStore(self.root)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_local_sol_file_is_copied_to_workspace(self):
        # Create a real .sol file in a separate temp location.
        src_dir = self.root / "contracts"
        src_dir.mkdir()
        sol = src_dir / "MyContract.sol"
        sol.write_text("pragma solidity ^0.8.0;", encoding="utf-8")

        with patch("trustgraph_cloud.runner.audit_service.run_workflow",
                   return_value=_MOCK_STATE_CLEAN):
            run_audit(
                job_id="job-002",
                workspace=self.workspace,
                input_type="local_path",
                source_path=str(sol),
                options=JobOptions(),
                artifact_store=self.artifact_store,
            )
        # Original must not be touched — copy must exist in workspace.
        copied = self.workspace / "input" / "MyContract.sol"
        self.assertTrue(copied.exists())
        self.assertTrue(sol.exists())

    def test_nonexistent_source_path_raises(self):
        with self.assertRaises(AuditServiceError):
            run_audit(
                job_id="job-002",
                workspace=self.workspace,
                input_type="local_path",
                source_path="/does/not/exist.sol",
                options=JobOptions(),
                artifact_store=self.artifact_store,
            )

    def test_missing_source_path_raises(self):
        with self.assertRaises(AuditServiceError):
            run_audit(
                job_id="job-002",
                workspace=self.workspace,
                input_type="local_path",
                source_path=None,
                options=JobOptions(),
                artifact_store=self.artifact_store,
            )


class TestRunAuditDemoPathResolution(unittest.TestCase):
    """Verify demo path resolution: explicit setting → candidates → error."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.workspace = self.root / "job-path"
        self.workspace.mkdir()
        self.artifact_store = LocalArtifactStore(self.root)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_explicit_demo_source_path_is_used(self):
        demo_dir = self.root / "explicit-demo" / "src"
        demo_dir.mkdir(parents=True)
        used_paths = []

        def capture_config(config):
            used_paths.append(config["path"])
            return _MOCK_STATE_CLEAN

        with patch("trustgraph_cloud.runner.audit_service.run_workflow", side_effect=capture_config):
            run_audit(
                job_id="job-path",
                workspace=self.workspace,
                input_type="demo",
                source_path=None,
                options=JobOptions(),
                artifact_store=self.artifact_store,
                demo_source_path=str(demo_dir),
            )

        self.assertEqual(used_paths[0], str(demo_dir))

    def test_explicit_path_takes_priority_over_defaults(self):
        # Both explicit and a default candidate exist — explicit wins.
        explicit_dir = self.root / "explicit" / "src"
        explicit_dir.mkdir(parents=True)

        default_dir = self.root / "default" / "src"
        default_dir.mkdir(parents=True)

        used_paths = []

        def capture_config(config):
            used_paths.append(config["path"])
            return _MOCK_STATE_CLEAN

        from trustgraph_cloud.runner import audit_service as _svc
        original = _svc._DEFAULT_DEMO_CANDIDATES
        try:
            _svc._DEFAULT_DEMO_CANDIDATES = [default_dir]
            with patch("trustgraph_cloud.runner.audit_service.run_workflow", side_effect=capture_config):
                run_audit(
                    job_id="job-path",
                    workspace=self.workspace,
                    input_type="demo",
                    source_path=None,
                    options=JobOptions(),
                    artifact_store=self.artifact_store,
                    demo_source_path=str(explicit_dir),
                )
        finally:
            _svc._DEFAULT_DEMO_CANDIDATES = original

        self.assertEqual(used_paths[0], str(explicit_dir))

    def test_fallback_to_default_candidate_when_explicit_missing(self):
        fallback_dir = self.root / "fallback" / "src"
        fallback_dir.mkdir(parents=True)

        used_paths = []

        def capture_config(config):
            used_paths.append(config["path"])
            return _MOCK_STATE_CLEAN

        from trustgraph_cloud.runner import audit_service as _svc
        original = _svc._DEFAULT_DEMO_CANDIDATES
        try:
            _svc._DEFAULT_DEMO_CANDIDATES = [fallback_dir]
            with patch("trustgraph_cloud.runner.audit_service.run_workflow", side_effect=capture_config):
                run_audit(
                    job_id="job-path",
                    workspace=self.workspace,
                    input_type="demo",
                    source_path=None,
                    options=JobOptions(),
                    artifact_store=self.artifact_store,
                    demo_source_path="/does/not/exist",
                )
        finally:
            _svc._DEFAULT_DEMO_CANDIDATES = original

        self.assertEqual(used_paths[0], str(fallback_dir))

    def test_missing_demo_raises_and_lists_all_candidates(self):
        from trustgraph_cloud.runner import audit_service as _svc
        original = _svc._DEFAULT_DEMO_CANDIDATES
        fake_candidates = [Path("/fake/a"), Path("/fake/b")]
        try:
            _svc._DEFAULT_DEMO_CANDIDATES = fake_candidates
            with self.assertRaises(AuditServiceError) as ctx:
                run_audit(
                    job_id="job-path",
                    workspace=self.workspace,
                    input_type="demo",
                    source_path=None,
                    options=JobOptions(),
                    artifact_store=self.artifact_store,
                    demo_source_path="/explicit/missing",
                )
        finally:
            _svc._DEFAULT_DEMO_CANDIDATES = original

        msg = str(ctx.exception)
        self.assertIn("/explicit/missing", msg)
        self.assertIn("/fake/a", msg)
        self.assertIn("/fake/b", msg)

    def test_missing_demo_no_explicit_raises_and_lists_defaults(self):
        from trustgraph_cloud.runner import audit_service as _svc
        original = _svc._DEFAULT_DEMO_CANDIDATES
        fake_candidates = [Path("/fake/c")]
        try:
            _svc._DEFAULT_DEMO_CANDIDATES = fake_candidates
            with self.assertRaises(AuditServiceError) as ctx:
                run_audit(
                    job_id="job-path",
                    workspace=self.workspace,
                    input_type="demo",
                    source_path=None,
                    options=JobOptions(),
                    artifact_store=self.artifact_store,
                )
        finally:
            _svc._DEFAULT_DEMO_CANDIDATES = original

        self.assertIn("/fake/c", str(ctx.exception))


class TestRunAuditErrors(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.workspace = self.root / "job-err"
        self.workspace.mkdir()
        self.artifact_store = LocalArtifactStore(self.root)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_unknown_input_type_raises(self):
        with self.assertRaises(AuditServiceError):
            run_audit(
                job_id="job-err",
                workspace=self.workspace,
                input_type="uploaded_zip",
                source_path=None,
                options=JobOptions(),
                artifact_store=self.artifact_store,
            )

    def test_workflow_exception_wrapped_as_audit_service_error(self):
        with patch("trustgraph_cloud.runner.audit_service.run_workflow",
                   side_effect=RuntimeError("boom")):
            with self.assertRaises(AuditServiceError) as ctx:
                run_audit(
                    job_id="job-err",
                    workspace=self.workspace,
                    input_type="demo",
                    source_path=None,
                    options=JobOptions(),
                    artifact_store=self.artifact_store,
                )
        self.assertIn("boom", str(ctx.exception))


class TestLocalJobStore(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.jobs_dir = Path(self.tmpdir.name) / "jobs"
        self.jobs_dir.mkdir()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_create_and_get(self):
        from trustgraph_cloud.jobs.models import Job
        from trustgraph_cloud.jobs.store import LocalJobStore

        store = LocalJobStore(self.jobs_dir)
        job = Job(input_type="demo")
        store.create(job)
        retrieved = store.get(job.job_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.job_id, job.job_id)

    def test_get_nonexistent_returns_none(self):
        from trustgraph_cloud.jobs.store import LocalJobStore

        store = LocalJobStore(self.jobs_dir)
        self.assertIsNone(store.get("does-not-exist"))

    def test_update_status(self):
        from trustgraph_cloud.jobs.models import Job, JobStatus
        from trustgraph_cloud.jobs.store import LocalJobStore

        store = LocalJobStore(self.jobs_dir)
        job = Job(input_type="demo")
        store.create(job)
        store.update(job.job_id, status=JobStatus.RUNNING)
        updated = store.get(job.job_id)
        self.assertEqual(updated.status, JobStatus.RUNNING)

    def test_update_nonexistent_returns_none(self):
        from trustgraph_cloud.jobs.models import JobStatus
        from trustgraph_cloud.jobs.store import LocalJobStore

        store = LocalJobStore(self.jobs_dir)
        result = store.update("nonexistent", status=JobStatus.FAILED)
        self.assertIsNone(result)

    def test_list_all_returns_sorted_by_created_at(self):
        from trustgraph_cloud.jobs.models import Job
        from trustgraph_cloud.jobs.store import LocalJobStore

        store = LocalJobStore(self.jobs_dir)
        j1 = Job(input_type="demo")
        j2 = Job(input_type="local_path")
        store.create(j1)
        store.create(j2)
        jobs = store.list_all()
        self.assertEqual(len(jobs), 2)
        self.assertLessEqual(jobs[0].created_at, jobs[1].created_at)

    def test_workspace_subdirectories_created_on_create(self):
        from trustgraph_cloud.jobs.models import Job
        from trustgraph_cloud.jobs.store import LocalJobStore

        store = LocalJobStore(self.jobs_dir)
        job = Job(input_type="demo")
        store.create(job)
        job_dir = self.jobs_dir / job.job_id
        for sub in ("input", "output", "artifacts", "logs"):
            self.assertTrue((job_dir / sub).exists(), f"Missing subdirectory: {sub}")


class TestLocalArtifactStore(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.jobs_dir = Path(self.tmpdir.name) / "jobs"
        self.jobs_dir.mkdir()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _make_store(self):
        from trustgraph_cloud.artifacts.store import LocalArtifactStore
        return LocalArtifactStore(self.jobs_dir)

    def test_register_and_list(self):
        store = self._make_store()
        src = Path(self.tmpdir.name) / "report.md"
        src.write_text("# report", encoding="utf-8")
        store.register("job-001", str(src), "report.md")
        arts = store.list("job-001")
        self.assertEqual(len(arts), 1)
        self.assertEqual(arts[0].name, "report.md")

    def test_list_empty_for_unknown_job(self):
        store = self._make_store()
        self.assertEqual(store.list("unknown"), [])

    def test_get_existing_artifact(self):
        store = self._make_store()
        src = Path(self.tmpdir.name) / "report.json"
        src.write_text("{}", encoding="utf-8")
        store.register("job-001", str(src), "report.json")
        art = store.get("job-001", "report.json")
        self.assertIsNotNone(art)
        self.assertEqual(art.name, "report.json")

    def test_get_nonexistent_returns_none(self):
        store = self._make_store()
        self.assertIsNone(store.get("job-001", "missing.md"))

    def test_size_bytes_populated(self):
        store = self._make_store()
        content = "hello world"
        src = Path(self.tmpdir.name) / "data.md"
        src.write_text(content, encoding="utf-8")
        art = store.register("job-001", str(src), "data.md")
        self.assertGreater(art.size_bytes, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
