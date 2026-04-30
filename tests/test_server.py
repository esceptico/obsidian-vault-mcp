import os
import tempfile
import asyncio
import unittest
from pathlib import Path
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
    def _tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"OBSIDIAN_MCP_VAULT_ROOT": tmp}, clear=True):
                mcp = create_mcp(ServerSettings(_env_file=None))  # type: ignore[call-arg]

            async def list_tools():
                return await mcp.list_tools()

            return {tool.name: tool for tool in asyncio.run(list_tools())}

    def test_nullable_fields_are_not_required_when_defaults_exist(self) -> None:
        tools = self._tools()
        self.assertEqual(tools["vault_create_note"].inputSchema["required"], ["path", "content"])
        self.assertEqual(tools["vault_update_note"].inputSchema["required"], ["path"])

    def test_tool_output_schemas_do_not_force_stale_structured_shapes(self) -> None:
        tools = self._tools()
        for name in [
            "vault_list",
            "vault_read",
            "vault_search",
            "vault_create_note",
            "vault_update_note",
            "vault_move_path",
            "vault_delete_path",
            "vault_backlinks",
            "vault_reindex",
        ]:
            self.assertIsNone(tools[name].outputSchema, name)

    def test_tool_annotations_mark_read_only_tools(self) -> None:
        tools = self._tools()
        for name in ["vault_list", "vault_read", "vault_search", "vault_backlinks"]:
            self.assertTrue(tools[name].annotations.readOnlyHint, name)
            self.assertFalse(tools[name].annotations.destructiveHint, name)
            self.assertTrue(tools[name].annotations.idempotentHint, name)
            self.assertFalse(tools[name].annotations.openWorldHint, name)

    def test_tool_annotations_mark_reindex_as_safe_action(self) -> None:
        tools = self._tools()
        annotations = tools["vault_reindex"].annotations
        self.assertFalse(annotations.readOnlyHint)
        self.assertFalse(annotations.destructiveHint)
        self.assertTrue(annotations.idempotentHint)
        self.assertFalse(annotations.openWorldHint)

    def test_tool_annotations_mark_content_mutations_as_destructive(self) -> None:
        tools = self._tools()
        expected = {
            "vault_create_note": False,
            "vault_update_note": True,
            "vault_move_path": False,
            "vault_delete_path": False,
        }
        for name, idempotent in expected.items():
            annotations = tools[name].annotations
            self.assertFalse(annotations.readOnlyHint, name)
            self.assertTrue(annotations.destructiveHint, name)
            self.assertEqual(annotations.idempotentHint, idempotent, name)
            self.assertFalse(annotations.openWorldHint, name)


class ToolResultTests(unittest.TestCase):
    def _mcp(self, tmp: str):
        with patch.dict(os.environ, {"OBSIDIAN_MCP_VAULT_ROOT": tmp}, clear=True):
            return create_mcp(ServerSettings(_env_file=None))  # type: ignore[call-arg]

    def test_search_returns_markdown_content_and_structured_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "Note.md").write_text("semantic search body", encoding="utf-8")
            mcp = self._mcp(tmp)

            async def call_search():
                return await mcp.call_tool("vault_search", {"query": "semantic", "mode": "bm25"})

            result = asyncio.run(call_search())

        self.assertEqual(result.content[0].type, "text")
        self.assertIn("Found 1 matches", result.content[0].text)
        self.assertIn("`Note.md`", result.content[0].text)
        self.assertEqual(result.structuredContent["query"], "semantic")
        self.assertEqual(result.structuredContent["mode"], "bm25")
        self.assertEqual(result.structuredContent["hits"][0]["path"], "Note.md")

    def test_list_wraps_entries_for_structured_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "Alpha.md").write_text("alpha", encoding="utf-8")
            mcp = self._mcp(tmp)

            async def call_list():
                return await mcp.call_tool("vault_list", {"path": ""})

            result = asyncio.run(call_list())

        self.assertIn("Found 1 entries", result.content[0].text)
        self.assertEqual(result.structuredContent["path"], "")
        self.assertEqual(result.structuredContent["entries"][0]["path"], "Alpha.md")

    def test_read_returns_note_markdown_and_structured_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "Alpha.md").write_text("---\ntags: [project]\n---\nBody", encoding="utf-8")
            mcp = self._mcp(tmp)

            async def call_read():
                return await mcp.call_tool("vault_read", {"path": "Alpha.md"})

            result = asyncio.run(call_read())

        self.assertIn("# `Alpha.md`", result.content[0].text)
        self.assertIn("Body", result.content[0].text)
        self.assertEqual(result.structuredContent["frontmatter"]["tags"], ["project"])
        self.assertEqual(result.structuredContent["path"], "Alpha.md")


if __name__ == "__main__":
    unittest.main()
