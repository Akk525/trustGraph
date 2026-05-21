from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable


@runtime_checkable
class JobQueue(Protocol):
    supports_retry: bool  # True if the backend re-delivers unacked messages (SQS); False for local

    async def enqueue(self, job_id: str) -> None: ...
    async def dequeue(self) -> str: ...
    async def ack(self, job_id: str) -> None: ...
    def qsize(self) -> int: ...


class LocalJobQueue:
    """
    In-memory asyncio queue.

    Jobs in-flight are lost if the process restarts — acceptable for Phase 1
    local development. Phase 2B migration: replace with SQSJobQueue (same protocol).

    ack() is a no-op because asyncio.Queue removes the item on get(); there is no
    visibility timeout or redelivery mechanism in the local queue.
    """

    supports_retry: bool = False

    def __init__(self) -> None:
        self._q: asyncio.Queue[str] = asyncio.Queue()

    async def enqueue(self, job_id: str) -> None:
        await self._q.put(job_id)

    async def dequeue(self) -> str:
        return await self._q.get()

    async def ack(self, job_id: str) -> None:
        pass

    def qsize(self) -> int:
        return self._q.qsize()
