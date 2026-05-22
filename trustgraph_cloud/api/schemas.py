from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, model_validator

from trustgraph_cloud.jobs.models import FindingsSummary, JobOptions, JobStatus


class AuditRequest(BaseModel):
    source_path: Optional[str] = None
    use_demo: bool = False
    input_s3_key: Optional[str] = None
    generate_test: bool = True
    run_foundry: bool = False
    report_format: str = "both"
    no_ai: bool = False

    @model_validator(mode="after")
    def _validate_source(self) -> "AuditRequest":
        provided = sum([
            bool(self.use_demo),
            bool(self.source_path),
            bool(self.input_s3_key),
        ])
        if provided > 1:
            raise ValueError("Provide exactly one of: use_demo, source_path, or input_s3_key")
        if provided == 0:
            raise ValueError("Provide one of: use_demo, source_path, or input_s3_key")
        return self

    def to_job_options(self) -> JobOptions:
        return JobOptions(
            generate_test=self.generate_test,
            run_foundry=self.run_foundry,
            report_format=self.report_format,
            no_ai=self.no_ai,
        )


class AuditJobResponse(BaseModel):
    job_id: str
    status: JobStatus
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    input_type: str
    findings_summary: Optional[FindingsSummary] = None
    artifact_names: list[str] = []
    artifact_count: int = 0
    error_message: Optional[str] = None


class AuditListResponse(BaseModel):
    jobs: list[AuditJobResponse]
    total: Optional[int] = None       # None in DynamoDB cursor mode
    limit: int
    offset: Optional[int] = None      # None in cursor mode
    has_more: bool
    next_cursor: Optional[str] = None  # opaque base64url; present when has_more


class ArtifactInfo(BaseModel):
    name: str
    size_bytes: int
    storage_backend: str = "local"
    path: Optional[str] = None          # local file path (local backend)
    s3_key: Optional[str] = None        # S3 object key (s3 backend)
    presigned_url: Optional[str] = None # signed download URL (s3 backend)
    content_type: Optional[str] = None


class ArtifactsResponse(BaseModel):
    job_id: str
    artifacts: list[ArtifactInfo]


class HealthResponse(BaseModel):
    status: str
    queue_depth: int
    version: str = "0.1.0"


class PresignedUploadRequest(BaseModel):
    filename: str
    content_type: str = "application/zip"


class PresignedUploadResponse(BaseModel):
    upload_url: str
    input_s3_key: str
    expires_in: int


# ---------------------------------------------------------------------------
# Phase 3B — Auth schemas
# ---------------------------------------------------------------------------

class SignupRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserResponse(BaseModel):
    user_id: str
    email: str
    created_at: datetime


class CreateApiKeyRequest(BaseModel):
    name: str


class ApiKeyCreatedResponse(BaseModel):
    key_id: str
    name: str
    key_prefix: str
    raw_key: str        # returned exactly once; not stored
    created_at: datetime


class ApiKeyResponse(BaseModel):
    key_id: str
    name: str
    key_prefix: str
    created_at: datetime
    last_used_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
