from __future__ import annotations

from datetime import datetime
from typing import Optional

import boto3

from trustgraph_cloud.jobs.models import Job
from trustgraph_cloud.jobs.store import Page, decode_cursor, encode_cursor

# GSI names — must match the CDK stack definitions.
_USER_CREATED_INDEX = "user_id-created_at-index"
_STATUS_CREATED_INDEX = "status-created_at-index"


def _job_item(job: Job) -> dict:
    """
    Build the DynamoDB item for a job, including GSI projection attributes.

    Two GSIs are defined:
      user_id-created_at-index  — queries a single user's jobs ordered by age.
      status-created_at-index   — queries all jobs of a given status (admin/ops).

    The user_id attribute is omitted when job.user_id is None so that
    anonymous (auth_required=False) jobs are excluded from the user GSI
    and do not pollute user-scoped queries.
    """
    item: dict = {
        "job_id":     {"S": job.job_id},
        "data":       {"S": job.model_dump_json()},
        "status":     {"S": job.status.value},
        "created_at": {"S": job.created_at.isoformat()},
    }
    if job.user_id is not None:
        item["user_id"] = {"S": job.user_id}
    return item


class DynamoDBJobStore:
    """
    DynamoDB-backed job store. Implements the JobStore protocol.

    Table schema (single attribute, JSON-blob approach):
        Partition key: job_id  (String)
        Attribute:     data    (String — full Job model as JSON)

    Phase 4A additions — GSI projection attributes written alongside data:
        status     (String) — indexed by status-created_at-index
        created_at (String — ISO 8601) — sort key for both GSIs
        user_id    (String, optional) — partition key for user_id-created_at-index

    Read-modify-write in update() is last-write-wins. Safe for the single-worker
    model; add a conditional expression for optimistic locking if multiple workers
    ever update the same job concurrently.
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
            Item=_job_item(job),
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
        # Use _job_item so GSI attributes (status, created_at) stay in sync
        # when status transitions occur during worker processing.
        self._client.put_item(
            TableName=self._table_name,
            Item=_job_item(updated),
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

    def list_for_user(self, user_id: Optional[str]) -> list[Job]:
        """
        Return all jobs for user_id, newest first.

        Phase 4A: uses user_id-created_at-index (GSI query, O(log n + k))
        instead of a full-table scan.

        When user_id is None (auth_required=False dev mode), anonymous jobs
        have no user_id attribute and are not indexed by the GSI, so we fall
        back to list_all() + filter. This path is dev-only.
        """
        if user_id is None:
            return [j for j in self.list_all() if j.user_id is None]

        jobs: list[Job] = []
        kwargs: dict = {
            "TableName": self._table_name,
            "IndexName": _USER_CREATED_INDEX,
            "KeyConditionExpression": "user_id = :uid",
            "ExpressionAttributeValues": {":uid": {"S": user_id}},
            "ScanIndexForward": False,  # newest first
        }
        while True:
            resp = self._client.query(**kwargs)
            for item in resp.get("Items", []):
                try:
                    jobs.append(Job.model_validate_json(item["data"]["S"]))
                except Exception:
                    pass
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
        return jobs

    def list_for_user_page(
        self,
        user_id: Optional[str],
        limit: int,
        cursor: Optional[str] = None,
    ) -> Page:
        """
        Phase 4C: cursor-based pagination via DynamoDB LastEvaluatedKey.

        For authenticated users, one Query call is issued per page — O(limit)
        items read, no full scan. The cursor encodes the LastEvaluatedKey so
        DynamoDB can resume exactly where it left off.

        Anonymous users fall back to offset-based cursor (dev mode only).
        """
        if user_id is None:
            all_jobs = [j for j in self.list_all() if j.user_id is None]
            all_jobs.sort(key=lambda j: j.created_at, reverse=True)
            offset = decode_cursor(cursor)["offset"] if cursor else 0
            page = all_jobs[offset : offset + limit]
            next_offset = offset + limit
            has_more = next_offset < len(all_jobs)
            nc = encode_cursor({"offset": next_offset}) if has_more else None
            return Page(items=page, next_cursor=nc, has_more=has_more)

        kwargs: dict = {
            "TableName": self._table_name,
            "IndexName": _USER_CREATED_INDEX,
            "KeyConditionExpression": "user_id = :uid",
            "ExpressionAttributeValues": {":uid": {"S": user_id}},
            "ScanIndexForward": False,
            "Limit": limit,
        }
        if cursor is not None:
            kwargs["ExclusiveStartKey"] = decode_cursor(cursor)

        resp = self._client.query(**kwargs)
        items: list[Job] = []
        for item in resp.get("Items", []):
            try:
                items.append(Job.model_validate_json(item["data"]["S"]))
            except Exception:
                pass

        lek = resp.get("LastEvaluatedKey")
        next_cursor = encode_cursor(lek) if lek else None
        return Page(items=items, next_cursor=next_cursor, has_more=lek is not None)

    def count_for_user_since(self, user_id: str, since: datetime) -> int:
        """
        Phase 4B: count jobs for user_id created on or after `since`.

        Uses a KeyConditionExpression range on created_at so DynamoDB reads
        only the relevant partition slice — O(today's jobs) instead of
        O(user's full job history). Select=COUNT avoids transferring item data.
        """
        since_str = since.isoformat()
        total = 0
        kwargs: dict = {
            "TableName": self._table_name,
            "IndexName": _USER_CREATED_INDEX,
            "KeyConditionExpression": "user_id = :uid AND created_at >= :since",
            "ExpressionAttributeValues": {
                ":uid":   {"S": user_id},
                ":since": {"S": since_str},
            },
            "Select": "COUNT",
        }
        while True:
            resp = self._client.query(**kwargs)
            total += resp.get("Count", 0)
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
        return total
