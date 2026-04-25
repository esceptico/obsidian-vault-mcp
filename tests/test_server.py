import os
import tempfile
import unittest
from unittest.mock import patch

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from obsidian_mcp.config import ServerSettings
from obsidian_mcp.server import BearerAuthMiddleware, build_asgi_app, create_mcp


def _make_inner_app() -> Starlette:
    async def ok(_request):
        return JSONResponse({"ok": True})

    return Starlette(routes=[Route("/mcp", ok, methods=["POST", "GET"])])


class BearerAuthMiddlewareTests(unittest.TestCase):
    TOKEN = "s3cret-token"

    def setUp(self) -> None:
        self.client = TestClient(BearerAuthMiddleware(_make_inner_app(), self.TOKEN))

    def test_correct_token_passes_through(self) -> None:
        response = self.client.post("/mcp", headers={"Authorization": f"Bearer {self.TOKEN}"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})

    def test_missing_authorization_returns_401(self) -> None:
        response = self.client.post("/mcp")
        self.assertEqual(response.status_code, 401)

    def test_wrong_token_returns_401(self) -> None:
        response = self.client.post("/mcp", headers={"Authorization": "Bearer wrong"})
        self.assertEqual(response.status_code, 401)

    def test_401_advertises_bearer_realm_only(self) -> None:
        """Critical: WWW-Authenticate must NOT include `resource_metadata=`,
        otherwise spec-compliant MCP clients will attempt OAuth discovery
        instead of just sending the static token we want them to use."""
        response = self.client.post("/mcp")
        www_auth = response.headers.get("www-authenticate", "")
        self.assertIn("Bearer", www_auth)
        self.assertNotIn("resource_metadata", www_auth)


class AuthPostureTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _settings(self, **overrides) -> ServerSettings:
        env = {"OBSIDIAN_MCP_VAULT_ROOT": self._tmp.name, **overrides}
        with patch.dict(os.environ, env, clear=True):
            return ServerSettings(_env_file=None)  # type: ignore[call-arg]

    def test_non_loopback_without_token_refuses(self) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            create_mcp(self._settings(OBSIDIAN_MCP_HOST="0.0.0.0"))
        self.assertIn("AUTH", str(ctx.exception).upper())

    def test_loopback_without_token_starts(self) -> None:
        create_mcp(self._settings(OBSIDIAN_MCP_HOST="127.0.0.1"))

    def test_auth_token_without_public_url_warns(self) -> None:
        with self.assertLogs("obsidian_mcp.server", level="WARNING") as captured:
            create_mcp(
                self._settings(
                    OBSIDIAN_MCP_HOST="0.0.0.0",
                    OBSIDIAN_MCP_AUTH_TOKEN="t",
                )
            )
        self.assertTrue(any("OBSIDIAN_MCP_PUBLIC_URL" in line for line in captured.output))


class BuildAsgiAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _settings(self, **overrides) -> ServerSettings:
        env = {"OBSIDIAN_MCP_VAULT_ROOT": self._tmp.name, **overrides}
        with patch.dict(os.environ, env, clear=True):
            return ServerSettings(_env_file=None)  # type: ignore[call-arg]

    def test_cors_preflight_succeeds_without_auth(self) -> None:
        """Browser preflight (OPTIONS) carries no Authorization header by design.
        If the bearer guard rejects it, the actual request never runs and the
        browser-based MCP Inspector loops on 401s. CORS middleware must short-
        circuit preflights with 200 + Access-Control-Allow-* headers."""
        settings = self._settings(OBSIDIAN_MCP_AUTH_TOKEN="t")
        app = build_asgi_app(settings, create_mcp(settings))
        client = TestClient(app)
        response = client.options(
            "/mcp",
            headers={
                "Origin": "http://localhost:6274",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Authorization, Content-Type",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("access-control-allow-origin", {k.lower() for k in response.headers})

    def test_actual_request_still_requires_auth_after_cors(self) -> None:
        settings = self._settings(OBSIDIAN_MCP_AUTH_TOKEN="t")
        app = build_asgi_app(settings, create_mcp(settings))
        client = TestClient(app)
        # POST with Origin (browser-style) but no Authorization → still 401.
        response = client.post(
            "/mcp",
            headers={"Origin": "http://localhost:6274", "Content-Type": "application/json"},
            content=b"{}",
        )
        self.assertEqual(response.status_code, 401)

    def test_well_known_discovery_paths_return_404_unauthenticated(self) -> None:
        """MCP Inspector and other spec-compliant clients probe these well-known
        paths whenever they see a 401 — if we 401 the probes too, the client
        loops through the whole discovery list before falling back to the
        static bearer. 404 on each (not 401) lets the client skip ahead."""
        settings = self._settings(OBSIDIAN_MCP_AUTH_TOKEN="t")
        app = build_asgi_app(settings, create_mcp(settings))
        client = TestClient(app)
        for path in (
            "/.well-known/oauth-protected-resource",
            "/.well-known/oauth-protected-resource/mcp",
            "/.well-known/oauth-authorization-server",
            "/.well-known/openid-configuration",
        ):
            with self.subTest(path=path):
                response = client.get(path)  # no Authorization header
                self.assertEqual(response.status_code, 404, f"{path} should 404, got {response.status_code}")

    def test_options_request_to_protocol_path_is_not_gated_by_bearer(self) -> None:
        """OPTIONS doesn't carry MCP payload; the bearer guard should let it
        through (CORS already handles valid preflights). Otherwise Inspector's
        session lifecycle generates 401 noise."""
        settings = self._settings(OBSIDIAN_MCP_AUTH_TOKEN="t")
        app = build_asgi_app(settings, create_mcp(settings))
        # FastMCP's StreamableHTTP manager needs lifespan startup; TestClient
        # only triggers that when used as a context manager.
        with TestClient(app) as client:
            # OPTIONS without Authorization, without preflight headers — CORS
            # won't synthesize a 200, request reaches inner app. Inner may 4xx
            # (FastMCP doesn't speak OPTIONS here) but must NOT be 401.
            response = client.options("/mcp")
        self.assertNotEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
