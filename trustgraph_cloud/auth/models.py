from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


class User(BaseModel):
    user_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    email: str
    password_hash: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    disabled_at: Optional[datetime] = None

    @property
    def is_active(self) -> bool:
        return self.disabled_at is None


class ApiKey(BaseModel):
    key_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    key_prefix: str   # first 16 chars of raw key — shown to user for identification
    key_hash: str     # sha256(raw_key) — used for constant-time lookup
    name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    last_used_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None
