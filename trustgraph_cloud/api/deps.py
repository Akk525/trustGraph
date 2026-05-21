from __future__ import annotations

from fastapi import Request

from trustgraph_cloud.artifacts.store import LocalArtifactStore
from trustgraph_cloud.jobs.queue import LocalJobQueue
from trustgraph_cloud.jobs.store import LocalJobStore


def get_job_store(request: Request) -> LocalJobStore:
    return request.app.state.job_store  # type: ignore[no-any-return]


def get_artifact_store(request: Request) -> LocalArtifactStore:
    return request.app.state.artifact_store  # type: ignore[no-any-return]


def get_job_queue(request: Request) -> LocalJobQueue:
    return request.app.state.job_queue  # type: ignore[no-any-return]
