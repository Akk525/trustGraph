from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI

from trustgraph_cloud.artifacts.store import LocalArtifactStore
from trustgraph_cloud.config import Settings, get_settings
from trustgraph_cloud.jobs.queue import LocalJobQueue
from trustgraph_cloud.jobs.store import LocalJobStore
from trustgraph_cloud.jobs.worker import Worker
from trustgraph_cloud.logging import configure_logging, logger
from trustgraph_cloud.api.routes import router


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    """
    Factory that creates a configured FastAPI application.

    Pass a Settings instance in tests to control workspace paths and config
    without mutating module-level state.
    """
    _settings = settings  # captured by lifespan closure

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        s = _settings if _settings is not None else get_settings()
        configure_logging(s.log_level)

        # -- Job store ------------------------------------------------------------
        if s.job_store == "dynamodb":
            if not s.dynamodb_table:
                raise RuntimeError(
                    "TRUSTGRAPH_DYNAMODB_TABLE must be set when TRUSTGRAPH_JOB_STORE=dynamodb"
                )
            from trustgraph_cloud.jobs.dynamodb_store import DynamoDBJobStore
            job_store = DynamoDBJobStore(
                table_name=s.dynamodb_table,
                region=s.dynamodb_region,
                endpoint_url=s.aws_endpoint_url,
            )
            logger.info("api.job_store", extra={"backend": "dynamodb", "table": s.dynamodb_table})
        else:
            s.jobs_dir.mkdir(parents=True, exist_ok=True)
            job_store = LocalJobStore(s.jobs_dir)
            logger.info("api.job_store", extra={"backend": "local"})

        # -- Job queue ------------------------------------------------------------
        if s.job_queue == "sqs":
            if not s.sqs_queue_url:
                raise RuntimeError(
                    "TRUSTGRAPH_SQS_QUEUE_URL must be set when TRUSTGRAPH_JOB_QUEUE=sqs"
                )
            from trustgraph_cloud.jobs.sqs_queue import SQSJobQueue
            job_queue = SQSJobQueue(
                queue_url=s.sqs_queue_url,
                region=s.sqs_region,
                visibility_timeout=s.sqs_visibility_timeout_seconds,
                wait_time_seconds=s.sqs_wait_time_seconds,
                endpoint_url=s.aws_endpoint_url,
            )
            logger.info("api.job_queue", extra={"backend": "sqs", "queue_url": s.sqs_queue_url})
        else:
            job_queue = LocalJobQueue()
            logger.info("api.job_queue", extra={"backend": "local"})

        if s.artifact_store == "s3":
            if not s.s3_bucket:
                raise RuntimeError(
                    "TRUSTGRAPH_S3_BUCKET must be set when TRUSTGRAPH_ARTIFACT_STORE=s3"
                )
            from trustgraph_cloud.artifacts.s3_store import S3ArtifactStore
            artifact_store = S3ArtifactStore(
                bucket=s.s3_bucket,
                prefix=s.s3_prefix,
                region=s.aws_region,
                presigned_url_ttl=s.s3_presigned_url_ttl_seconds,
                endpoint_url=s.aws_endpoint_url,
            )
            logger.info("api.artifact_store", extra={"backend": "s3", "bucket": s.s3_bucket})
        else:
            artifact_store = LocalArtifactStore(s.jobs_dir)
            logger.info("api.artifact_store", extra={"backend": "local"})

        app.state.job_store = job_store
        app.state.artifact_store = artifact_store
        app.state.job_queue = job_queue

        # -- Embedded worker (optional) -------------------------------------------
        # Disabled when TRUSTGRAPH_EMBEDDED_WORKER=false — i.e. an ECS Fargate
        # worker is polling the same SQS queue. The API only enqueues and reads
        # status; it does not compete with the dedicated worker for messages.
        if s.embedded_worker:
            worker = Worker(
                queue=job_queue,
                job_store=job_store,
                artifact_store=artifact_store,
                settings=s,
            )
            worker_task = asyncio.create_task(worker.run())
            logger.info("api.startup", extra={"workspace": str(s.base_workspace), "embedded_worker": True})
        else:
            worker = None
            worker_task = None
            logger.info("api.startup", extra={"workspace": str(s.base_workspace), "embedded_worker": False})

        yield

        if worker_task is not None:
            worker_task.cancel()
            await asyncio.gather(worker_task, return_exceptions=True)
        if worker is not None:
            # Wait for any in-flight thread pool jobs to finish before releasing workspace.
            await asyncio.get_event_loop().run_in_executor(None, worker.close)
        logger.info("api.shutdown")

    app = FastAPI(
        title="TrustGraph Cloud API",
        description=(
            "Async audit job API for deterministic Solidity trust-boundary analysis. "
            "Phase 1: local execution. Phase 2: SQS/S3/ECS Fargate."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(router)
    return app


# Module-level app instance for `uvicorn trustgraph_cloud.api.main:app`
app = create_app()
