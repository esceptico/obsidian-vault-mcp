import os
import tempfile
import asyncio
import unittest
from unittest.mock import patch

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from obsidian_mcp.core.config import ServerSettings
from obsidian_mcp.transport.http import BearerAuthMiddleware, build_asgi_app, create_mcp


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

    def test_health_endpoint_does_not_require_auth(self) -> None:
        settings = self._settings(OBSIDIAN_MCP_AUTH_TOKEN="t")
        app = build_asgi_app(settings, create_mcp(settings))
        client = TestClient(app)
        response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})

    def test_cors_allows_mcp_protocol_version_header(self) -> None:
        """Per the Streamable HTTP spec, clients MUST send MCP-Protocol-Version
        on every request after handshake. If the CORS allow_headers list
        doesn't include it, browser preflight rejects the actual request and
        the user sees `TypeError: Failed to fetch`."""
        settings = self._settings(OBSIDIAN_MCP_AUTH_TOKEN="t")
        app = build_asgi_app(settings, create_mcp(settings))
        client = TestClient(app)
        response = client.options(
            "/mcp",
            headers={
                "Origin": "http://localhost:6274",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "MCP-Protocol-Version, Authorization, Content-Type",
            },
        )
        self.assertEqual(response.status_code, 200)
        allowed = response.headers.get("access-control-allow-headers", "").lower()
        self.assertIn("mcp-protocol-version", allowed)


class ToolSchemaTests(unittest.TestCase):
    def test_nullable_fields_are_not_required_when_defaults_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"OBSIDIAN_MCP_VAULT_ROOT": tmp}, clear=True):
                mcp = create_mcp(ServerSettings(_env_file=None))  # type: ignore[call-arg]

            async def list_tools():
                return await mcp.list_tools()

            tools = {tool.name: tool.inputSchema for tool in asyncio.run(list_tools())}

        self.assertEqual(tools["vault_create_note"]["required"], ["path", "content"])
        self.assertEqual(tools["vault_update_note"]["required"], ["path"])


if __name__ == "__main__":
    unittest.main()
