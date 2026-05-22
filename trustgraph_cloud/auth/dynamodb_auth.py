from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import boto3

from trustgraph_cloud.auth.models import ApiKey, User

# GSI names — must match the CDK stack definitions.
_EMAIL_INDEX = "email-index"
_KEY_HASH_INDEX = "key_hash-index"
_API_KEY_USER_INDEX = "user_id-created_at-index"


def _user_item(user: User) -> dict:
    """Build the DynamoDB item for a user, including the email GSI attribute."""
    return {
        "user_id": {"S": user.user_id},
        "data":    {"S": user.model_dump_json()},
        # GSI projection attribute for email-index.
        # Stored lowercase so the index query is case-insensitive.
        "email":   {"S": user.email.lower()},
    }


def _key_item(key: ApiKey) -> dict:
    """Build the DynamoDB item for an API key, including GSI projection attributes."""
    return {
        "key_id":     {"S": key.key_id},
        "data":       {"S": key.model_dump_json()},
        # GSI: key_hash-index — direct hash lookup at auth time.
        "key_hash":   {"S": key.key_hash},
        # GSI: user_id-created_at-index — list keys for a user, oldest first.
        "user_id":    {"S": key.user_id},
        "created_at": {"S": key.created_at.isoformat()},
    }


class DynamoDBUserStore:
    """
    DynamoDB-backed user store.

    Table schema:
        PK: user_id (String)
        Attribute: data (String — full User JSON blob)

    Phase 4A additions:
        email (String) — indexed by email-index for O(log n) email lookup.
    """

    def __init__(
        self,
        table_name: str,
        region: str = "us-east-1",
        endpoint_url: Optional[str] = None,
    ) -> None:
        self._table = table_name
        self._client = boto3.session.Session().client(
            "dynamodb",
            region_name=region,
            endpoint_url=endpoint_url or None,
        )

    def create(self, user: User) -> User:
        self._client.put_item(
            TableName=self._table,
            Item=_user_item(user),
        )
        return user

    def get(self, user_id: str) -> Optional[User]:
        resp = self._client.get_item(
            TableName=self._table,
            Key={"user_id": {"S": user_id}},
        )
        item = resp.get("Item")
        return User.model_validate_json(item["data"]["S"]) if item else None

    def get_by_email(self, email: str) -> Optional[User]:
        """
        Phase 4A: query email-index instead of full-table scan.
        O(log n) versus O(n).
        """
        normalised = email.strip().lower()
        resp = self._client.query(
            TableName=self._table,
            IndexName=_EMAIL_INDEX,
            KeyConditionExpression="#em = :email",
            ExpressionAttributeNames={"#em": "email"},
            ExpressionAttributeValues={":email": {"S": normalised}},
            Limit=1,
        )
        items = resp.get("Items", [])
        if not items:
            return None
        try:
            return User.model_validate_json(items[0]["data"]["S"])
        except Exception:
            return None


class DynamoDBApiKeyStore:
    """
    DynamoDB-backed API key store.

    Table schema:
        PK: key_id (String)
        Attribute: data (String — full ApiKey JSON blob)

    Phase 4A additions:
        key_hash   (String) — indexed by key_hash-index for O(log n) auth lookup.
        user_id    (String) — partition key of user_id-created_at-index.
        created_at (String) — sort key of user_id-created_at-index.
    """

    def __init__(
        self,
        table_name: str,
        region: str = "us-east-1",
        endpoint_url: Optional[str] = None,
    ) -> None:
        self._table = table_name
        self._client = boto3.session.Session().client(
            "dynamodb",
            region_name=region,
            endpoint_url=endpoint_url or None,
        )

    def create(self, key: ApiKey) -> ApiKey:
        self._client.put_item(
            TableName=self._table,
            Item=_key_item(key),
        )
        return key

    def get(self, key_id: str) -> Optional[ApiKey]:
        resp = self._client.get_item(
            TableName=self._table,
            Key={"key_id": {"S": key_id}},
        )
        item = resp.get("Item")
        return ApiKey.model_validate_json(item["data"]["S"]) if item else None

    def get_by_hash(self, key_hash: str) -> Optional[ApiKey]:
        """
        Phase 4A: query key_hash-index instead of full-table scan.
        O(log n) versus O(n) — called on every authenticated API request.
        """
        resp = self._client.query(
            TableName=self._table,
            IndexName=_KEY_HASH_INDEX,
            KeyConditionExpression="key_hash = :h",
            ExpressionAttributeValues={":h": {"S": key_hash}},
            Limit=1,
        )
        items = resp.get("Items", [])
        if not items:
            return None
        try:
            return ApiKey.model_validate_json(items[0]["data"]["S"])
        except Exception:
            return None

    def list_for_user(self, user_id: str) -> list[ApiKey]:
        """
        Phase 4A: query user_id-created_at-index instead of full-table scan.
        Returns keys ordered oldest-first (ScanIndexForward=True).
        """
        keys: list[ApiKey] = []
        kwargs: dict = {
            "TableName": self._table,
            "IndexName": _API_KEY_USER_INDEX,
            "KeyConditionExpression": "user_id = :uid",
            "ExpressionAttributeValues": {":uid": {"S": user_id}},
            "ScanIndexForward": True,  # oldest first (matches original sort order)
        }
        while True:
            resp = self._client.query(**kwargs)
            for item in resp.get("Items", []):
                try:
                    keys.append(ApiKey.model_validate_json(item["data"]["S"]))
                except Exception:
                    pass
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
        return keys

    def revoke(self, key_id: str, user_id: str) -> bool:
        key = self.get(key_id)
        if key is None or key.user_id != user_id:
            return False
        revoked = key.model_copy(update={"revoked_at": datetime.now(tz=timezone.utc)})
        self._client.put_item(
            TableName=self._table,
            Item=_key_item(revoked),
        )
        return True

    def update_last_used(self, key_id: str) -> None:
        key = self.get(key_id)
        if key is None:
            return
        updated = key.model_copy(update={"last_used_at": datetime.now(tz=timezone.utc)})
        self._client.put_item(
            TableName=self._table,
            Item=_key_item(updated),
        )
