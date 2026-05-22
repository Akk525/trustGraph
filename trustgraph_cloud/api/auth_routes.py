from __future__ import annotations

import functools

from fastapi import APIRouter, Depends, HTTPException, Request

from trustgraph_cloud.api.deps import get_current_user
from trustgraph_cloud.api.schemas import (
    LoginRequest,
    SignupRequest,
    TokenResponse,
    UserResponse,
)
from trustgraph_cloud.auth.hashing import hash_password, verify_password
from trustgraph_cloud.auth.models import User
from trustgraph_cloud.auth.tokens import create_access_token
from trustgraph_cloud.logging import logger

router = APIRouter(prefix="/auth", tags=["auth"])


@functools.lru_cache(maxsize=1)
def _timing_guard_hash() -> str:
    # Computed once on first login attempt, never at import time.
    # Avoids passlib/bcrypt compatibility crashes during module load (e.g.
    # bcrypt >= 4.2 strict-mode errors that would kill /health at startup).
    return hash_password("__timing_guard_placeholder__")


@router.post("/signup", response_model=TokenResponse, status_code=201)
async def signup(body: SignupRequest, request: Request) -> TokenResponse:
    """Register a new account and return a JWT access token."""
    rl = getattr(request.app.state, "rate_limiter", None)
    if rl is not None:
        ip = request.client.host if request.client else "unknown"
        if not rl.check(f"auth.signup:{ip}"):
            raise HTTPException(status_code=429, detail="Too many signup attempts. Try again later.")

    s = request.app.state.settings
    user_store = request.app.state.user_store

    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=422, detail="Invalid email address")
    if len(body.password) < 8:
        raise HTTPException(status_code=422, detail="Password must be at least 8 characters")

    if user_store.get_by_email(email) is not None:
        raise HTTPException(status_code=409, detail="Email already registered")

    user = User(email=email, password_hash=hash_password(body.password))
    user_store.create(user)
    logger.info("auth.signup", extra={"user_id": user.user_id})

    token = create_access_token(
        user_id=user.user_id,
        email=user.email,
        secret=s.jwt_secret,
        ttl_seconds=s.jwt_ttl_seconds,
    )
    return TokenResponse(access_token=token, expires_in=s.jwt_ttl_seconds)


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request) -> TokenResponse:
    """Authenticate with email + password and return a JWT access token."""
    rl = getattr(request.app.state, "rate_limiter", None)
    if rl is not None:
        ip = request.client.host if request.client else "unknown"
        if not rl.check(f"auth.login:{ip}"):
            raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.")

    s = request.app.state.settings
    user_store = request.app.state.user_store

    user = user_store.get_by_email(body.email.strip().lower())
    # Always call verify_password to avoid timing-based user enumeration
    candidate_hash = user.password_hash if user else _timing_guard_hash()

    if not verify_password(body.password, candidate_hash) or user is None:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")

    logger.info("auth.login", extra={"user_id": user.user_id})
    token = create_access_token(
        user_id=user.user_id,
        email=user.email,
        secret=s.jwt_secret,
        ttl_seconds=s.jwt_ttl_seconds,
    )
    return TokenResponse(access_token=token, expires_in=s.jwt_ttl_seconds)


@router.get("/me", response_model=UserResponse)
async def me(
    request: Request,
    current_user=Depends(get_current_user),
) -> UserResponse:
    """Return the authenticated user's profile."""
    s = request.app.state.settings
    if s.auth_required and current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if current_user is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required (set TRUSTGRAPH_AUTH_REQUIRED=true and send a Bearer token)",
        )
    return UserResponse(
        user_id=current_user.user_id,
        email=current_user.email,
        created_at=current_user.created_at,
    )
