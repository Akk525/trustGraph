from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from trustgraph_cloud.artifacts.store import ArtifactStore
from trustgraph_cloud.config import Settings
from trustgraph_cloud.jobs.models import JobStatus
from trustgraph_cloud.jobs.queue import JobQueue
from trustgraph_cloud.jobs.store import JobStore
from trustgraph_cloud.logging import logger
from trustgraph_cloud.runner.audit_service import AuditServiceError, run_audit


class Worker:
    """
    Pulls job IDs from the queue and executes the TrustGraph audit in a thread pool.

    Phase 2 migration: replace the LocalJobQueue with an SQS consumer and
    deploy this worker as an ECS Fargate task. The audit execution logic
    (run_audit) is unchanged; only the queue integration changes.
    """

    def __init__(
        self,
        queue: JobQueue,
        job_store: JobStore,
        artifact_store: ArtifactStore,
        settings: Settings,
    ) -> None:
        self._queue = queue
        self._job_store = job_store
        self._artifact_store = artifact_store
        self._settings = settings
        self._executor = ThreadPoolExecutor(
            max_workers=settings.max_concurrent_workers,
            thread_name_prefix="tg-worker",
        )

    def close(self) -> None:
        """Shut down the thread pool executor. Call during lifespan teardown."""
        self._executor.shutdown(wait=True, cancel_futures=False)

    async def run(self) -> None:
        """Main loop — dequeue and dispatch jobs until cancelled."""
        logger.info("worker.started")
        while True:
            job_id = await self._queue.dequeue()
            asyncio.create_task(self._process(job_id))

    async def _process(self, job_id: str) -> None:
        job = self._job_store.get(job_id)
        if job is None:
            logger.error("worker.job_not_found", extra={"job_id": job_id})
            return

        self._job_store.update(
            job_id,
            status=JobStatus.RUNNING,
            started_at=datetime.now(tz=timezone.utc),
        )
        logger.info("worker.job_started", extra={"job_id": job_id})

        workspace = self._settings.job_workspace(job_id)
        loop = asyncio.get_event_loop()

        try:
            summary, artifact_names = await loop.run_in_executor(
                self._executor,
                lambda: run_audit(
                    job_id=job_id,
                    workspace=workspace,
                    input_type=job.input_type,
                    source_path=job.source_path,
                    options=job.options,
                    artifact_store=self._artifact_store,
                ),
            )
            self._job_store.update(
                job_id,
                status=JobStatus.SUCCEEDED,
                completed_at=datetime.now(tz=timezone.utc),
                findings_summary=summary,
                artifact_names=artifact_names,
            )
            logger.info("worker.job_succeeded", extra={"job_id": job_id})

        except AuditServiceError as exc:
            self._job_store.update(
                job_id,
                status=JobStatus.FAILED,
                completed_at=datetime.now(tz=timezone.utc),
                error_message=str(exc),
            )
            logger.error("worker.job_failed", extra={"job_id": job_id, "error": str(exc)})

        except Exception as exc:
            self._job_store.update(
                job_id,
                status=JobStatus.FAILED,
                completed_at=datetime.now(tz=timezone.utc),
                error_message=f"Unexpected worker error: {exc}",
            )
            logger.error("worker.unexpected_error", extra={"job_id": job_id, "error": str(exc)})
