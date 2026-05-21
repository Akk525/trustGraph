from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from trustgraph_cloud.artifacts.store import ArtifactStore
from trustgraph_cloud.jobs.models import FindingsSummary, JobOptions
from trustgraph_cloud.logging import logger
from trustgraph_cloud.runner.audit_service import AuditServiceError

_DEMO_SRC = Path(__file__).parent.parent.parent / "examples" / "vulnerable-crosschain" / "src"


class DockerRunnerError(AuditServiceError):
    """Container execution failed (non-zero exit, OOM, bad image, etc.)."""


class DockerTimeoutError(DockerRunnerError):
    """Container exceeded the allowed execution time."""


class DockerNotAvailableError(DockerRunnerError):
    """Docker binary not present on the host."""


def _check_docker_available() -> str:
    path = shutil.which("docker")
    if not path:
        raise DockerNotAvailableError(
            "docker binary not found; install Docker or set "
            "TRUSTGRAPH_EXECUTION_MODE=local_host"
        )
    return path


def _prepare_workspace(
    workspace: Path,
    input_type: str,
    source_path: Optional[str],
) -> Path:
    """
    Copy source files into workspace/input/ and return that directory.

    The container mounts workspace/input as /work/input:ro, so the
    scan path passed to the container CLI is always /work/input.
    """
    input_dir = workspace / "input"
    input_dir.mkdir(parents=True, exist_ok=True)

    if input_type == "demo":
        if not _DEMO_SRC.exists():
            raise DockerRunnerError(f"Demo source not found at {_DEMO_SRC}")
        dest = input_dir / _DEMO_SRC.name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(_DEMO_SRC, dest)
        return input_dir

    if input_type == "local_path":
        if not source_path:
            raise DockerRunnerError("source_path required for local_path input")
        src = Path(source_path)
        if not src.exists():
            raise DockerRunnerError(f"source_path does not exist: {source_path}")
        dest = input_dir / src.name
        if src.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src, dest)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        return input_dir

    raise DockerRunnerError(f"Unknown input_type: {input_type!r}")


def _parse_findings_summary(output_dir: Path) -> FindingsSummary:
    """
    Parse report.json written by the container to produce a FindingsSummary.

    Falls back to an empty summary if no report exists (e.g., container found
    no Solidity files), rather than raising — a zero-finding result is valid.
    """
    report_path = output_dir / "report.json"
    if not report_path.exists():
        return FindingsSummary()

    try:
        data = json.loads(report_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise DockerRunnerError(f"Failed to parse report.json: {exc}") from exc

    summary = data.get("summary", {})
    critical = int(summary.get("critical", 0))
    medium = int(summary.get("medium", 0))

    findings = data.get("findings", [])
    foundry_ran = any((f.get("foundry") or {}).get("ran", False) for f in findings)
    foundry_passed: Optional[bool] = None
    if foundry_ran:
        foundry_passed = all(
            (f.get("foundry") or {}).get("passed", False)
            for f in findings
            if (f.get("foundry") or {}).get("ran", False)
        )

    return FindingsSummary(
        critical=critical,
        medium=medium,
        total=critical + medium,
        foundry_ran=foundry_ran,
        foundry_passed=foundry_passed,
    )


def _collect_artifacts(
    job_id: str,
    output_dir: Path,
    artifact_store: ArtifactStore,
) -> list[str]:
    artifact_names: list[str] = []

    for candidate in sorted(output_dir.iterdir()):
        if candidate.is_file() and candidate.suffix in {".json", ".md"}:
            art = artifact_store.register(job_id, str(candidate), candidate.name)
            artifact_names.append(art.name)

    tests_dir = output_dir / "tests"
    if tests_dir.exists():
        for sol in sorted(tests_dir.glob("*.t.sol")):
            art = artifact_store.register(job_id, str(sol), sol.name)
            artifact_names.append(art.name)

    return artifact_names


def run_audit_in_docker(
    job_id: str,
    workspace: Path,
    input_type: str,
    source_path: Optional[str],
    options: JobOptions,
    artifact_store: ArtifactStore,
    image: str,
    memory_limit: str,
    cpu_limit: str,
    timeout_seconds: int,
    disable_network: bool,
) -> tuple[FindingsSummary, list[str]]:
    """
    Run a TrustGraph audit inside an isolated Docker container.

    The container mounts workspace/input as read-only and writes all outputs
    to workspace/output. Container stdout/stderr is captured to
    workspace/logs/container.log.

    Raises:
        DockerNotAvailableError  — docker binary missing
        DockerTimeoutError       — container ran past timeout_seconds
        DockerRunnerError        — non-zero exit or other failure
    """
    docker_bin = _check_docker_available()

    output_dir = workspace / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = workspace / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    input_dir = _prepare_workspace(workspace, input_type, source_path)

    cmd: list[str] = [docker_bin, "run", "--rm"]

    if disable_network:
        cmd += ["--network", "none"]
    if memory_limit:
        cmd += ["--memory", memory_limit]
    if cpu_limit:
        cmd += ["--cpus", cpu_limit]

    cmd += ["-v", f"{input_dir.resolve()}:/work/input:ro"]
    cmd += ["-v", f"{output_dir.resolve()}:/work/output"]

    # Always produce both formats so report.json is available for parsing.
    # The markdown file is registered as an artifact too.
    cmd += [image, "audit", "/work/input"]
    cmd += ["--report-format", "both", "--output-dir", "/work/output"]

    if not options.generate_test:
        cmd += ["--no-generate-test"]
    if options.run_foundry:
        cmd += ["--run-foundry"]
    if options.no_ai:
        cmd += ["--no-ai"]

    logger.info("container.started", extra={
        "job_id": job_id,
        "image": image,
        "network_disabled": disable_network,
    })

    log_file = logs_dir / "container.log"

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        log_file.write_text(exc.stdout or "")
        logger.error("container.timeout", extra={
            "job_id": job_id,
            "timeout_seconds": timeout_seconds,
        })
        raise DockerTimeoutError(
            f"Container exceeded {timeout_seconds}s timeout"
        ) from exc
    except OSError as exc:
        logger.error("container.failed", extra={"job_id": job_id, "error": str(exc)})
        raise DockerRunnerError(f"Failed to launch container: {exc}") from exc

    combined = result.stdout + ("\n" + result.stderr if result.stderr else "")
    log_file.write_text(combined)

    logger.info("container.completed", extra={
        "job_id": job_id,
        "returncode": result.returncode,
    })

    if result.returncode != 0:
        raise DockerRunnerError(
            f"Container exited {result.returncode}; see {log_file}"
        )

    summary = _parse_findings_summary(output_dir)
    artifact_names = _collect_artifacts(job_id, output_dir, artifact_store)

    logger.info("container.artifacts_collected", extra={
        "job_id": job_id,
        "count": len(artifact_names),
        "critical": summary.critical,
        "medium": summary.medium,
    })

    return summary, artifact_names
