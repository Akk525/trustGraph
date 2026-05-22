from __future__ import annotations

import base64
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from trustgraph_cloud.jobs.models import Job


# ---------------------------------------------------------------------------
# Cursor helpers (shared by all JobStore implementations)
# ---------------------------------------------------------------------------

def encode_cursor(payload: dict) -> str:
    """Encode a dict as an opaque base64url pagination cursor."""
    raw = json.dumps(payload, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> dict:
    """
    Decode a cursor back to a dict.
    Raises ValueError on malformed input so the route layer can return 400.
    """
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        return json.loads(base64.urlsafe_b64decode(padded.encode()))
    except Exception as exc:
        raise ValueError("Malformed pagination cursor") from exc


# ---------------------------------------------------------------------------
# Page — returned by list_for_user_page
# ---------------------------------------------------------------------------

@dataclass
class Page:
    items: list                      # list[Job] in practice
    next_cursor: Optional[str]
    has_more: bool
    total: Optional[int] = None      # populated by LocalJobStore; None for DynamoDB


@runtime_checkable
class JobStore(Protocol):
    def create(self, job: Job) -> Job: ...
    def get(self, job_id: str) -> Optional[Job]: ...
    def update(self, job_id: str, **fields) -> Optional[Job]: ...
    def list_all(self) -> list[Job]: ...
    def list_for_user(self, user_id: Optional[str]) -> list[Job]: ...
    def count_for_user_since(self, user_id: str, since: datetime) -> int: ...
    def list_for_user_page(
        self,
        user_id: Optional[str],
        limit: int,
        cursor: Optional[str] = None,
    ) -> Page: ...


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

    def list_for_user(self, user_id: Optional[str]) -> list[Job]:
        return [j for j in self.list_all() if j.user_id == user_id]

    def list_for_user_page(
        self,
        user_id: Optional[str],
        limit: int,
        cursor: Optional[str] = None,
    ) -> Page:
        all_jobs = [j for j in self.list_all() if j.user_id == user_id]
        all_jobs.sort(key=lambda j: j.created_at, reverse=True)
        total = len(all_jobs)
        offset = decode_cursor(cursor)["offset"] if cursor else 0
        page = all_jobs[offset : offset + limit]
        next_offset = offset + limit
        has_more = next_offset < total
        nc = encode_cursor({"offset": next_offset}) if has_more else None
        return Page(items=page, next_cursor=nc, has_more=has_more, total=total)

    def count_for_user_since(self, user_id: str, since: datetime) -> int:
        return sum(
            1 for j in self.list_all()
            if j.user_id == user_id and j.created_at >= since
        )
