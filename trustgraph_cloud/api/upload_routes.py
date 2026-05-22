from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from trustgraph_cloud.api.deps import get_current_user
from trustgraph_cloud.api.schemas import PresignedUploadRequest, PresignedUploadResponse
from trustgraph_cloud.logging import logger

router = APIRouter()


@router.post(
    "/uploads/presigned",
    response_model=PresignedUploadResponse,
    status_code=200,
    tags=["uploads"],
    summary="Generate a presigned S3 PUT URL for direct ZIP upload",
)
async def create_presigned_upload(
    body: PresignedUploadRequest,
    request: Request,
    current_user=Depends(get_current_user),
) -> PresignedUploadResponse:
    """
    Return a presigned S3 PUT URL the client uses to upload a Solidity project ZIP
    directly to S3 — bypassing the API body size limit.

    Flow:
    1. POST /uploads/presigned  →  get upload_url + input_s3_key
    2. PUT upload_url  (client uploads ZIP with Content-Type: application/zip)
    3. POST /audits { "input_s3_key": "..." }  →  create audit job
    """
    s3_input_store = getattr(request.app.state, "s3_input_store", None)
    if s3_input_store is None:
        raise HTTPException(
            status_code=503,
            detail="S3 input store is not configured. "
                   "Set TRUSTGRAPH_ARTIFACT_STORE=s3 and TRUSTGRAPH_S3_BUCKET.",
        )

    try:
        upload_url, s3_key = s3_input_store.generate_upload_url(
            filename=body.filename,
            content_type=body.content_type,
        )
    except Exception as exc:
        logger.error("uploads.presigned_failed", extra={"error": str(exc)})
        raise HTTPException(status_code=500, detail="Failed to generate upload URL")

    ttl = getattr(request.app.state, "upload_url_ttl_seconds", 900)
    logger.info("uploads.presigned_created", extra={"s3_key": s3_key})
    return PresignedUploadResponse(
        upload_url=upload_url,
        input_s3_key=s3_key,
        expires_in=ttl,
    )
