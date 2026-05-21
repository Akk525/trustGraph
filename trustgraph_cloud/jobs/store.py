from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from trustgraph_cloud.jobs.models import Job


@runtime_checkable
class JobStore(Protocol):
    def create(self, job: Job) -> Job: ...
    def get(self, job_id: str) -> Optional[Job]: ...
    def update(self, job_id: str, **fields) -> Optional[Job]: ...
    def list_all(self) -> list[Job]: ...


class LocalJobStore:
    """
    File-backed job store.

    Each job is persisted as {jobs_dir}/{job_id}/job.json.
    An in-memory lock prevents concurrent read-modify-write on the same instance.

    Phase 2 migration: replace with DynamoDB or Postgres-backed store
    implementing the same JobStore protocol.
    """

    def __init__(self, jobs_dir: Path) -> None:
        self._jobs_dir = jobs_dir
        self._lock = threading.Lock()

    def _job_file(self, job_id: str) -> Path:
        return self._jobs_dir / job_id / "job.json"

    def create(self, job: Job) -> Job:
        job_dir = self._jobs_dir / job.job_id
        for sub in ("input", "output", "artifacts", "logs"):
            (job_dir / sub).mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._job_file(job.job_id).write_text(
                job.model_dump_json(indent=2), encoding="utf-8"
            )
        return job

    def get(self, job_id: str) -> Optional[Job]:
        path = self._job_file(job_id)
        if not path.exists():
            return None
        with self._lock:
            raw = path.read_text(encoding="utf-8")
        return Job.model_validate_json(raw)

    def update(self, job_id: str, **fields) -> Optional[Job]:
        with self._lock:
            path = self._job_file(job_id)
            if not path.exists():
                return None
            job = Job.model_validate_json(path.read_text(encoding="utf-8"))
            updated = job.model_copy(update=fields)
            path.write_text(updated.model_dump_json(indent=2), encoding="utf-8")
        return updated

    def list_all(self) -> list[Job]:
        if not self._jobs_dir.exists():
            return []
        jobs: list[Job] = []
        for job_dir in self._jobs_dir.iterdir():
            f = job_dir / "job.json"
            if f.exists():
                try:
                    with self._lock:
                        raw = f.read_text(encoding="utf-8")
                    jobs.append(Job.model_validate_json(raw))
                except Exception:
                    pass
        return sorted(jobs, key=lambda j: j.created_at)
