"""
Phase 5C tests — CORS middleware configuration and CDK env injection.

CORS tests use FastAPI's TestClient with explicit Origin headers so
starlette's CORSMiddleware logic is exercised end-to-end.

CDK tests are skipped when aws-cdk-lib is not installed.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from trustgraph_cloud.api.main import create_app
from trustgraph_cloud.config import Settings

# ---------------------------------------------------------------------------
# CDK availability guard (same pattern as test_phase2c.py)
# ---------------------------------------------------------------------------
try:
    import aws_cdk as cdk
    from aws_cdk import assertions as cdk_assertions
    _CDK_DIR = Path(__file__).parent.parent.parent / "infra" / "cdk"
    sys.path.insert(0, str(_CDK_DIR))
    from stacks.trustgraph_worker_stack import TrustGraphWorkerStack
    HAS_CDK = True
except ImportError:
    HAS_CDK = False


def _settings(**kwargs) -> Settings:
    """Build a minimal Settings with overrides."""
    defaults = dict(
        base_workspace=Path("/tmp/tg-cors-test"),
        auth_required=False,
        embedded_worker=False,
        job_store="local",
        artifact_store="local",
        job_queue="local",
    )
    defaults.update(kwargs)
    return Settings(**defaults)


# ---------------------------------------------------------------------------
# CORS disabled by default
# ---------------------------------------------------------------------------

class TestCORSDisabledByDefault(unittest.TestCase):

    def setUp(self):
        s = _settings()  # cors_origins="" by default
        app = create_app(s)
        self.client = TestClient(app, raise_server_exceptions=True)

    def test_no_cors_header_for_arbitrary_origin(self):
        """When cors_origins is empty, no Access-Control-Allow-Origin header is sent."""
        with self.client as c:
            resp = c.get(
                "/health",
                headers={"Origin": "http://evil.example.com"},
            )
        self.assertNotIn("access-control-allow-origin", resp.headers)

    def test_options_preflight_returns_method_not_allowed_or_no_cors(self):
        """No CORS middleware → OPTIONS either returns 405 or has no CORS headers."""
        with self.client as c:
            resp = c.options(
                "/health",
                headers={
                    "Origin": "http://evil.example.com",
                    "Access-Control-Request-Method": "GET",
                },
            )
        self.assertNotIn("access-control-allow-origin", resp.headers)

    def test_normal_request_still_works(self):
        """Regular GET requests are unaffected by absent CORS config."""
        with self.client as c:
            resp = c.get("/health")
        self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# CORS enabled for specific origin
# ---------------------------------------------------------------------------

ALLOWED = "https://trustgraph.vercel.app"
OTHER = "https://other.example.com"


class TestCORSAllowedOrigin(unittest.TestCase):

    def setUp(self):
        s = _settings(cors_origins=ALLOWED)
        app = create_app(s)
        self.client = TestClient(app, raise_server_exceptions=True)

    def test_allowed_origin_gets_acao_header(self):
        """Configured origin is reflected in Access-Control-Allow-Origin."""
        with self.client as c:
            resp = c.get(
                "/health",
                headers={"Origin": ALLOWED},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.headers.get("access-control-allow-origin"),
            ALLOWED,
        )

    def test_disallowed_origin_no_acao_header(self):
        """Non-matching origin does not receive an Access-Control-Allow-Origin header."""
        with self.client as c:
            resp = c.get(
                "/health",
                headers={"Origin": OTHER},
            )
        self.assertNotIn("access-control-allow-origin", resp.headers)

    def test_options_preflight_succeeds_for_allowed_origin(self):
        """Preflight OPTIONS returns 200 and includes the requested origin."""
        with self.client as c:
            resp = c.options(
                "/health",
                headers={
                    "Origin": ALLOWED,
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "Authorization, Content-Type",
                },
            )
        self.assertIn(resp.status_code, (200, 204))
        self.assertEqual(
            resp.headers.get("access-control-allow-origin"),
            ALLOWED,
        )

    def test_authorization_header_in_allow_headers(self):
        """Authorization is explicitly allowed so Bearer tokens work from browsers."""
        with self.client as c:
            resp = c.options(
                "/health",
                headers={
                    "Origin": ALLOWED,
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "Authorization",
                },
            )
        allow_headers = resp.headers.get("access-control-allow-headers", "").lower()
        self.assertIn("authorization", allow_headers)

    def test_content_type_header_in_allow_headers(self):
        """Content-Type is allowed so JSON request bodies work from browsers."""
        with self.client as c:
            resp = c.options(
                "/health",
                headers={
                    "Origin": ALLOWED,
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "Content-Type",
                },
            )
        allow_headers = resp.headers.get("access-control-allow-headers", "").lower()
        self.assertIn("content-type", allow_headers)

    def test_delete_in_allowed_methods(self):
        """DELETE method is included so the frontend can revoke API keys."""
        with self.client as c:
            resp = c.options(
                "/health",
                headers={
                    "Origin": ALLOWED,
                    "Access-Control-Request-Method": "DELETE",
                },
            )
        allow_methods = resp.headers.get("access-control-allow-methods", "").upper()
        self.assertIn("DELETE", allow_methods)


# ---------------------------------------------------------------------------
# Multiple origins
# ---------------------------------------------------------------------------

class TestCORSMultipleOrigins(unittest.TestCase):

    def setUp(self):
        s = _settings(
            cors_origins=f"{ALLOWED}, http://localhost:3000",
        )
        app = create_app(s)
        self.client = TestClient(app, raise_server_exceptions=True)

    def test_first_origin_allowed(self):
        with self.client as c:
            resp = c.get("/health", headers={"Origin": ALLOWED})
        self.assertEqual(resp.headers.get("access-control-allow-origin"), ALLOWED)

    def test_second_origin_allowed(self):
        with self.client as c:
            resp = c.get(
                "/health", headers={"Origin": "http://localhost:3000"}
            )
        self.assertEqual(
            resp.headers.get("access-control-allow-origin"),
            "http://localhost:3000",
        )

    def test_third_party_origin_blocked(self):
        with self.client as c:
            resp = c.get("/health", headers={"Origin": OTHER})
        self.assertNotIn("access-control-allow-origin", resp.headers)


# ---------------------------------------------------------------------------
# cors_allow_credentials
# ---------------------------------------------------------------------------

class TestCORSCredentials(unittest.TestCase):

    def test_credentials_false_by_default(self):
        """allow-credentials header is absent when cors_allow_credentials=False."""
        s = _settings(cors_origins=ALLOWED)
        app = create_app(s)
        with TestClient(app) as client:
            resp = client.get("/health", headers={"Origin": ALLOWED})
        # Starlette only sets the header when allow_credentials=True
        self.assertNotEqual(
            resp.headers.get("access-control-allow-credentials", "false").lower(),
            "true",
        )

    def test_credentials_true_when_enabled(self):
        """allow-credentials header is set when cors_allow_credentials=True."""
        s = _settings(cors_origins=ALLOWED, cors_allow_credentials=True)
        app = create_app(s)
        with TestClient(app) as client:
            resp = client.get("/health", headers={"Origin": ALLOWED})
        self.assertEqual(
            resp.headers.get("access-control-allow-credentials", "").lower(),
            "true",
        )


# ---------------------------------------------------------------------------
# CDK — CORS env injected when context is set
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_CDK, "aws-cdk-lib not installed (install infra/cdk/requirements.txt)")
class TestCDKCORSContext(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        app_with_cors = cdk.App(
            context={"cors_origins": "https://myapp.vercel.app"}
        )
        cls.stack_with_cors = TrustGraphWorkerStack(
            app_with_cors,
            "TestStackWithCORS",
            env=cdk.Environment(account="123456789012", region="us-east-1"),
        )
        cls.template_with_cors = cdk_assertions.Template.from_stack(cls.stack_with_cors)

        app_no_cors = cdk.App()
        cls.stack_no_cors = TrustGraphWorkerStack(
            app_no_cors,
            "TestStackNoCORS",
            env=cdk.Environment(account="123456789012", region="us-east-1"),
        )
        cls.template_no_cors = cdk_assertions.Template.from_stack(cls.stack_no_cors)

    def test_cors_origins_injected_when_context_set(self):
        """API task env includes TRUSTGRAPH_CORS_ORIGINS when cdk context is set."""
        self.template_with_cors.has_resource_properties(
            "AWS::ECS::TaskDefinition",
            cdk_assertions.Match.object_like({
                "ContainerDefinitions": cdk_assertions.Match.array_with([
                    cdk_assertions.Match.object_like({
                        "Environment": cdk_assertions.Match.array_with([
                            cdk_assertions.Match.object_like({
                                "Name": "TRUSTGRAPH_CORS_ORIGINS",
                                "Value": "https://myapp.vercel.app",
                            })
                        ])
                    })
                ])
            }),
        )

    def test_cors_origins_empty_when_no_context(self):
        """API task env has TRUSTGRAPH_CORS_ORIGINS="" when no context provided."""
        self.template_no_cors.has_resource_properties(
            "AWS::ECS::TaskDefinition",
            cdk_assertions.Match.object_like({
                "ContainerDefinitions": cdk_assertions.Match.array_with([
                    cdk_assertions.Match.object_like({
                        "Environment": cdk_assertions.Match.array_with([
                            cdk_assertions.Match.object_like({
                                "Name": "TRUSTGRAPH_CORS_ORIGINS",
                                "Value": "",
                            })
                        ])
                    })
                ])
            }),
        )
