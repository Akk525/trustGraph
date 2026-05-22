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

    # Job store backend: "local" | "dynamodb"  (env: TRUSTGRAPH_JOB_STORE)
    job_store: str = "local"

    # DynamoDB job store settings (used when job_store="dynamodb")
    dynamodb_table: str = "trustgraph-jobs"
    dynamodb_region: str = "us-east-1"

    # Worker-only mode — set true in ECS Fargate worker tasks (no FastAPI)
    worker_only: bool = False

    # Embedded worker — set false when the API runs alongside a separate ECS worker
    # (TRUSTGRAPH_JOB_QUEUE=sqs + ECS Fargate).  Defaults true so local dev is
    # unchanged.  Setting false prevents the API process from also polling SQS.
    embedded_worker: bool = True

    # Explicit path to the bundled demo contracts.  When unset the audit service
    # walks a candidate list (package-relative → /build/examples → /work/examples).
    # Set TRUSTGRAPH_DEMO_SOURCE_PATH=/build/examples/vulnerable-crosschain/src in ECS.
    demo_source_path: Optional[str] = None

    # Phase 3B — Authentication
    # Set auth_required=true in production. False means all endpoints are open
    # (backward-compatible with Phase 1/2 local dev and existing tests).
    auth_required: bool = False
    # Secret used to sign JWTs.  Must be set (and kept stable) in production so
    # tokens survive API restarts.  When empty, a random secret is generated at
    # startup — only suitable for dev/testing with auth_required=true.
    jwt_secret: str = ""
    jwt_ttl_seconds: int = 86400   # 24 h
    # Auth store backend: "local" (in-memory, dev/tests) | "dynamodb" (production)
    auth_store: str = "local"
    users_table: str = "trustgraph-users"
    api_keys_table: str = "trustgraph-api-keys"

    # Phase 3A — S3 input uploads
    # S3 key prefix for user-uploaded project ZIPs.  Must be different from s3_prefix
    # (artifact store) so IAM policies and lifecycle rules can target each independently.
    input_s3_prefix: str = "trustgraph/inputs"
    # TTL for presigned PUT URLs (seconds).  900 s = 15 min — generous for large ZIPs.
    upload_url_ttl_seconds: int = 900
    # Hard limits applied by safe_extract() before the worker touches the archive.
    max_zip_files: int = 1000
    max_zip_bytes: int = 50_000_000  # 50 MB uncompressed

    # Phase 3C — Per-user quotas and in-process rate limiting
    max_audits_per_day: int = 20    # total audits a user may create in a UTC day
    max_active_jobs: int = 3        # max concurrent queued+running jobs per user
    # Requests per minute to allow from a single IP on auth endpoints.
    # Phase 4: move to Redis, API Gateway usage plans, or AWS WAF rate rules.
    auth_rate_limit_per_minute: int = 10

    # Phase 5C — CORS
    # Comma-separated list of allowed browser origins. Empty = CORS middleware
    # is not added at all (safe default for API-only / backend deployments).
    # Example: "https://myapp.vercel.app,http://localhost:3000"
    cors_origins: str = ""
    # Allow credentials (cookies). Keep False for Bearer-token auth — the
    # Authorization header is always allowed when cors_origins is configured.
    cors_allow_credentials: bool = False

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
