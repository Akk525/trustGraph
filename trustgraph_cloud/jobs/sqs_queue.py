from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

import boto3

from trustgraph_cloud.logging import logger


class SQSJobQueue:
    """
    SQS-backed job queue. Implements the JobQueue protocol.

    Message flow:
        enqueue(job_id) → SendMessage        — producer (API handler)
        dequeue()       → ReceiveMessage     — consumer (worker loop)
        ack(job_id)     → DeleteMessage      — called after job completes or fails

    Visibility semantics:
        A received message is hidden from other consumers for
        sqs_visibility_timeout_seconds. If ack() is not called within that window
        (e.g., the worker crashes), SQS re-delivers the message automatically.

        For permanent failures (bad source path etc.), ack() is always called so
        the message is removed. Transient failures that benefit from retry should be
        left unacked; a Dead Letter Queue can capture messages that exceed a
        max-receive-count threshold (configure on the SQS queue, not here).

    Phase 2C migration: this class is unchanged. Only the worker deployment changes
    (local process → ECS Fargate task). The polling loop and ack semantics are
    identical whether the worker runs locally or on Fargate.
    """

    supports_retry: bool = True

    def __init__(
        self,
        queue_url: str,
        region: str,
        visibility_timeout: int = 300,
        wait_time_seconds: int = 20,
        endpoint_url: Optional[str] = None,
    ) -> None:
        self._queue_url = queue_url
        self._visibility_timeout = visibility_timeout
        self._wait_time_seconds = wait_time_seconds
        # boto3.session.Session() is intercepted by moto in tests.
        self._client = boto3.session.Session().client(
            "sqs",
            region_name=region,
            endpoint_url=endpoint_url or None,
        )
        # Maps job_id → receipt_handle for messages currently being processed.
        # Accessed only from the asyncio event loop (dequeue/ack are both async),
        # so no additional locking is required.
        self._pending_acks: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Sync helpers — run inside thread pool executor
    # ------------------------------------------------------------------

    def _send_message(self, job_id: str) -> None:
        payload = json.dumps({
            "job_id": job_id,
            "submitted_at": datetime.now(tz=timezone.utc).isoformat(),
        })
        self._client.send_message(QueueUrl=self._queue_url, MessageBody=payload)

    def _receive_one(self) -> Optional[tuple[str, str]]:
        """
        Long-poll for one message. Blocks for up to wait_time_seconds if the queue
        is empty. Returns (job_id, receipt_handle) or None.
        """
        response = self._client.receive_message(
            QueueUrl=self._queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=self._wait_time_seconds,
            VisibilityTimeout=self._visibility_timeout,
            AttributeNames=["ApproximateReceiveCount"],
        )
        messages = response.get("Messages", [])
        if not messages:
            return None
        msg = messages[0]
        receive_count = int(msg.get("Attributes", {}).get("ApproximateReceiveCount", 1))
        body = json.loads(msg["Body"])
        job_id: str = body["job_id"]
        logger.info("sqs.message_received", extra={
            "job_id": job_id,
            "receive_count": receive_count,
        })
        return job_id, msg["ReceiptHandle"]

    def _delete_message(self, receipt_handle: str) -> None:
        self._client.delete_message(
            QueueUrl=self._queue_url,
            ReceiptHandle=receipt_handle,
        )

    # ------------------------------------------------------------------
    # JobQueue protocol
    # ------------------------------------------------------------------

    async def enqueue(self, job_id: str) -> None:
        """Send a job_id to SQS. Returns immediately after the message is accepted."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._send_message, job_id)

    async def dequeue(self) -> str:
        """
        Long-poll SQS until a message arrives, then return the job_id.

        In real SQS, _receive_one blocks for up to wait_time_seconds per iteration,
        so this loop is efficient. In mocked or zero-wait-time environments, a brief
        cooperative yield prevents tight spinning.
        """
        loop = asyncio.get_event_loop()
        while True:
            result = await loop.run_in_executor(None, self._receive_one)
            if result is not None:
                job_id, receipt_handle = result
                self._pending_acks[job_id] = receipt_handle
                return job_id
            logger.info("sqs.poll_timeout", extra={"queue_url": self._queue_url})
            # Yield to the event loop; in real SQS the long-poll itself provides backpressure.
            await asyncio.sleep(0)

    async def ack(self, job_id: str) -> None:
        """
        Delete the SQS message. Must be called after processing completes
        (success or permanent failure) to prevent re-delivery.
        """
        receipt_handle = self._pending_acks.pop(job_id, None)
        if receipt_handle is None:
            logger.warning("sqs.ack_unknown_job", extra={"job_id": job_id})
            return
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._delete_message, receipt_handle)
        logger.info("sqs.message_deleted", extra={"job_id": job_id})

    def qsize(self) -> int:
        """
        Return the approximate number of messages visible in the queue.

        Uses SQS GetQueueAttributes — eventually consistent and may not reflect
        in-flight messages. Acceptable for health-check reporting.
        """
        try:
            resp = self._client.get_queue_attributes(
                QueueUrl=self._queue_url,
                AttributeNames=["ApproximateNumberOfMessages"],
            )
            return int(resp["Attributes"].get("ApproximateNumberOfMessages", 0))
        except Exception:
            return 0
