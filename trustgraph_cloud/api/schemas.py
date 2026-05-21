from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, model_validator

from trustgraph_cloud.jobs.models import FindingsSummary, JobOptions, JobStatus


class AuditRequest(BaseModel):
    source_path: Optional[str] = None
    use_demo: bool = False
    generate_test: bool = True
    run_foundry: bool = False
    report_format: str = "both"
    no_ai: bool = False

    @model_validator(mode="after")
    def _validate_source(self) -> "AuditRequest":
        if self.use_demo and self.source_path:
            raise ValueError("Provide source_path or use_demo, not both")
        if not self.use_demo and not self.source_path:
            raise ValueError("Provide either source_path or set use_demo=true")
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
    error_message: Optional[str] = None


class ArtifactInfo(BaseModel):
    name: str
    path: str
    size_bytes: int


class ArtifactsResponse(BaseModel):
    job_id: str
    artifacts: list[ArtifactInfo]


class HealthResponse(BaseModel):
    status: str
    queue_depth: int
    version: str = "0.1.0"
