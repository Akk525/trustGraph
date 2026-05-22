from __future__ import annotations

import uuid
from pathlib import Path
from typing import Optional, Tuple

import boto3


class S3InputStore:
    """
    Manages user-uploaded Solidity project ZIPs in S3.

    Separate from S3ArtifactStore so the input prefix can have its own IAM
    policy and lifecycle rule (short TTL for uploads vs. longer for artifacts).

    API uses generate_upload_url() to hand the client a presigned PUT URL.
    Worker uses download() to fetch the ZIP before extracting.
    """

    def __init__(
        self,
        bucket: str,
        prefix: str,
        region: str,
        upload_ttl: int = 900,
        endpoint_url: Optional[str] = None,
    ) -> None:
        self._bucket = bucket
        self._prefix = prefix.rstrip("/")
        self._upload_ttl = upload_ttl
        self._client = boto3.session.Session().client(
            "s3",
            region_name=region,
            endpoint_url=endpoint_url or None,
        )

    def generate_upload_url(self, filename: str, content_type: str) -> Tuple[str, str]:
        """
        Return (presigned_put_url, s3_key) for a direct client upload.

        The caller should PUT the file body to upload_url with the matching
        Content-Type header. After the PUT succeeds, pass input_s3_key in the
        POST /audits body to create an audit job from the uploaded archive.
        """
        upload_id = str(uuid.uuid4())
        key = f"{self._prefix}/{upload_id}/{filename}"
        url = self._client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self._bucket,
                "Key": key,
                "ContentType": content_type,
            },
            ExpiresIn=self._upload_ttl,
        )
        return url, key

    def download(self, s3_key: str, dest_path: Path) -> None:
        """Download s3_key from the configured bucket to dest_path."""
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        self._client.download_file(self._bucket, s3_key, str(dest_path))
