from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from trustgraph.graph import run_workflow
from trustgraph.models import RiskLevel
from trustgraph_cloud.artifacts.store import ArtifactStore
from trustgraph_cloud.jobs.models import FindingsSummary, JobOptions
from trustgraph_cloud.logging import logger

# Bundled demo contracts shipped with the repo.
_DEMO_SRC = Path(__file__).parent.parent.parent / "examples" / "vulnerable-crosschain" / "src"


class AuditServiceError(Exception):
    """Raised when the audit runner cannot complete due to a known error condition."""


def run_audit(
    job_id: str,
    workspace: Path,
    input_type: str,
    source_path: Optional[str],
    options: JobOptions,
    artifact_store: ArtifactStore,
) -> tuple[FindingsSummary, list[str]]:
    """
    Execute a TrustGraph analysis job inside an isolated workspace.

    Returns:
        (FindingsSummary, list[artifact_name]) on success.

    Raises:
        AuditServiceError on expected failure (bad path, workflow error, etc.)

    Security note:
        Phase 1 is for trusted local development only. Foundry execution against
        untrusted uploaded code requires container isolation and resource limits
        (implemented in Phase 2 via ECS Fargate tasks).
    """
    output_dir = workspace / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Resolve the scan path ─────────────────────────────────────────────────
    if input_type == "demo":
        if not _DEMO_SRC.exists():
            raise AuditServiceError(f"Demo source not found at {_DEMO_SRC}")
        scan_path = str(_DEMO_SRC)
        logger.info("audit.using_demo", extra={"job_id": job_id, "path": scan_path})

    elif input_type == "local_path":
        if not source_path:
            raise AuditServiceError("source_path is required for local_path input")
        src = Path(source_path)
        if not src.exists():
            raise AuditServiceError(f"source_path does not exist: {source_path}")
        # Copy into isolated input/ so the analysis never touches the original tree.
        input_dir = workspace / "input"
        dest = input_dir / src.name
        if src.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src, dest)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        scan_path = str(dest)
        logger.info("audit.copied_source", extra={"job_id": job_id, "scan_path": scan_path})

    else:
        raise AuditServiceError(f"Unknown input_type: {input_type!r}")

    # ── Run the deterministic analysis pipeline ───────────────────────────────
    config = {
        "path": scan_path,
        "generate_test": options.generate_test,
        "run_foundry": options.run_foundry,
        "report_format": options.report_format,
        "output_dir": str(output_dir),
        "no_ai": options.no_ai,
    }
    logger.info("audit.started", extra={"job_id": job_id})

    try:
        state = run_workflow(config)
    except Exception as exc:
        logger.error("audit.workflow_error", extra={"job_id": job_id, "error": str(exc)})
        raise AuditServiceError(f"Workflow error: {exc}") from exc

    # ── Summarise findings ────────────────────────────────────────────────────
    findings = state.get("findings", [])
    critical = sum(1 for f in findings if f.get("risk_level") == RiskLevel.CRITICAL.value)
    medium = sum(1 for f in findings if f.get("risk_level") == RiskLevel.MEDIUM.value)

    # Foundry summary: aggregate across all findings that have foundry_result.
    foundry_ran = any(
        (f.get("foundry_result") or {}).get("ran", False) for f in findings
    )
    foundry_passed: Optional[bool] = None
    if foundry_ran:
        foundry_passed = all(
            (f.get("foundry_result") or {}).get("passed", False)
            for f in findings
            if (f.get("foundry_result") or {}).get("ran", False)
        )

    summary = FindingsSummary(
        critical=critical,
        medium=medium,
        total=len(findings),
        foundry_ran=foundry_ran,
        foundry_passed=foundry_passed,
    )
    logger.info("audit.completed", extra={
        "job_id": job_id,
        "critical": critical,
        "medium": medium,
        "total": len(findings),
    })

    # ── Register artifacts ────────────────────────────────────────────────────
    artifact_names: list[str] = []

    for report_path in state.get("report_paths", []):
        rp = Path(report_path)
        if rp.exists():
            art = artifact_store.register(job_id, str(rp), rp.name)
            artifact_names.append(art.name)

    tests_dir = output_dir / "tests"
    if tests_dir.exists():
        for sol in sorted(tests_dir.glob("*.t.sol")):
            art = artifact_store.register(job_id, str(sol), sol.name)
            artifact_names.append(art.name)

    logger.info("audit.artifacts_written", extra={
        "job_id": job_id,
        "artifacts": artifact_names,
    })

    return summary, artifact_names
