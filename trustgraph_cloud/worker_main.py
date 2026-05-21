"""
Standalone worker entrypoint for ECS Fargate deployment (Phase 2C).

Starts the SQS polling loop without importing FastAPI. The Fargate task
definition overrides the container ENTRYPOINT to run this module.

Usage:
    trustgraph-worker          # via pyproject.toml entry point
    python -m trustgraph_cloud.worker_main

Required env vars:
    TRUSTGRAPH_JOB_QUEUE=sqs
    TRUSTGRAPH_SQS_QUEUE_URL=<url>
    TRUSTGRAPH_ARTIFACT_STORE=s3
    TRUSTGRAPH_S3_BUCKET=<bucket>
    TRUSTGRAPH_JOB_STORE=dynamodb          (recommended; falls back to local)
    TRUSTGRAPH_DYNAMODB_TABLE=trustgraph-jobs
    TRUSTGRAPH_EXECUTION_MODE=local_host   (Fargate task IS the isolation boundary)
"""
from __future__ import annotations

import asyncio
import signal
import sys

from trustgraph_cloud.config import get_settings
from trustgraph_cloud.logging import configure_logging, logger


async def _run() -> None:
    s = get_settings()
    configure_logging(s.log_level)

    # Validate required cloud settings upfront so failures are obvious at startup.
    errors: list[str] = []
    if s.job_queue != "sqs" or not s.sqs_queue_url:
        errors.append("TRUSTGRAPH_JOB_QUEUE=sqs and TRUSTGRAPH_SQS_QUEUE_URL are required")
    if s.artifact_store != "s3" or not s.s3_bucket:
        errors.append("TRUSTGRAPH_ARTIFACT_STORE=s3 and TRUSTGRAPH_S3_BUCKET are required")
    if errors:
        for e in errors:
            logger.error("worker_main.config_error", extra={"error": e})
        sys.exit(1)

    # -- Job queue ----------------------------------------------------------------
    from trustgraph_cloud.jobs.sqs_queue import SQSJobQueue
    job_queue = SQSJobQueue(
        queue_url=s.sqs_queue_url,
        region=s.sqs_region,
        visibility_timeout=s.sqs_visibility_timeout_seconds,
        wait_time_seconds=s.sqs_wait_time_seconds,
        endpoint_url=s.aws_endpoint_url,
    )

    # -- Artifact store -----------------------------------------------------------
    from trustgraph_cloud.artifacts.s3_store import S3ArtifactStore
    artifact_store = S3ArtifactStore(
        bucket=s.s3_bucket,
        prefix=s.s3_prefix,
        region=s.aws_region,
        presigned_url_ttl=s.s3_presigned_url_ttl_seconds,
        endpoint_url=s.aws_endpoint_url,
    )

    # -- Job store ----------------------------------------------------------------
    if s.job_store == "dynamodb":
        from trustgraph_cloud.jobs.dynamodb_store import DynamoDBJobStore
        job_store = DynamoDBJobStore(
            table_name=s.dynamodb_table,
            region=s.dynamodb_region,
            endpoint_url=s.aws_endpoint_url,
        )
        logger.info("worker_main.job_store", extra={"backend": "dynamodb", "table": s.dynamodb_table})
    else:
        # Local fallback — job state is not shared with the API.
        # See docs/phase2c-ecs-fargate.md §Known Limitations.
        from trustgraph_cloud.jobs.store import LocalJobStore
        s.jobs_dir.mkdir(parents=True, exist_ok=True)
        job_store = LocalJobStore(s.jobs_dir)
        logger.warning("worker_main.job_store", extra={
            "backend": "local",
            "warning": "job status not visible to API; set TRUSTGRAPH_JOB_STORE=dynamodb",
        })

    # -- Worker -------------------------------------------------------------------
    from trustgraph_cloud.jobs.worker import Worker
    worker = Worker(
        queue=job_queue,
        job_store=job_store,
        artifact_store=artifact_store,
        settings=s,
    )

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _on_signal() -> None:
        logger.info("worker_main.shutdown_signal")
        stop_event.set()

    loop.add_signal_handler(signal.SIGTERM, _on_signal)
    loop.add_signal_handler(signal.SIGINT, _on_signal)

    logger.info("worker_main.started", extra={
        "queue_url": s.sqs_queue_url,
        "bucket": s.s3_bucket,
        "job_store": s.job_store,
        "execution_mode": s.execution_mode,
    })

    worker_task = asyncio.create_task(worker.run())
    await stop_event.wait()

    logger.info("worker_main.shutting_down")
    worker_task.cancel()
    await asyncio.gather(worker_task, return_exceptions=True)
    worker.close()
    logger.info("worker_main.shutdown_complete")


def main() -> None:
    """Entry point registered in pyproject.toml as `trustgraph-worker`."""
    asyncio.run(_run())


if __name__ == "__main__":
    main()
