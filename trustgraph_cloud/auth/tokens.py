from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt

_ALGORITHM = "HS256"


def create_access_token(
    user_id: str,
    email: str,
    secret: str,
    ttl_seconds: int,
) -> str:
    now = datetime.now(tz=timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "iat": now,
        "exp": now + timedelta(seconds=ttl_seconds),
    }
    return jwt.encode(payload, secret, algorithm=_ALGORITHM)


def decode_access_token(token: str, secret: str) -> Optional[dict]:
    """
    Decode and verify a JWT. Returns the payload dict, or None on any failure
    (expired, bad signature, malformed).
    """
    try:
        return jwt.decode(token, secret, algorithms=[_ALGORITHM])
    except JWTError:
        return None
