from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    base_workspace: Path = Path(".trustgraph-cloud")
    max_concurrent_workers: int = 1
    log_level: str = "INFO"

    # Execution mode: "local_host" (Phase 1, trusted dev) | "docker" (Phase 1.5+, isolation)
    execution_mode: str = "local_host"

    # Docker runner settings (used when execution_mode="docker")
    docker_image: str = "trustgraph-worker:latest"
    docker_memory_limit: str = "512m"
    docker_cpu_limit: str = "0.5"
    docker_timeout_seconds: int = 300
    docker_disable_network: bool = True

    # Job queue backend: "local" | "sqs"  (env: TRUSTGRAPH_JOB_QUEUE)
    job_queue: str = "local"

    # SQS job queue settings (used when job_queue="sqs")
    sqs_queue_url: str = ""
    sqs_region: str = "us-east-1"
    sqs_visibility_timeout_seconds: int = 300   # must exceed max audit duration
    sqs_wait_time_seconds: int = 20             # long-poll window (0–20 seconds)
    sqs_max_messages: int = 1                   # one job per poll; scale by adding workers

    # Artifact store backend: "local" | "s3"  (env: TRUSTGRAPH_ARTIFACT_STORE)
    artifact_store: str = "local"

    # S3 artifact store settings (used when artifact_store="s3")
    s3_bucket: str = ""
    s3_prefix: str = "trustgraph/jobs"
    aws_region: str = "us-east-1"
    s3_presigned_url_ttl_seconds: int = 3600
    aws_endpoint_url: Optional[str] = None  # LocalStack or custom endpoint

    model_config = {
        "env_prefix": "TRUSTGRAPH_",
        "env_file": ".env",
        "extra": "ignore",
    }

    @property
    def jobs_dir(self) -> Path:
        return self.base_workspace / "jobs"

    def job_workspace(self, job_id: str) -> Path:
        return self.jobs_dir / job_id


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Reset cached settings. Used in tests only."""
    global _settings
    _settings = None
