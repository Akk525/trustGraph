from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Protocol, runtime_checkable

from trustgraph_cloud.auth.models import ApiKey, User


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------

@runtime_checkable
class UserStore(Protocol):
    def create(self, user: User) -> User: ...
    def get(self, user_id: str) -> Optional[User]: ...
    def get_by_email(self, email: str) -> Optional[User]: ...


@runtime_checkable
class ApiKeyStore(Protocol):
    def create(self, key: ApiKey) -> ApiKey: ...
    def get(self, key_id: str) -> Optional[ApiKey]: ...
    def get_by_hash(self, key_hash: str) -> Optional[ApiKey]: ...
    def list_for_user(self, user_id: str) -> list[ApiKey]: ...
    def revoke(self, key_id: str, user_id: str) -> bool: ...
    def update_last_used(self, key_id: str) -> None: ...


# ---------------------------------------------------------------------------
# In-memory implementations (dev / tests)
# ---------------------------------------------------------------------------

class LocalUserStore:
    """
    Thread-safe in-memory user store for local development and tests.

    Not persistent across restarts.  For production, use DynamoDBUserStore.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, User] = {}
        self._email_index: dict[str, str] = {}   # normalised email → user_id

    def create(self, user: User) -> User:
        self._by_id[user.user_id] = user
        self._email_index[user.email.lower()] = user.user_id
        return user

    def get(self, user_id: str) -> Optional[User]:
        return self._by_id.get(user_id)

    def get_by_email(self, email: str) -> Optional[User]:
        uid = self._email_index.get(email.lower())
        return self._by_id.get(uid) if uid else None


class LocalApiKeyStore:
    """
    Thread-safe in-memory API key store for local development and tests.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, ApiKey] = {}
        self._hash_index: dict[str, str] = {}   # key_hash → key_id

    def create(self, key: ApiKey) -> ApiKey:
        self._by_id[key.key_id] = key
        self._hash_index[key.key_hash] = key.key_id
        return key

    def get(self, key_id: str) -> Optional[ApiKey]:
        return self._by_id.get(key_id)

    def get_by_hash(self, key_hash: str) -> Optional[ApiKey]:
        kid = self._hash_index.get(key_hash)
        return self._by_id.get(kid) if kid else None

    def list_for_user(self, user_id: str) -> list[ApiKey]:
        return sorted(
            [k for k in self._by_id.values() if k.user_id == user_id],
            key=lambda k: k.created_at,
        )

    def revoke(self, key_id: str, user_id: str) -> bool:
        key = self._by_id.get(key_id)
        if key is None or key.user_id != user_id:
            return False
        self._by_id[key_id] = key.model_copy(
            update={"revoked_at": datetime.now(tz=timezone.utc)}
        )
        return True

    def update_last_used(self, key_id: str) -> None:
        key = self._by_id.get(key_id)
        if key is None:
            return
        self._by_id[key_id] = key.model_copy(
            update={"last_used_at": datetime.now(tz=timezone.utc)}
        )
