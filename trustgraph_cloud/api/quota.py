from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException

from trustgraph_cloud.jobs.models import JobStatus


_ACTIVE_STATUSES = {JobStatus.QUEUED, JobStatus.RUNNING}


def check_quotas(
    job_store,
    user_id: Optional[str],
    max_audits_per_day: int,
    max_active_jobs: int,
) -> None:
    """
    Raise HTTP 429 if any per-user quota is exceeded.

    No-op when user_id is None (auth_required=false dev mode) — the quota
    model only applies when user identity is established.

    Quotas checked:
      1. Active jobs (queued + running) < max_active_jobs
         — list_for_user + Python status filter; status has no GSI.
      2. Jobs created today (UTC) < max_audits_per_day
         — Phase 4B: count_for_user_since(start_of_day) range query so only
           today's partition slice is read; Select=COUNT skips item transfer.
    """
    if user_id is None:
        return

    user_jobs = job_store.list_for_user(user_id)

    active = sum(1 for j in user_jobs if j.status in _ACTIVE_STATUSES)
    if active >= max_active_jobs:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Active job limit reached ({max_active_jobs}). "
                "Wait for a running job to complete before submitting another."
            ),
        )

    today_start = datetime.now(tz=timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    daily = job_store.count_for_user_since(user_id, today_start)
    if daily >= max_audits_per_day:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Daily audit limit reached ({max_audits_per_day}). "
                "Try again tomorrow (UTC)."
            ),
        )
