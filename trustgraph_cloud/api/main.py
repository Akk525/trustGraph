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
        s.jobs_dir.mkdir(parents=True, exist_ok=True)

        job_store = LocalJobStore(s.jobs_dir)
        artifact_store = LocalArtifactStore(s.jobs_dir)
        job_queue = LocalJobQueue()
        worker = Worker(
            queue=job_queue,
            job_store=job_store,
            artifact_store=artifact_store,
            settings=s,
        )

        app.state.job_store = job_store
        app.state.artifact_store = artifact_store
        app.state.job_queue = job_queue

        worker_task = asyncio.create_task(worker.run())
        logger.info("api.startup", extra={"workspace": str(s.base_workspace)})

        yield

        worker_task.cancel()
        await asyncio.gather(worker_task, return_exceptions=True)
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
