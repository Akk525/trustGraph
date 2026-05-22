from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from trustgraph_cloud.api.deps import get_current_user
from trustgraph_cloud.api.schemas import (
    ApiKeyCreatedResponse,
    ApiKeyResponse,
    CreateApiKeyRequest,
)
from trustgraph_cloud.auth.hashing import generate_api_key
from trustgraph_cloud.auth.models import ApiKey
from trustgraph_cloud.logging import logger

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


def _require_user(request: Request, current_user):
    """Shared guard: auth must be enabled and user must be authenticated."""
    s = request.app.state.settings
    if not s.auth_required:
        raise HTTPException(
            status_code=400,
            detail="API key management requires TRUSTGRAPH_AUTH_REQUIRED=true",
        )
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return current_user


@router.post("", response_model=ApiKeyCreatedResponse, status_code=201)
async def create_api_key(
    body: CreateApiKeyRequest,
    request: Request,
    current_user=Depends(get_current_user),
) -> ApiKeyCreatedResponse:
    """
    Create a new API key for the authenticated user.

    The raw key is returned exactly once in this response and never stored.
    Save it securely — it cannot be retrieved again.
    """
    user = _require_user(request, current_user)

    rl = getattr(request.app.state, "rate_limiter", None)
    if rl is not None:
        ip = request.client.host if request.client else "unknown"
        if not rl.check(f"api-keys.create:{ip}"):
            raise HTTPException(status_code=429, detail="Too many requests. Try again later.")

    raw_key, key_prefix, key_hash = generate_api_key()
    key = ApiKey(
        user_id=user.user_id,
        key_prefix=key_prefix,
        key_hash=key_hash,
        name=body.name,
    )
    request.app.state.api_key_store.create(key)
    logger.info("api_key.created", extra={"user_id": user.user_id, "key_id": key.key_id})

    return ApiKeyCreatedResponse(
        key_id=key.key_id,
        name=key.name,
        key_prefix=key.key_prefix,
        raw_key=raw_key,   # returned ONCE — not stored
        created_at=key.created_at,
    )


@router.get("", response_model=list[ApiKeyResponse])
async def list_api_keys(
    request: Request,
    current_user=Depends(get_current_user),
) -> list[ApiKeyResponse]:
    """List all API keys for the authenticated user (raw values never returned)."""
    user = _require_user(request, current_user)
    keys = request.app.state.api_key_store.list_for_user(user.user_id)
    return [
        ApiKeyResponse(
            key_id=k.key_id,
            name=k.name,
            key_prefix=k.key_prefix,
            created_at=k.created_at,
            last_used_at=k.last_used_at,
            revoked_at=k.revoked_at,
        )
        for k in keys
    ]


@router.delete("/{key_id}", status_code=204, response_class=Response)
async def revoke_api_key(
    key_id: str,
    request: Request,
    current_user=Depends(get_current_user),
) -> Response:
    """Revoke an API key. The key becomes invalid immediately."""
    user = _require_user(request, current_user)
    revoked = request.app.state.api_key_store.revoke(key_id, user.user_id)
    if not revoked:
        raise HTTPException(status_code=404, detail=f"API key {key_id!r} not found")
    logger.info("api_key.revoked", extra={"user_id": user.user_id, "key_id": key_id})
    return Response(status_code=204)
