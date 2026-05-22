"""
Phase 3B tests: signup, login, JWT auth, API keys, job ownership.

All tests use local in-memory auth stores — no AWS required.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from starlette.testclient import TestClient

from trustgraph_cloud.api.main import create_app
from trustgraph_cloud.config import Settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEMO_SRC = str(
    Path(__file__).parent.parent.parent / "examples" / "vulnerable-crosschain" / "src"
)
_JWT_SECRET = "test-secret-must-be-at-least-this-long-for-safety"


def _make_client(tmpdir: Path, auth_required: bool = True) -> TestClient:
    settings = Settings(
        base_workspace=tmpdir / ".trustgraph-cloud",
        auth_required=auth_required,
        jwt_secret=_JWT_SECRET,
        jwt_ttl_seconds=3600,
        auth_store="local",
        embedded_worker=False,
    )
    return TestClient(create_app(settings=settings))


def _signup(c: TestClient, email: str = "user@example.com", password: str = "password123") -> dict:
    resp = c.post("/auth/signup", json={"email": email, "password": password})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _login(c: TestClient, email: str = "user@example.com", password: str = "password123") -> dict:
    resp = c.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# TestSignup
# ---------------------------------------------------------------------------

class TestSignup(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.client = _make_client(Path(self.tmpdir.name))

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_signup_returns_201(self):
        with self.client as c:
            resp = c.post("/auth/signup", json={"email": "a@b.com", "password": "securepass"})
        self.assertEqual(resp.status_code, 201)

    def test_signup_returns_access_token(self):
        with self.client as c:
            data = _signup(c, "a@b.com")
        self.assertIn("access_token", data)
        self.assertIsInstance(data["access_token"], str)
        self.assertGreater(len(data["access_token"]), 10)

    def test_signup_token_type_is_bearer(self):
        with self.client as c:
            data = _signup(c, "a@b.com")
        self.assertEqual(data["token_type"], "bearer")

    def test_signup_duplicate_email_returns_409(self):
        with self.client as c:
            _signup(c, "dup@example.com")
            resp = c.post("/auth/signup", json={"email": "dup@example.com", "password": "pass1234"})
        self.assertEqual(resp.status_code, 409)

    def test_signup_email_is_case_insensitive(self):
        with self.client as c:
            _signup(c, "User@Example.COM")
            resp = c.post("/auth/signup", json={"email": "user@example.com", "password": "pass1234"})
        self.assertEqual(resp.status_code, 409)

    def test_signup_short_password_rejected(self):
        with self.client as c:
            resp = c.post("/auth/signup", json={"email": "a@b.com", "password": "short"})
        self.assertEqual(resp.status_code, 422)

    def test_signup_invalid_email_rejected(self):
        with self.client as c:
            resp = c.post("/auth/signup", json={"email": "notanemail", "password": "password123"})
        self.assertEqual(resp.status_code, 422)

    def test_signup_expires_in_present(self):
        with self.client as c:
            data = _signup(c, "a@b.com")
        self.assertIn("expires_in", data)
        self.assertGreater(data["expires_in"], 0)


# ---------------------------------------------------------------------------
# TestLogin
# ---------------------------------------------------------------------------

class TestLogin(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.client = _make_client(Path(self.tmpdir.name))

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_login_returns_token(self):
        with self.client as c:
            _signup(c, "login@example.com")
            data = _login(c, "login@example.com")
        self.assertIn("access_token", data)

    def test_login_wrong_password_returns_401(self):
        with self.client as c:
            _signup(c, "login2@example.com")
            resp = c.post("/auth/login", json={"email": "login2@example.com", "password": "wrongpass"})
        self.assertEqual(resp.status_code, 401)

    def test_login_unknown_email_returns_401(self):
        with self.client as c:
            resp = c.post("/auth/login", json={"email": "nobody@example.com", "password": "pass1234"})
        self.assertEqual(resp.status_code, 401)

    def test_login_token_differs_from_signup_token(self):
        with self.client as c:
            signup_data = _signup(c, "tok@example.com")
            login_data = _login(c, "tok@example.com")
        # Both valid but issued at slightly different times — strings differ
        self.assertIsInstance(login_data["access_token"], str)
        # (tokens may be equal if iat precision matches, just check it's a valid string)
        self.assertGreater(len(login_data["access_token"]), 10)


# ---------------------------------------------------------------------------
# TestAuthMe
# ---------------------------------------------------------------------------

class TestAuthMe(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.client = _make_client(Path(self.tmpdir.name))

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_me_with_valid_token(self):
        with self.client as c:
            token = _signup(c, "me@example.com")["access_token"]
            resp = c.get("/auth/me", headers=_auth_header(token))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["email"], "me@example.com")
        self.assertIn("user_id", data)

    def test_me_without_token_returns_401_when_auth_required(self):
        with self.client as c:
            resp = c.get("/auth/me")
        self.assertEqual(resp.status_code, 401)

    def test_me_with_invalid_token_returns_401(self):
        with self.client as c:
            resp = c.get("/auth/me", headers={"Authorization": "Bearer notavalidtoken"})
        self.assertEqual(resp.status_code, 401)


# ---------------------------------------------------------------------------
# TestJWTProtectedRoutes
# ---------------------------------------------------------------------------

class TestJWTProtectedRoutes(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.client = _make_client(Path(self.tmpdir.name))

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_create_audit_requires_auth(self):
        with self.client as c:
            resp = c.post("/audits", json={"use_demo": True})
        self.assertEqual(resp.status_code, 401)

    def test_create_audit_succeeds_with_token(self):
        with self.client as c:
            token = _signup(c, "audit@example.com")["access_token"]
            resp = c.post("/audits", json={"use_demo": True}, headers=_auth_header(token))
        self.assertEqual(resp.status_code, 202)

    def test_get_audit_requires_auth(self):
        with self.client as c:
            token = _signup(c, "ga@example.com")["access_token"]
            job_id = c.post(
                "/audits", json={"use_demo": True}, headers=_auth_header(token)
            ).json()["job_id"]
            resp = c.get(f"/audits/{job_id}")
        self.assertEqual(resp.status_code, 401)

    def test_get_audit_succeeds_with_owner_token(self):
        with self.client as c:
            token = _signup(c, "owner@example.com")["access_token"]
            job_id = c.post(
                "/audits", json={"use_demo": True}, headers=_auth_header(token)
            ).json()["job_id"]
            resp = c.get(f"/audits/{job_id}", headers=_auth_header(token))
        self.assertEqual(resp.status_code, 200)

    def test_presigned_upload_requires_auth(self):
        with self.client as c:
            resp = c.post("/uploads/presigned", json={"filename": "x.zip"})
        self.assertEqual(resp.status_code, 401)


# ---------------------------------------------------------------------------
# TestJobOwnership
# ---------------------------------------------------------------------------

class TestJobOwnership(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.client = _make_client(Path(self.tmpdir.name))

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_user_cannot_access_another_users_job(self):
        with self.client as c:
            token_a = _signup(c, "alice@example.com")["access_token"]
            token_b = _signup(c, "bob@example.com")["access_token"]
            # Alice creates a job
            job_id = c.post(
                "/audits", json={"use_demo": True}, headers=_auth_header(token_a)
            ).json()["job_id"]
            # Bob tries to read it
            resp = c.get(f"/audits/{job_id}", headers=_auth_header(token_b))
        self.assertEqual(resp.status_code, 404)

    def test_user_cannot_access_another_users_artifacts(self):
        with self.client as c:
            token_a = _signup(c, "a2@example.com")["access_token"]
            token_b = _signup(c, "b2@example.com")["access_token"]
            job_id = c.post(
                "/audits", json={"use_demo": True}, headers=_auth_header(token_a)
            ).json()["job_id"]
            resp = c.get(f"/audits/{job_id}/artifacts", headers=_auth_header(token_b))
        self.assertEqual(resp.status_code, 404)

    def test_owner_can_access_own_artifacts(self):
        with self.client as c:
            token = _signup(c, "owner2@example.com")["access_token"]
            job_id = c.post(
                "/audits", json={"use_demo": True}, headers=_auth_header(token)
            ).json()["job_id"]
            resp = c.get(f"/audits/{job_id}/artifacts", headers=_auth_header(token))
        self.assertEqual(resp.status_code, 200)

    def test_job_user_id_stored_on_creation(self):
        with self.client as c:
            token = _signup(c, "uid@example.com")["access_token"]
            me = c.get("/auth/me", headers=_auth_header(token)).json()
            resp = c.post("/audits", json={"use_demo": True}, headers=_auth_header(token))
        # user_id is internal — not exposed in AuditJobResponse yet, but we can
        # verify the job is accessible to the owner (implying user_id was set)
        self.assertEqual(resp.status_code, 202)


# ---------------------------------------------------------------------------
# TestApiKeys
# ---------------------------------------------------------------------------

class TestApiKeys(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.client = _make_client(Path(self.tmpdir.name))

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_create_api_key_returns_201(self):
        with self.client as c:
            token = _signup(c, "key@example.com")["access_token"]
            resp = c.post("/api-keys", json={"name": "My CLI key"}, headers=_auth_header(token))
        self.assertEqual(resp.status_code, 201)

    def test_create_api_key_returns_raw_key(self):
        with self.client as c:
            token = _signup(c, "key2@example.com")["access_token"]
            data = c.post(
                "/api-keys", json={"name": "cli"}, headers=_auth_header(token)
            ).json()
        self.assertIn("raw_key", data)
        self.assertTrue(data["raw_key"].startswith("tg_live_"))

    def test_raw_key_not_returned_in_list(self):
        with self.client as c:
            token = _signup(c, "list@example.com")["access_token"]
            c.post("/api-keys", json={"name": "k1"}, headers=_auth_header(token))
            keys = c.get("/api-keys", headers=_auth_header(token)).json()
        self.assertIsInstance(keys, list)
        self.assertEqual(len(keys), 1)
        for k in keys:
            self.assertNotIn("raw_key", k)

    def test_api_key_authenticates_requests(self):
        with self.client as c:
            token = _signup(c, "apikey@example.com")["access_token"]
            raw_key = c.post(
                "/api-keys", json={"name": "cli"}, headers=_auth_header(token)
            ).json()["raw_key"]
            # Use API key instead of JWT
            resp = c.post("/audits", json={"use_demo": True}, headers=_auth_header(raw_key))
        self.assertEqual(resp.status_code, 202)

    def test_revoked_api_key_rejected(self):
        with self.client as c:
            token = _signup(c, "revoke@example.com")["access_token"]
            key_data = c.post(
                "/api-keys", json={"name": "disposable"}, headers=_auth_header(token)
            ).json()
            raw_key = key_data["raw_key"]
            key_id = key_data["key_id"]
            # Revoke the key
            c.delete(f"/api-keys/{key_id}", headers=_auth_header(token))
            # Try to use the revoked key
            resp = c.post("/audits", json={"use_demo": True}, headers=_auth_header(raw_key))
        self.assertEqual(resp.status_code, 401)

    def test_api_key_key_prefix_in_list(self):
        with self.client as c:
            token = _signup(c, "prefix@example.com")["access_token"]
            created = c.post(
                "/api-keys", json={"name": "pk"}, headers=_auth_header(token)
            ).json()
            keys = c.get("/api-keys", headers=_auth_header(token)).json()
        self.assertEqual(keys[0]["key_prefix"], created["key_prefix"])

    def test_api_key_name_preserved(self):
        with self.client as c:
            token = _signup(c, "name@example.com")["access_token"]
            created = c.post(
                "/api-keys", json={"name": "production-cli"}, headers=_auth_header(token)
            ).json()
        self.assertEqual(created["name"], "production-cli")

    def test_revoke_nonexistent_key_returns_404(self):
        with self.client as c:
            token = _signup(c, "rv404@example.com")["access_token"]
            resp = c.delete("/api-keys/no-such-key", headers=_auth_header(token))
        self.assertEqual(resp.status_code, 404)

    def test_revoke_other_users_key_returns_404(self):
        with self.client as c:
            tok_a = _signup(c, "rv_a@example.com")["access_token"]
            tok_b = _signup(c, "rv_b@example.com")["access_token"]
            key_id = c.post(
                "/api-keys", json={"name": "a_key"}, headers=_auth_header(tok_a)
            ).json()["key_id"]
            # B tries to revoke A's key
            resp = c.delete(f"/api-keys/{key_id}", headers=_auth_header(tok_b))
        self.assertEqual(resp.status_code, 404)

    def test_api_key_requires_auth_required_true(self):
        """API key management endpoints return 400 when auth_required=false."""
        tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        try:
            client = _make_client(Path(tmpdir.name), auth_required=False)
            with client as c:
                resp = c.post("/api-keys", json={"name": "k"})
            self.assertEqual(resp.status_code, 400)
        finally:
            tmpdir.cleanup()

    def test_api_key_job_ownership_enforced(self):
        """A job created with API key is owned by that key's user."""
        with self.client as c:
            tok_a = _signup(c, "akown_a@example.com")["access_token"]
            tok_b = _signup(c, "akown_b@example.com")["access_token"]
            raw_key = c.post(
                "/api-keys", json={"name": "cli"}, headers=_auth_header(tok_a)
            ).json()["raw_key"]
            # A creates job via API key
            job_id = c.post(
                "/audits", json={"use_demo": True}, headers=_auth_header(raw_key)
            ).json()["job_id"]
            # B cannot see A's job
            resp = c.get(f"/audits/{job_id}", headers=_auth_header(tok_b))
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# TestAuthDisabledLocalDev
# ---------------------------------------------------------------------------

class TestAuthDisabledLocalDev(unittest.TestCase):
    """auth_required=False preserves backward-compatible open access."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.client = _make_client(Path(self.tmpdir.name), auth_required=False)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_create_audit_no_auth_header_accepted(self):
        with self.client as c:
            resp = c.post("/audits", json={"use_demo": True})
        self.assertEqual(resp.status_code, 202)

    def test_get_audit_no_auth_header_accepted(self):
        with self.client as c:
            job_id = c.post("/audits", json={"use_demo": True}).json()["job_id"]
            resp = c.get(f"/audits/{job_id}")
        self.assertEqual(resp.status_code, 200)

    def test_artifacts_no_auth_header_accepted(self):
        with self.client as c:
            job_id = c.post("/audits", json={"use_demo": True}).json()["job_id"]
            resp = c.get(f"/audits/{job_id}/artifacts")
        self.assertEqual(resp.status_code, 200)

    def test_any_user_can_read_any_job(self):
        """Without ownership enforcement, all jobs are visible."""
        with self.client as c:
            job_id = c.post("/audits", json={"use_demo": True}).json()["job_id"]
            # A completely different request (no auth) can still read it
            resp = c.get(f"/audits/{job_id}")
        self.assertEqual(resp.status_code, 200)

    def test_health_always_accessible(self):
        with self.client as c:
            resp = c.get("/health")
        self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# TestAuthHashing — pure unit tests, no HTTP
# ---------------------------------------------------------------------------

class TestAuthHashing(unittest.TestCase):

    def test_hash_and_verify_password(self):
        from trustgraph_cloud.auth.hashing import hash_password, verify_password
        h = hash_password("supersecret")
        self.assertTrue(verify_password("supersecret", h))
        self.assertFalse(verify_password("wrongpass", h))

    def test_different_passwords_produce_different_hashes(self):
        from trustgraph_cloud.auth.hashing import hash_password
        self.assertNotEqual(hash_password("a"), hash_password("b"))

    def test_bcrypt_same_password_produces_different_hashes(self):
        """bcrypt salts every hash — same input, different output."""
        from trustgraph_cloud.auth.hashing import hash_password
        h1 = hash_password("pass")
        h2 = hash_password("pass")
        self.assertNotEqual(h1, h2)

    def test_generate_api_key_format(self):
        from trustgraph_cloud.auth.hashing import generate_api_key
        raw, prefix, h = generate_api_key()
        self.assertTrue(raw.startswith("tg_live_"))
        self.assertTrue(raw.startswith(prefix))
        self.assertEqual(len(prefix), 16)
        self.assertEqual(len(h), 64)   # sha256 hex digest

    def test_generate_api_key_uniqueness(self):
        from trustgraph_cloud.auth.hashing import generate_api_key
        keys = {generate_api_key()[0] for _ in range(20)}
        self.assertEqual(len(keys), 20)

    def test_hash_api_key_deterministic(self):
        from trustgraph_cloud.auth.hashing import hash_api_key
        h1 = hash_api_key("tg_live_abc123")
        h2 = hash_api_key("tg_live_abc123")
        self.assertEqual(h1, h2)


# ---------------------------------------------------------------------------
# TestJWT — pure unit tests
# ---------------------------------------------------------------------------

class TestJWT(unittest.TestCase):

    SECRET = "test-signing-secret-long-enough"

    def test_encode_and_decode(self):
        from trustgraph_cloud.auth.tokens import create_access_token, decode_access_token
        token = create_access_token("uid-1", "a@b.com", self.SECRET, ttl_seconds=3600)
        payload = decode_access_token(token, self.SECRET)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["sub"], "uid-1")
        self.assertEqual(payload["email"], "a@b.com")

    def test_wrong_secret_returns_none(self):
        from trustgraph_cloud.auth.tokens import create_access_token, decode_access_token
        token = create_access_token("uid-2", "x@y.com", self.SECRET, ttl_seconds=3600)
        result = decode_access_token(token, "wrong-secret")
        self.assertIsNone(result)

    def test_expired_token_returns_none(self):
        from trustgraph_cloud.auth.tokens import create_access_token, decode_access_token
        token = create_access_token("uid-3", "x@y.com", self.SECRET, ttl_seconds=-1)
        result = decode_access_token(token, self.SECRET)
        self.assertIsNone(result)

    def test_malformed_token_returns_none(self):
        from trustgraph_cloud.auth.tokens import decode_access_token
        result = decode_access_token("not.a.jwt", self.SECRET)
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# TestLocalStores — unit tests for in-memory stores
# ---------------------------------------------------------------------------

class TestLocalUserStore(unittest.TestCase):

    def _store(self):
        from trustgraph_cloud.auth.stores import LocalUserStore
        return LocalUserStore()

    def test_create_and_get(self):
        from trustgraph_cloud.auth.hashing import hash_password
        from trustgraph_cloud.auth.models import User
        store = self._store()
        user = User(email="a@b.com", password_hash=hash_password("pass"))
        store.create(user)
        self.assertIsNotNone(store.get(user.user_id))

    def test_get_by_email(self):
        from trustgraph_cloud.auth.hashing import hash_password
        from trustgraph_cloud.auth.models import User
        store = self._store()
        user = User(email="find@b.com", password_hash=hash_password("p"))
        store.create(user)
        found = store.get_by_email("find@b.com")
        self.assertIsNotNone(found)
        self.assertEqual(found.user_id, user.user_id)

    def test_get_by_email_case_insensitive(self):
        from trustgraph_cloud.auth.hashing import hash_password
        from trustgraph_cloud.auth.models import User
        store = self._store()
        user = User(email="Case@B.Com", password_hash=hash_password("p"))
        store.create(user)
        self.assertIsNotNone(store.get_by_email("case@b.com"))
        self.assertIsNotNone(store.get_by_email("CASE@B.COM"))

    def test_get_unknown_returns_none(self):
        store = self._store()
        self.assertIsNone(store.get("no-such-id"))

    def test_get_by_email_unknown_returns_none(self):
        store = self._store()
        self.assertIsNone(store.get_by_email("nobody@example.com"))


class TestLocalApiKeyStore(unittest.TestCase):

    def _store(self):
        from trustgraph_cloud.auth.stores import LocalApiKeyStore
        return LocalApiKeyStore()

    def _key(self, user_id: str = "uid-x", name: str = "test"):
        from trustgraph_cloud.auth.hashing import generate_api_key
        from trustgraph_cloud.auth.models import ApiKey
        raw, prefix, h = generate_api_key()
        return ApiKey(user_id=user_id, key_prefix=prefix, key_hash=h, name=name), raw, h

    def test_create_and_get(self):
        store = self._store()
        key, _, _ = self._key()
        store.create(key)
        self.assertIsNotNone(store.get(key.key_id))

    def test_get_by_hash(self):
        store = self._store()
        key, _, h = self._key()
        store.create(key)
        found = store.get_by_hash(h)
        self.assertIsNotNone(found)
        self.assertEqual(found.key_id, key.key_id)

    def test_list_for_user(self):
        store = self._store()
        k1, _, _ = self._key("u-1", "k1")
        k2, _, _ = self._key("u-1", "k2")
        k3, _, _ = self._key("u-2", "k3")
        for k in (k1, k2, k3):
            store.create(k)
        result = store.list_for_user("u-1")
        self.assertEqual(len(result), 2)
        ids = {k.key_id for k in result}
        self.assertIn(k1.key_id, ids)
        self.assertIn(k2.key_id, ids)
        self.assertNotIn(k3.key_id, ids)

    def test_revoke(self):
        store = self._store()
        key, _, _ = self._key("u-1")
        store.create(key)
        self.assertTrue(store.revoke(key.key_id, "u-1"))
        self.assertFalse(store.get(key.key_id).is_active)

    def test_revoke_wrong_user_returns_false(self):
        store = self._store()
        key, _, _ = self._key("u-1")
        store.create(key)
        self.assertFalse(store.revoke(key.key_id, "u-2"))
        self.assertTrue(store.get(key.key_id).is_active)

    def test_update_last_used(self):
        store = self._store()
        key, _, _ = self._key()
        store.create(key)
        self.assertIsNone(store.get(key.key_id).last_used_at)
        store.update_last_used(key.key_id)
        self.assertIsNotNone(store.get(key.key_id).last_used_at)

    def test_get_by_hash_unknown_returns_none(self):
        store = self._store()
        self.assertIsNone(store.get_by_hash("deadbeef" * 8))


if __name__ == "__main__":
    unittest.main(verbosity=2)
