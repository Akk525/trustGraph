from __future__ import annotations

import hashlib
import secrets

from passlib.context import CryptContext

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


def generate_api_key() -> tuple[str, str, str]:
    """
    Generate a new API key.

    Returns (raw_key, key_prefix, key_hash):
    - raw_key     — the full key returned to the user once; never stored
    - key_prefix  — first 16 chars of raw_key, stored for display
    - key_hash    — sha256(raw_key), stored for verification
    """
    random_part = secrets.token_hex(32)   # 64 hex chars
    raw_key = f"tg_live_{random_part}"    # 72 chars total
    key_prefix = raw_key[:16]             # "tg_live_XXXXXXXX"
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    return raw_key, key_prefix, key_hash


def hash_api_key(raw_key: str) -> str:
    """sha256 hash of a raw API key string."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
