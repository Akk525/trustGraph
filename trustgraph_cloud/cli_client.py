from __future__ import annotations

from pathlib import Path
from typing import Optional

import httpx


class CloudAPIError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HTTP {status_code}: {detail}")


def _raise_for_error(resp: httpx.Response) -> None:
    if resp.is_error:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        raise CloudAPIError(resp.status_code, str(detail))


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def login(api_url: str, email: str, password: str) -> dict:
    with httpx.Client(base_url=api_url.rstrip("/"), timeout=15.0) as c:
        resp = c.post("/auth/login", json={"email": email, "password": password})
    _raise_for_error(resp)
    return resp.json()


def create_api_key(api_url: str, token: str, name: str) -> dict:
    with httpx.Client(
        base_url=api_url.rstrip("/"),
        headers=_auth_headers(token),
        timeout=15.0,
    ) as c:
        resp = c.post("/api-keys", json={"name": name})
    _raise_for_error(resp)
    return resp.json()


def presigned_upload(api_url: str, token: str, filename: str) -> dict:
    with httpx.Client(
        base_url=api_url.rstrip("/"),
        headers=_auth_headers(token),
        timeout=15.0,
    ) as c:
        resp = c.post(
            "/uploads/presigned",
            json={"filename": filename, "content_type": "application/zip"},
        )
    _raise_for_error(resp)
    return resp.json()


def upload_to_s3(upload_url: str, data: bytes) -> None:
    with httpx.Client(timeout=120.0) as c:
        resp = c.put(
            upload_url,
            content=data,
            headers={"Content-Type": "application/zip"},
        )
    if resp.is_error:
        raise CloudAPIError(resp.status_code, f"S3 upload failed: {resp.text[:200]}")


def submit_audit(api_url: str, token: str, input_s3_key: str) -> dict:
    with httpx.Client(
        base_url=api_url.rstrip("/"),
        headers=_auth_headers(token),
        timeout=15.0,
    ) as c:
        resp = c.post("/audits", json={"input_s3_key": input_s3_key})
    _raise_for_error(resp)
    return resp.json()


def get_job(api_url: str, token: str, job_id: str) -> dict:
    with httpx.Client(
        base_url=api_url.rstrip("/"),
        headers=_auth_headers(token),
        timeout=15.0,
    ) as c:
        resp = c.get(f"/audits/{job_id}")
    _raise_for_error(resp)
    return resp.json()


def list_jobs(
    api_url: str,
    token: str,
    limit: int = 20,
    cursor: Optional[str] = None,
    offset: Optional[int] = None,
) -> dict:
    params: dict = {"limit": limit}
    if cursor is not None:
        params["cursor"] = cursor
    if offset is not None:
        params["offset"] = offset
    with httpx.Client(
        base_url=api_url.rstrip("/"),
        headers=_auth_headers(token),
        timeout=15.0,
    ) as c:
        resp = c.get("/audits", params=params)
    _raise_for_error(resp)
    return resp.json()


def list_artifacts(api_url: str, token: str, job_id: str) -> dict:
    with httpx.Client(
        base_url=api_url.rstrip("/"),
        headers=_auth_headers(token),
        timeout=15.0,
    ) as c:
        resp = c.get(f"/audits/{job_id}/artifacts")
    _raise_for_error(resp)
    return resp.json()


def download_artifact(url: str) -> bytes:
    with httpx.Client(timeout=60.0) as c:
        resp = c.get(url)
    if resp.is_error:
        raise CloudAPIError(resp.status_code, f"Download failed: {resp.text[:200]}")
    return resp.content
