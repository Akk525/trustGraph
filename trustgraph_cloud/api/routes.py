from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from trustgraph_cloud.api.deps import (
    get_artifact_store,
    get_current_user,
    get_job_queue,
    get_job_store,
)
from trustgraph_cloud.api.quota import check_quotas
from trustgraph_cloud.api.schemas import (
    ArtifactInfo,
    ArtifactsResponse,
    AuditJobResponse,
    AuditListResponse,
    AuditRequest,
    HealthResponse,
)
from trustgraph_cloud.jobs.models import Job
from trustgraph_cloud.logging import logger

router = APIRouter()


def _to_response(job: Job) -> AuditJobResponse:
    return AuditJobResponse(
        job_id=job.job_id,
        status=job.status,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        input_type=job.input_type,
        findings_summary=job.findings_summary,
        artifact_names=job.artifact_names,
        artifact_count=len(job.artifact_names),
        error_message=job.error_message,
    )


def _check_ownership(job: Job, current_user, request: Request) -> None:
    """Raise 404 if the authenticated user does not own this job."""
    s = request.app.state.settings
    if s.auth_required and job.user_id and (
        current_user is None or job.user_id != current_user.user_id
    ):
        raise HTTPException(status_code=404, detail=f"Job {job.job_id!r} not found")


@router.get("/health", response_model=HealthResponse, tags=["system"])
async def health(request: Request) -> HealthResponse:
    queue = request.app.state.job_queue
    return HealthResponse(status="ok", queue_depth=queue.qsize())


@router.get("/audits", response_model=AuditListResponse, tags=["audits"])
async def list_audits(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: Optional[str] = Query(default=None),
    offset: Optional[int] = Query(default=None, ge=0),
    job_store=Depends(get_job_store),
    current_user=Depends(get_current_user),
) -> AuditListResponse:
    """
    List audit jobs for the current user, newest first.

    Default mode (cursor): O(limit) DynamoDB reads; next_cursor present when
    more pages exist. Pass cursor=<next_cursor> to fetch the next page.

    Backward-compat mode (offset): pass offset=N to use classic pagination.
    cursor and offset cannot be combined.
    """
    s = request.app.state.settings
    if s.auth_required and current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    if cursor is not None and offset is not None:
        raise HTTPException(
            status_code=400,
            detail="Provide cursor or offset, not both",
        )

    user_id = current_user.user_id if current_user else None

    if offset is not None:
        # Backward-compat offset mode: return total count
        all_jobs = job_store.list_for_user(user_id)
        all_jobs.sort(key=lambda j: j.created_at, reverse=True)
        total = len(all_jobs)
        page_jobs = all_jobs[offset : offset + limit]
        return AuditListResponse(
            jobs=[_to_response(j) for j in page_jobs],
            total=total,
            limit=limit,
            offset=offset,
            has_more=(offset + limit) < total,
            next_cursor=None,
        )

    # Cursor mode (default): one targeted query per page
    try:
        page = job_store.list_for_user_page(user_id, limit=limit, cursor=cursor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return AuditListResponse(
        jobs=[_to_response(j) for j in page.items],
        total=page.total,
        limit=limit,
        offset=None,
        has_more=page.has_more,
        next_cursor=page.next_cursor,
    )


@router.post("/audits", response_model=AuditJobResponse, status_code=202, tags=["audits"])
async def create_audit(
    body: AuditRequest,
    request: Request,
    job_store=Depends(get_job_store),
    job_queue=Depends(get_job_queue),
    current_user=Depends(get_current_user),
) -> AuditJobResponse:
    """Submit an audit job. Returns immediately with job_id and queued status."""
    s = request.app.state.settings

    if body.use_demo:
        input_type = "demo"
    elif body.input_s3_key:
        input_type = "s3_upload"
        if getattr(request.app.state, "s3_input_store", None) is None:
            raise HTTPException(
                status_code=400,
                detail="S3 input store is not configured; cannot accept s3_upload jobs",
            )
    else:
        input_type = "local_path"

    check_quotas(
        job_store=job_store,
        user_id=current_user.user_id if current_user else None,
        max_audits_per_day=s.max_audits_per_day,
        max_active_jobs=s.max_active_jobs,
    )

    job = Job(
        input_type=input_type,
        source_path=body.source_path,
        input_s3_key=body.input_s3_key,
        user_id=current_user.user_id if current_user else None,
        options=body.to_job_options(),
    )
    job_store.create(job)
    await job_queue.enqueue(job.job_id)
    logger.info("api.job_created", extra={
        "job_id": job.job_id,
        "input_type": input_type,
        "user_id": job.user_id,
    })
    return _to_response(job)


@router.get("/audits/{job_id}", response_model=AuditJobResponse, tags=["audits"])
async def get_audit(
    job_id: str,
    request: Request,
    job_store=Depends(get_job_store),
    current_user=Depends(get_current_user),
) -> AuditJobResponse:
    """Poll job status and metadata."""
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    _check_ownership(job, current_user, request)
    return _to_response(job)


@router.get("/audits/{job_id}/artifacts", response_model=ArtifactsResponse, tags=["audits"])
async def list_artifacts(
    job_id: str,
    request: Request,
    job_store=Depends(get_job_store),
    artifact_store=Depends(get_artifact_store),
    current_user=Depends(get_current_user),
) -> ArtifactsResponse:
    """List available artifacts for a completed job."""
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    _check_ownership(job, current_user, request)
    arts = artifact_store.list(job_id)
    return ArtifactsResponse(
        job_id=job_id,
        artifacts=[
            ArtifactInfo(
                name=a.name,
                size_bytes=a.size_bytes,
                storage_backend=a.storage_backend,
                path=a.path if a.path else None,
                s3_key=a.s3_key,
                presigned_url=a.presigned_url,
                content_type=a.content_type,
            )
            for a in arts
        ],
    )
