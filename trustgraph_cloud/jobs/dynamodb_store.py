from __future__ import annotations

from typing import Optional

import boto3

from trustgraph_cloud.jobs.models import Job


class DynamoDBJobStore:
    """
    DynamoDB-backed job store. Implements the JobStore protocol.

    Table schema (single attribute, JSON-blob approach):
        Partition key: job_id  (String)
        Attribute:     data    (String, full Job model as JSON)

    This avoids per-field attribute mapping and makes schema evolution trivial —
    add fields to Job, no table migration needed.

    Read-modify-write in update() is last-write-wins. For Phase 2C's single-worker
    model this is safe; add a conditional expression for optimistic locking if
    multiple workers ever update the same job concurrently.

    Phase 2D migration: add GSI on status + created_at for efficient list queries.
    """

    def __init__(
        self,
        table_name: str,
        region: str = "us-east-1",
        endpoint_url: Optional[str] = None,
    ) -> None:
        self._table_name = table_name
        # boto3.session.Session() is intercepted by moto in tests.
        self._client = boto3.session.Session().client(
            "dynamodb",
            region_name=region,
            endpoint_url=endpoint_url or None,
        )

    # ------------------------------------------------------------------
    # JobStore protocol
    # ------------------------------------------------------------------

    def create(self, job: Job) -> Job:
        self._client.put_item(
            TableName=self._table_name,
            Item={
                "job_id": {"S": job.job_id},
                "data": {"S": job.model_dump_json()},
            },
        )
        return job

    def get(self, job_id: str) -> Optional[Job]:
        resp = self._client.get_item(
            TableName=self._table_name,
            Key={"job_id": {"S": job_id}},
        )
        item = resp.get("Item")
        if item is None:
            return None
        return Job.model_validate_json(item["data"]["S"])

    def update(self, job_id: str, **fields) -> Optional[Job]:
        job = self.get(job_id)
        if job is None:
            return None
        updated = job.model_copy(update=fields)
        self._client.put_item(
            TableName=self._table_name,
            Item={
                "job_id": {"S": job.job_id},
                "data": {"S": updated.model_dump_json()},
            },
        )
        return updated

    def list_all(self) -> list[Job]:
        paginator = self._client.get_paginator("scan")
        jobs: list[Job] = []
        for page in paginator.paginate(TableName=self._table_name):
            for item in page.get("Items", []):
                try:
                    jobs.append(Job.model_validate_json(item["data"]["S"]))
                except Exception:
                    pass
        return sorted(jobs, key=lambda j: j.created_at)
