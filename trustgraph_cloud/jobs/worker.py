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
from trustgraph_cloud.runner.docker_runner import run_audit_in_docker


class Worker:
    """
    Pulls job IDs from the queue and executes TrustGraph audits in a thread pool.

    Execution mode is controlled by Settings.execution_mode:
      - "local_host"  (Phase 1)   — runs the audit in-process on the host.
      - "docker"      (Phase 1.5) — spawns an isolated container per job.

    Queue backend is controlled by Settings.job_queue:
      - "local"  — asyncio.Queue (in-memory, single process)
      - "sqs"    — AWS SQS with visibility timeout and ack semantics

    Phase 2C migration: deploy this worker as an ECS Fargate task. The audit
    execution logic and ack semantics are unchanged; only the deployment unit
    changes (local process → Fargate task).
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
            await self._queue.ack(job_id)
            return

        self._job_store.update(
            job_id,
            status=JobStatus.RUNNING,
            started_at=datetime.now(tz=timezone.utc),
        )
        logger.info("worker.job_started", extra={
            "job_id": job_id,
            "execution_mode": self._settings.execution_mode,
        })

        workspace = self._settings.job_workspace(job_id)
        loop = asyncio.get_event_loop()

        if self._settings.execution_mode == "docker":
            s = self._settings
            fn = lambda: run_audit_in_docker(
                job_id=job_id,
                workspace=workspace,
                input_type=job.input_type,
                source_path=job.source_path,
                options=job.options,
                artifact_store=self._artifact_store,
                image=s.docker_image,
                memory_limit=s.docker_memory_limit,
                cpu_limit=s.docker_cpu_limit,
                timeout_seconds=s.docker_timeout_seconds,
                disable_network=s.docker_disable_network,
            )
        else:
            fn = lambda: run_audit(
                job_id=job_id,
                workspace=workspace,
                input_type=job.input_type,
                source_path=job.source_path,
                options=job.options,
                artifact_store=self._artifact_store,
                demo_source_path=self._settings.demo_source_path,
            )

        try:
            summary, artifact_names = await loop.run_in_executor(self._executor, fn)
            self._job_store.update(
                job_id,
                status=JobStatus.SUCCEEDED,
                completed_at=datetime.now(tz=timezone.utc),
                findings_summary=summary,
                artifact_names=artifact_names,
            )
            logger.info("worker.job_succeeded", extra={"job_id": job_id})
            await self._queue.ack(job_id)
            logger.info("queue.message_acknowledged", extra={"job_id": job_id})

        except AuditServiceError as exc:
            self._job_store.update(
                job_id,
                status=JobStatus.FAILED,
                completed_at=datetime.now(tz=timezone.utc),
                error_message=str(exc),
            )
            if self._queue.supports_retry:
                logger.error("worker.job_failed_retry_pending", extra={"job_id": job_id, "error": str(exc)})
                logger.info("queue.message_not_acknowledged", extra={"job_id": job_id})
            else:
                logger.error("worker.job_failed_no_retry", extra={"job_id": job_id, "error": str(exc)})

        except Exception as exc:
            self._job_store.update(
                job_id,
                status=JobStatus.FAILED,
                completed_at=datetime.now(tz=timezone.utc),
                error_message=f"Unexpected worker error: {exc}",
            )
            if self._queue.supports_retry:
                logger.error("worker.unexpected_error_retry_pending", extra={"job_id": job_id, "error": str(exc)})
                logger.info("queue.message_not_acknowledged", extra={"job_id": job_id})
            else:
                logger.error("worker.unexpected_error", extra={"job_id": job_id, "error": str(exc)})
