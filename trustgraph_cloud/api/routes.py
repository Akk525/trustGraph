from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from trustgraph_cloud.api.deps import get_artifact_store, get_job_queue, get_job_store
from trustgraph_cloud.api.schemas import (
    ArtifactInfo,
    ArtifactsResponse,
    AuditJobResponse,
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
        error_message=job.error_message,
    )


@router.get("/health", response_model=HealthResponse, tags=["system"])
async def health(request: Request) -> HealthResponse:
    queue = request.app.state.job_queue
    return HealthResponse(status="ok", queue_depth=queue.qsize())


@router.post("/audits", response_model=AuditJobResponse, status_code=202, tags=["audits"])
async def create_audit(
    body: AuditRequest,
    job_store=Depends(get_job_store),
    job_queue=Depends(get_job_queue),
) -> AuditJobResponse:
    """Submit an audit job. Returns immediately with job_id and queued status."""
    input_type = "demo" if body.use_demo else "local_path"
    job = Job(
        input_type=input_type,
        source_path=body.source_path,
        options=body.to_job_options(),
    )
    job_store.create(job)
    await job_queue.enqueue(job.job_id)
    logger.info("api.job_created", extra={"job_id": job.job_id, "input_type": input_type})
    return _to_response(job)


@router.get("/audits/{job_id}", response_model=AuditJobResponse, tags=["audits"])
async def get_audit(
    job_id: str,
    job_store=Depends(get_job_store),
) -> AuditJobResponse:
    """Poll job status and metadata."""
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    return _to_response(job)


@router.get("/audits/{job_id}/artifacts", response_model=ArtifactsResponse, tags=["audits"])
async def list_artifacts(
    job_id: str,
    job_store=Depends(get_job_store),
    artifact_store=Depends(get_artifact_store),
) -> ArtifactsResponse:
    """List available artifacts for a completed job."""
    if job_store.get(job_id) is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
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
