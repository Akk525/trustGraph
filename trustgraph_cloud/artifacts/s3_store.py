from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from trustgraph_cloud.artifacts.store import Artifact, ArtifactStore

_DEFAULT_CONTENT_TYPE = "application/octet-stream"

# Explicit mapping for types that mimetypes.guess_type misses on some platforms.
_KNOWN_TYPES: dict[str, str] = {
    ".json": "application/json",
    ".md": "text/markdown",
    ".sol": "text/plain",
    ".log": "text/plain",
}


def _content_type(artifact_name: str) -> str:
    suffix = Path(artifact_name).suffix.lower()
    if suffix in _KNOWN_TYPES:
        return _KNOWN_TYPES[suffix]
    ct, _ = mimetypes.guess_type(artifact_name)
    return ct or _DEFAULT_CONTENT_TYPE


class S3ArtifactStore:
    """
    S3-backed artifact store. Implements the ArtifactStore protocol.

    Artifacts are uploaded under:
        s3://{bucket}/{prefix}/{job_id}/{artifact_name}

    All retrieval operations return presigned URLs so the bucket stays private.

    Phase 2B migration: this is a drop-in replacement for LocalArtifactStore —
    the Worker, routes, and deps do not change.
    """

    def __init__(
        self,
        bucket: str,
        prefix: str,
        region: str,
        presigned_url_ttl: int = 3600,
        endpoint_url: Optional[str] = None,
    ) -> None:
        self._bucket = bucket
        self._prefix = prefix.rstrip("/")
        self._presigned_url_ttl = presigned_url_ttl
        # boto3.session.Session() is intercepted by moto in tests.
        self._client = boto3.session.Session().client(
            "s3",
            region_name=region,
            endpoint_url=endpoint_url or None,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _key(self, job_id: str, artifact_name: str) -> str:
        return f"{self._prefix}/{job_id}/{artifact_name}"

    def _presigned_url(self, key: str) -> Optional[str]:
        if self._presigned_url_ttl <= 0:
            return None
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=self._presigned_url_ttl,
        )

    # ------------------------------------------------------------------
    # ArtifactStore protocol
    # ------------------------------------------------------------------

    def register(self, job_id: str, source_path: str, artifact_name: str) -> Artifact:
        """Upload a local file to S3 and return an Artifact with its S3 key."""
        src = Path(source_path)
        key = self._key(job_id, artifact_name)
        ct = _content_type(artifact_name)
        size_bytes = src.stat().st_size

        self._client.upload_file(
            str(src),
            self._bucket,
            key,
            ExtraArgs={"ContentType": ct},
        )

        return Artifact(
            name=artifact_name,
            path="",
            size_bytes=size_bytes,
            storage_backend="s3",
            s3_key=key,
            presigned_url=self._presigned_url(key),
            content_type=ct,
        )

    def list(self, job_id: str) -> list[Artifact]:
        """List all artifacts for a job by paginating the S3 prefix."""
        prefix = f"{self._prefix}/{job_id}/"
        paginator = self._client.get_paginator("list_objects_v2")
        artifacts: list[Artifact] = []

        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key: str = obj["Key"]
                relative = key[len(prefix):]
                if not relative:
                    continue
                artifacts.append(
                    Artifact(
                        name=relative,
                        path="",
                        size_bytes=obj["Size"],
                        storage_backend="s3",
                        s3_key=key,
                        presigned_url=self._presigned_url(key),
                    )
                )

        return artifacts

    def get(self, job_id: str, artifact_name: str) -> Optional[Artifact]:
        """Return metadata for a specific artifact, or None if it doesn't exist."""
        key = self._key(job_id, artifact_name)
        try:
            resp = self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code in ("404", "NoSuchKey"):
                return None
            raise
        return Artifact(
            name=artifact_name,
            path="",
            size_bytes=resp["ContentLength"],
            storage_backend="s3",
            s3_key=key,
            presigned_url=self._presigned_url(key),
            content_type=resp.get("ContentType"),
        )
