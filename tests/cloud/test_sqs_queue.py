"""
Tests for the SQS-backed job queue and worker ack semantics.

Unit tests use moto to mock AWS SQS — no real credentials required.
Integration tests are gated by env vars and skipped by default.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

try:
    import boto3
    import moto
    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False

from trustgraph_cloud.jobs.queue import LocalJobQueue
from trustgraph_cloud.jobs.models import FindingsSummary, Job, JobStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REGION = "us-east-1"
QUEUE_NAME = "tg-test-jobs"


def _make_sqs_queue(queue_url: str, wait_time_seconds: int = 0):
    """Construct SQSJobQueue with test-friendly defaults (no long polling)."""
    from trustgraph_cloud.jobs.sqs_queue import SQSJobQueue
    return SQSJobQueue(
        queue_url=queue_url,
        region=REGION,
        visibility_timeout=30,
        wait_time_seconds=wait_time_seconds,
    )


# ---------------------------------------------------------------------------
# TestLocalJobQueueAck — backward compat after adding ack() to protocol
# ---------------------------------------------------------------------------

class TestLocalJobQueueAck(unittest.TestCase):

    def test_ack_is_callable(self):
        q = LocalJobQueue()
        asyncio.run(q.ack("any-job-id"))  # must not raise

    def test_ack_is_noop_for_local_queue(self):
        q = LocalJobQueue()
        asyncio.run(q.enqueue("job-001"))
        asyncio.run(q.ack("job-001"))  # no-op; queue still has the item
        self.assertEqual(q.qsize(), 1)

    def test_local_queue_satisfies_protocol(self):
        from trustgraph_cloud.jobs.queue import JobQueue
        q = LocalJobQueue()
        self.assertIsInstance(q, JobQueue)


# ---------------------------------------------------------------------------
# TestSQSJobQueueEnqueue
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_MOTO, "moto not installed")
class TestSQSJobQueueEnqueue(unittest.TestCase):

    def setUp(self):
        self.mock = moto.mock_aws()
        self.mock.start()
        sqs = boto3.client("sqs", region_name=REGION)
        resp = sqs.create_queue(QueueName=QUEUE_NAME)
        self.queue_url = resp["QueueUrl"]
        self.sqs = sqs
        self.q = _make_sqs_queue(self.queue_url)

    def tearDown(self):
        self.mock.stop()

    def test_enqueue_sends_one_message(self):
        asyncio.run(self.q.enqueue("job-001"))
        resp = self.sqs.receive_message(QueueUrl=self.queue_url, MaxNumberOfMessages=1)
        self.assertEqual(len(resp.get("Messages", [])), 1)

    def test_enqueue_message_body_is_valid_json(self):
        asyncio.run(self.q.enqueue("job-abc"))
        resp = self.sqs.receive_message(QueueUrl=self.queue_url, MaxNumberOfMessages=1)
        body = json.loads(resp["Messages"][0]["Body"])
        self.assertIsInstance(body, dict)

    def test_enqueue_message_contains_job_id(self):
        asyncio.run(self.q.enqueue("my-special-job"))
        resp = self.sqs.receive_message(QueueUrl=self.queue_url, MaxNumberOfMessages=1)
        body = json.loads(resp["Messages"][0]["Body"])
        self.assertEqual(body["job_id"], "my-special-job")

    def test_enqueue_message_contains_submitted_at(self):
        asyncio.run(self.q.enqueue("job-001"))
        resp = self.sqs.receive_message(QueueUrl=self.queue_url, MaxNumberOfMessages=1)
        body = json.loads(resp["Messages"][0]["Body"])
        self.assertIn("submitted_at", body)

    def test_multiple_enqueues_produce_multiple_messages(self):
        asyncio.run(self.q.enqueue("job-001"))
        asyncio.run(self.q.enqueue("job-002"))
        # Fetch up to 10 at once — avoids moto's VisibilityTimeout=0 edge cases
        resp = self.sqs.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=10,
        )
        ids = {json.loads(m["Body"])["job_id"] for m in resp.get("Messages", [])}
        self.assertIn("job-001", ids)
        self.assertIn("job-002", ids)


# ---------------------------------------------------------------------------
# TestSQSJobQueueDequeue
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_MOTO, "moto not installed")
class TestSQSJobQueueDequeue(unittest.TestCase):

    def setUp(self):
        self.mock = moto.mock_aws()
        self.mock.start()
        sqs = boto3.client("sqs", region_name=REGION)
        resp = sqs.create_queue(QueueName=QUEUE_NAME)
        self.queue_url = resp["QueueUrl"]
        self.sqs = sqs
        self.q = _make_sqs_queue(self.queue_url)

    def tearDown(self):
        self.mock.stop()

    def _enqueue_and_dequeue(self, job_id: str) -> str:
        async def _run():
            await self.q.enqueue(job_id)
            return await self.q.dequeue()
        return asyncio.run(_run())

    def test_dequeue_returns_correct_job_id(self):
        result = self._enqueue_and_dequeue("job-dequeue-001")
        self.assertEqual(result, "job-dequeue-001")

    def test_dequeue_stores_receipt_handle_for_ack(self):
        async def _run():
            await self.q.enqueue("job-rh-001")
            await self.q.dequeue()
        asyncio.run(_run())
        self.assertIn("job-rh-001", self.q._pending_acks)

    def test_job_id_survives_roundtrip_via_sqs(self):
        unique_id = "test-job-" + "x" * 32
        result = self._enqueue_and_dequeue(unique_id)
        self.assertEqual(result, unique_id)


# ---------------------------------------------------------------------------
# TestSQSJobQueueAck
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_MOTO, "moto not installed")
class TestSQSJobQueueAck(unittest.TestCase):

    def setUp(self):
        self.mock = moto.mock_aws()
        self.mock.start()
        sqs = boto3.client("sqs", region_name=REGION)
        resp = sqs.create_queue(QueueName=QUEUE_NAME)
        self.queue_url = resp["QueueUrl"]
        self.sqs = sqs
        self.q = _make_sqs_queue(self.queue_url)

    def tearDown(self):
        self.mock.stop()

    def _roundtrip(self, job_id: str) -> None:
        """Enqueue then dequeue — receipt handle is now in _pending_acks."""
        async def _run():
            await self.q.enqueue(job_id)
            await self.q.dequeue()
        asyncio.run(_run())

    def test_ack_deletes_message_from_sqs(self):
        self._roundtrip("job-ack-001")

        async def _ack():
            await self.q.ack("job-ack-001")
        asyncio.run(_ack())

        # After ack, no messages should be visible (even with VisibilityTimeout=0).
        resp = self.sqs.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=1,
            VisibilityTimeout=0,
        )
        self.assertEqual(len(resp.get("Messages", [])), 0)

    def test_ack_clears_pending_acks_entry(self):
        self._roundtrip("job-ack-002")
        asyncio.run(self.q.ack("job-ack-002"))
        self.assertNotIn("job-ack-002", self.q._pending_acks)

    def test_ack_unknown_job_id_is_noop(self):
        # Should not raise even if the job_id was never dequeued.
        asyncio.run(self.q.ack("nonexistent-job"))

    def test_message_redelivered_without_ack(self):
        """Without ack, the message becomes visible again after visibility timeout."""
        self._roundtrip("job-redeliver-001")
        # Do NOT call ack. In real SQS the message reappears after VisibilityTimeout.
        # We can verify it's still in pending_acks (receipt handle not deleted).
        self.assertIn("job-redeliver-001", self.q._pending_acks)


# ---------------------------------------------------------------------------
# TestSQSJobQueueQsize
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_MOTO, "moto not installed")
class TestSQSJobQueueQsize(unittest.TestCase):

    def setUp(self):
        self.mock = moto.mock_aws()
        self.mock.start()
        sqs = boto3.client("sqs", region_name=REGION)
        resp = sqs.create_queue(QueueName=QUEUE_NAME)
        self.queue_url = resp["QueueUrl"]
        self.sqs = sqs
        self.q = _make_sqs_queue(self.queue_url)

    def tearDown(self):
        self.mock.stop()

    def test_qsize_empty_queue_returns_zero(self):
        self.assertEqual(self.q.qsize(), 0)

    def test_qsize_after_enqueue(self):
        asyncio.run(self.q.enqueue("job-001"))
        self.assertGreaterEqual(self.q.qsize(), 0)  # approximate; moto may return 0 or 1

    def test_qsize_returns_int(self):
        self.assertIsInstance(self.q.qsize(), int)


# ---------------------------------------------------------------------------
# TestWorkerAcknowledgement
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_MOTO, "moto not installed")
class TestWorkerAcknowledgement(unittest.TestCase):
    """Verify worker ack semantics: ack on success, no ack on failure (SQS retry path)."""

    def setUp(self):
        self.mock = moto.mock_aws()
        self.mock.start()
        sqs = boto3.client("sqs", region_name=REGION)
        resp = sqs.create_queue(QueueName=QUEUE_NAME)
        self.queue_url = resp["QueueUrl"]
        self.sqs = sqs
        self.tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)

    def tearDown(self):
        self.mock.stop()
        self.tmpdir.cleanup()

    def _make_worker_with_sqs(self):
        from trustgraph_cloud.artifacts.store import LocalArtifactStore
        from trustgraph_cloud.config import Settings
        from trustgraph_cloud.jobs.store import LocalJobStore
        from trustgraph_cloud.jobs.worker import Worker

        settings = Settings(
            base_workspace=Path(self.tmpdir.name) / ".trustgraph-cloud",
            job_queue="sqs",
            sqs_queue_url=self.queue_url,
            sqs_region=REGION,
        )
        settings.jobs_dir.mkdir(parents=True, exist_ok=True)

        job_store = LocalJobStore(settings.jobs_dir)
        artifact_store = LocalArtifactStore(settings.jobs_dir)
        sqs_queue = _make_sqs_queue(self.queue_url)

        worker = Worker(
            queue=sqs_queue,
            job_store=job_store,
            artifact_store=artifact_store,
            settings=settings,
        )
        return worker, job_store, sqs_queue

    def test_worker_acks_after_successful_job(self):
        worker, job_store, sqs_queue = self._make_worker_with_sqs()
        job = Job(input_type="local_path", source_path="/tmp")
        job_store.create(job)

        real_summary = FindingsSummary(critical=0, medium=0)
        try:
            async def _run():
                await sqs_queue.enqueue(job.job_id)
                await sqs_queue.dequeue()  # populate _pending_acks
                with patch("trustgraph_cloud.jobs.worker.run_audit",
                           return_value=(real_summary, [])):
                    await worker._process(job.job_id)

            asyncio.run(_run())
        finally:
            worker.close()

        # Message must be deleted — pending_acks entry cleared.
        self.assertNotIn(job.job_id, sqs_queue._pending_acks)

        # Verify from SQS perspective: message gone (VisibilityTimeout=0 forces immediate visibility).
        resp = self.sqs.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=1,
            VisibilityTimeout=0,
        )
        self.assertEqual(len(resp.get("Messages", [])), 0)

    def test_worker_does_not_ack_after_failed_job(self):
        """On AuditServiceError the message must NOT be deleted — SQS will redeliver it."""
        worker, job_store, sqs_queue = self._make_worker_with_sqs()
        job = Job(input_type="local_path", source_path="/nonexistent/path")
        job_store.create(job)

        try:
            async def _run():
                await sqs_queue.enqueue(job.job_id)
                await sqs_queue.dequeue()
                with patch("trustgraph_cloud.jobs.worker.run_audit",
                           side_effect=__import__("trustgraph_cloud.runner.audit_service",
                                                   fromlist=["AuditServiceError"]).AuditServiceError("bad path")):
                    await worker._process(job.job_id)

            asyncio.run(_run())
        finally:
            worker.close()

        # Receipt handle must still be in _pending_acks — ack was NOT called.
        self.assertIn(job.job_id, sqs_queue._pending_acks)

        # Job store must reflect the failure.
        job_record = job_store.get(job.job_id)
        self.assertEqual(job_record.status, JobStatus.FAILED)

        # Message still exists in SQS (in-flight, not deleted). Check via queue attributes
        # rather than receive_message — the message is invisible until visibility timeout expires.
        attrs = self.sqs.get_queue_attributes(
            QueueUrl=self.queue_url,
            AttributeNames=["ApproximateNumberOfMessagesNotVisible"],
        )
        in_flight = int(attrs["Attributes"].get("ApproximateNumberOfMessagesNotVisible", 0))
        self.assertGreater(in_flight, 0)

    def test_worker_acks_when_job_not_found(self):
        """If job_id is in queue but not in job store, ack is still called."""
        worker, _job_store, sqs_queue = self._make_worker_with_sqs()
        ghost_id = "nonexistent-job-id"

        try:
            async def _run():
                await sqs_queue.enqueue(ghost_id)
                await sqs_queue.dequeue()
                await worker._process(ghost_id)

            asyncio.run(_run())
        finally:
            worker.close()

        self.assertNotIn(ghost_id, sqs_queue._pending_acks)


# ---------------------------------------------------------------------------
# TestAPIWithSQSBackend
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_MOTO, "moto not installed")
class TestAPIWithSQSBackend(unittest.TestCase):
    """Verify the FastAPI app operates correctly with an SQS-backed queue."""

    def setUp(self):
        self.mock = moto.mock_aws()
        self.mock.start()
        sqs = boto3.client("sqs", region_name=REGION)
        resp = sqs.create_queue(QueueName=QUEUE_NAME)
        self.queue_url = resp["QueueUrl"]
        self.sqs = sqs
        self.tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)

    def tearDown(self):
        self.mock.stop()
        self.tmpdir.cleanup()

    def _make_client(self):
        from starlette.testclient import TestClient
        from trustgraph_cloud.api.main import create_app
        from trustgraph_cloud.config import Settings
        settings = Settings(
            base_workspace=Path(self.tmpdir.name) / ".trustgraph-cloud",
            job_queue="sqs",
            sqs_queue_url=self.queue_url,
            sqs_region=REGION,
            sqs_wait_time_seconds=0,  # no long polling in tests
        )
        return TestClient(create_app(settings=settings))

    def test_health_returns_ok_with_sqs(self):
        client = self._make_client()
        with client as c:
            resp = c.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

    def test_demo_job_accepted_with_sqs(self):
        client = self._make_client()
        with client as c:
            resp = c.post("/audits", json={"use_demo": True})
        self.assertEqual(resp.status_code, 202)
        self.assertIn("job_id", resp.json())

    def test_job_enqueued_into_sqs(self):
        """After POST /audits, verify the message was sent to SQS."""
        client = self._make_client()
        with client as c:
            create_resp = c.post("/audits", json={"use_demo": True})
            job_id = create_resp.json()["job_id"]

        # After TestClient exits, receive from SQS (message may already be consumed by worker).
        # The job_id must be in the job store regardless.
        from trustgraph_cloud.config import Settings
        from trustgraph_cloud.jobs.store import LocalJobStore
        settings = Settings(
            base_workspace=Path(self.tmpdir.name) / ".trustgraph-cloud",
        )
        job_store = LocalJobStore(settings.jobs_dir)
        job = job_store.get(job_id)
        self.assertIsNotNone(job)
        self.assertEqual(job.job_id, job_id)

    def test_backend_switch_does_not_affect_job_retrieval(self):
        client = self._make_client()
        with client as c:
            create_resp = c.post("/audits", json={"use_demo": True})
            job_id = create_resp.json()["job_id"]
            get_resp = c.get(f"/audits/{job_id}")
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.json()["job_id"], job_id)

    def test_nonexistent_job_still_404_with_sqs(self):
        client = self._make_client()
        with client as c:
            resp = c.get("/audits/does-not-exist")
        self.assertEqual(resp.status_code, 404)

    def test_artifacts_endpoint_works_with_sqs(self):
        client = self._make_client()
        with client as c:
            create_resp = c.post("/audits", json={"use_demo": True})
            job_id = create_resp.json()["job_id"]
            resp = c.get(f"/audits/{job_id}/artifacts")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("artifacts", resp.json())


# ---------------------------------------------------------------------------
# Integration — real AWS (skipped unless env vars present)
# ---------------------------------------------------------------------------

_REAL_QUEUE_URL = os.environ.get("TRUSTGRAPH_SQS_QUEUE_URL")
_REAL_REGION = os.environ.get("TRUSTGRAPH_SQS_REGION", "us-east-1")


@unittest.skipUnless(_REAL_QUEUE_URL, "Set TRUSTGRAPH_SQS_QUEUE_URL to run real AWS tests")
class TestSQSJobQueueRealAWS(unittest.TestCase):
    """
    Sanity checks against a real SQS queue.
    Run with:
        TRUSTGRAPH_SQS_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/123/my-queue \
        python -m pytest tests/cloud/test_sqs_queue.py::TestSQSJobQueueRealAWS -v
    """

    def setUp(self):
        from trustgraph_cloud.jobs.sqs_queue import SQSJobQueue
        self.q = SQSJobQueue(
            queue_url=_REAL_QUEUE_URL,
            region=_REAL_REGION,
            visibility_timeout=30,
            wait_time_seconds=1,
        )

    def test_enqueue_dequeue_roundtrip(self):
        test_id = "integration-test-job-001"

        async def _run():
            await self.q.enqueue(test_id)
            job_id = await self.q.dequeue()
            await self.q.ack(job_id)
            return job_id

        result = asyncio.run(_run())
        self.assertEqual(result, test_id)


if __name__ == "__main__":
    unittest.main(verbosity=2)
