from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable


@runtime_checkable
class JobQueue(Protocol):
    async def enqueue(self, job_id: str) -> None: ...
    async def dequeue(self) -> str: ...
    def qsize(self) -> int: ...


class LocalJobQueue:
    """
    In-memory asyncio queue.

    Jobs in-flight are lost if the process restarts — acceptable for Phase 1
    local development. Phase 2 migration: replace with an SQS-backed queue
    implementing the same JobQueue protocol.
    """

    def __init__(self) -> None:
        self._q: asyncio.Queue[str] = asyncio.Queue()

    async def enqueue(self, job_id: str) -> None:
        await self._q.put(job_id)

    async def dequeue(self) -> str:
        return await self._q.get()

    def qsize(self) -> int:
        return self._q.qsize()
