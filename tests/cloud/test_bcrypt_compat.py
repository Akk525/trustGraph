"""
Regression tests for passlib/bcrypt startup compatibility.

Guards against the class of crash where importing auth_routes calls
hash_password() at module level, which fails under bcrypt >= 4.2 with:
  ValueError: password cannot be longer than 72 bytes

These tests verify:
1. auth_routes no longer hashes at import time.
2. /health is reachable even if no auth route has been called.
3. The login timing guard still fires correctly (lazy hash works).
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from starlette.testclient import TestClient

from trustgraph_cloud.api.main import create_app
from trustgraph_cloud.config import Settings

_JWT_SECRET = "test-secret-must-be-at-least-this-long-for-safety"


def _make_client(tmpdir: Path) -> TestClient:
    settings = Settings(
        base_workspace=tmpdir / ".trustgraph-cloud",
        auth_required=True,
        jwt_secret=_JWT_SECRET,
        auth_store="local",
        embedded_worker=False,
    )
    return TestClient(create_app(settings=settings))


# ---------------------------------------------------------------------------
# Import-time safety
# ---------------------------------------------------------------------------

class TestNoImportTimeHashing(unittest.TestCase):

    def test_auth_routes_has_no_module_level_hash_constant(self):
        """_TIMING_GUARD_HASH must not exist as a module-level str constant."""
        from trustgraph_cloud.api import auth_routes
        self.assertFalse(
            hasattr(auth_routes, "_TIMING_GUARD_HASH"),
            "_TIMING_GUARD_HASH is a module-level constant — this calls "
            "hash_password() at import time and crashes under bcrypt >= 4.2",
        )

    def test_auth_routes_has_lazy_timing_guard_function(self):
        """Timing guard must be a callable (lru_cache function), not a str."""
        from trustgraph_cloud.api import auth_routes
        self.assertTrue(callable(auth_routes._timing_guard_hash))

    def test_auth_routes_reimport_does_not_raise(self):
        """Force a clean reimport to confirm no import-time bcrypt call."""
        mod_name = "trustgraph_cloud.api.auth_routes"
        saved = sys.modules.pop(mod_name, None)
        try:
            module = importlib.import_module(mod_name)
            self.assertIsNotNone(module)
        finally:
            if saved is not None:
                sys.modules[mod_name] = saved
            elif mod_name in sys.modules:
                del sys.modules[mod_name]


# ---------------------------------------------------------------------------
# /health always reachable
# ---------------------------------------------------------------------------

class TestHealthAlwaysReachable(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_health_returns_200_before_any_auth_call(self):
        """/health must not depend on auth or bcrypt initialization."""
        client = _make_client(Path(self.tmpdir.name))
        with client as c:
            resp = c.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

    def test_health_returns_200_with_auth_required_true(self):
        """/health is unauthenticated regardless of auth_required setting."""
        client = _make_client(Path(self.tmpdir.name))
        with client as c:
            resp = c.get("/health")
        self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# Timing guard correctness after lazification
# ---------------------------------------------------------------------------

class TestTimingGuardLazy(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.client = _make_client(Path(self.tmpdir.name))

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_login_unknown_email_returns_401_not_crash(self):
        """Lazy timing guard must still fire and return 401, not 500."""
        with self.client as c:
            resp = c.post(
                "/auth/login",
                json={"email": "ghost@example.com", "password": "password123"},
            )
        self.assertEqual(resp.status_code, 401)
        self.assertIn("Invalid email", resp.json()["detail"])

    def test_login_wrong_password_returns_401(self):
        """verify_password path still works after lazy refactor."""
        with self.client as c:
            c.post("/auth/signup", json={"email": "a@test.com", "password": "password123"})
            resp = c.post(
                "/auth/login",
                json={"email": "a@test.com", "password": "wrongpassword"},
            )
        self.assertEqual(resp.status_code, 401)

    def test_login_correct_password_returns_200(self):
        """Happy path login still works after lazy refactor."""
        with self.client as c:
            c.post("/auth/signup", json={"email": "b@test.com", "password": "password123"})
            resp = c.post(
                "/auth/login",
                json={"email": "b@test.com", "password": "password123"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("access_token", resp.json())

    def test_timing_guard_hash_is_cached(self):
        """lru_cache means repeated calls return the same value with no extra misses."""
        from trustgraph_cloud.api.auth_routes import _timing_guard_hash
        misses_before = _timing_guard_hash.cache_info().misses
        h1 = _timing_guard_hash()
        h2 = _timing_guard_hash()
        self.assertEqual(h1, h2)
        # Both calls must be served from cache — no additional miss allowed.
        self.assertEqual(_timing_guard_hash.cache_info().misses, misses_before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
