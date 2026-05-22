from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException, Request

from trustgraph_cloud.jobs.queue import LocalJobQueue
from trustgraph_cloud.jobs.store import LocalJobStore


def get_job_store(request: Request) -> LocalJobStore:
    return request.app.state.job_store  # type: ignore[no-any-return]


def get_artifact_store(request: Request) -> Any:
    return request.app.state.artifact_store


def get_job_queue(request: Request) -> LocalJobQueue:
    return request.app.state.job_queue  # type: ignore[no-any-return]


def get_s3_input_store(request: Request) -> Optional[Any]:
    return getattr(request.app.state, "s3_input_store", None)


# ---------------------------------------------------------------------------
# Phase 3B — Auth dependency
# ---------------------------------------------------------------------------

async def get_current_user(request: Request) -> Optional[Any]:
    """
    Validate the Bearer token and return a User, or None when auth is disabled.

    When TRUSTGRAPH_AUTH_REQUIRED=false (default):
        Always returns None — no credentials required.
        Routes receive user=None and skip ownership checks.

    When TRUSTGRAPH_AUTH_REQUIRED=true:
        Requires a valid "Authorization: Bearer <token>" header.
        Accepts:
            - JWT access tokens issued by POST /auth/login or POST /auth/signup
            - API keys starting with "tg_live_" issued by POST /api-keys
        Returns 401 on missing / expired / revoked / invalid credentials.
    """
    s = request.app.state.settings
    if not s.auth_required:
        return None

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid Authorization header (expected: Bearer <token>)",
        )

    token = auth_header[7:].strip()

    if token.startswith("tg_live_"):
        return await _auth_from_api_key(token, request)
    else:
        return await _auth_from_jwt(token, request)


async def _auth_from_jwt(token: str, request: Request) -> Any:
    from trustgraph_cloud.auth.tokens import decode_access_token

    s = request.app.state.settings
    payload = decode_access_token(token, s.jwt_secret)
    if payload is None:
        raise HTTPException(status_code=401, detail="JWT is invalid or expired")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Malformed JWT: missing sub claim")

    user_store = request.app.state.user_store
    user = user_store.get(user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User account not found or disabled")

    return user


async def _auth_from_api_key(raw_key: str, request: Request) -> Any:
    from trustgraph_cloud.auth.hashing import hash_api_key

    key_hash = hash_api_key(raw_key)
    api_key_store = request.app.state.api_key_store
    key = api_key_store.get_by_hash(key_hash)

    if key is None or not key.is_active:
        raise HTTPException(status_code=401, detail="API key is invalid or revoked")

    user_store = request.app.state.user_store
    user = user_store.get(key.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User account not found or disabled")

    # Fire-and-forget last_used_at update — ignore failures
    try:
        api_key_store.update_last_used(key.key_id)
    except Exception:
        pass

    return user
