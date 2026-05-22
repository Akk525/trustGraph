from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobOptions(BaseModel):
    generate_test: bool = True
    run_foundry: bool = False
    report_format: str = "both"
    no_ai: bool = False


class FindingsSummary(BaseModel):
    critical: int = 0
    medium: int = 0
    total: int = 0
    foundry_ran: bool = False
    foundry_passed: Optional[bool] = None


class Job(BaseModel):
    job_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    status: JobStatus = JobStatus.QUEUED
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    input_type: str                        # "demo" | "local_path" | "s3_upload"
    source_path: Optional[str] = None     # populated for local_path
    input_s3_key: Optional[str] = None    # populated for s3_upload
    user_id: Optional[str] = None         # None when auth_required=false (dev mode)
    options: JobOptions = Field(default_factory=JobOptions)
    error_message: Optional[str] = None
    artifact_names: list[str] = Field(default_factory=list)
    findings_summary: Optional[FindingsSummary] = None
