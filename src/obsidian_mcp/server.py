import hmac
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from obsidian_mcp.config import ServerSettings, load_settings
from obsidian_mcp.constants import DEFAULT_SEARCH_LIMIT, LOOPBACK_HOSTS
from obsidian_mcp.logging import get_logger
from obsidian_mcp.types import DeleteStrategy, SearchMode
from obsidian_mcp.vault import Vault

log = get_logger("server")

_AUTH_DISABLED_NON_LOOPBACK = (
    "AUTH DISABLED: refusing to bind a non-loopback host without OBSIDIAN_MCP_AUTH_TOKEN. "
    "Set the token, or bind to 127.0.0.1."
)
_PUBLIC_URL_MISSING_WARNING = (
    "auth_token is set but OBSIDIAN_MCP_PUBLIC_URL is not; clients reaching the server through "
    "a different URL than %s may behave unexpectedly. Set OBSIDIAN_MCP_PUBLIC_URL when proxied."
)
_AUTH_DISABLED_LOOPBACK_WARNING = (
    "auth_token not set; tools are exposed without authentication on %s"
)
_REALM = "obsidian-mcp"
_UNAUTHORIZED_BODY = b'{"error":"unauthorized"}'

# Headers a browser MCP client may send. Per the Streamable HTTP spec
# (modelcontextprotocol.io/specification/2025-06-18/basic/transports), clients
# send Accept, Content-Type, Mcp-Session-Id (after initialize),
# MCP-Protocol-Version (after handshake), and Last-Event-ID (when resuming an
# SSE stream). Authorization is ours.
_CORS_ALLOW_HEADERS = [
    "Accept",
    "Authorization",
    "Content-Type",
    "Last-Event-ID",
    "Mcp-Session-Id",
    "MCP-Protocol-Version",
]
# Browsers cannot read response headers via JS unless the server explicitly
# exposes them. Mcp-Session-Id must be readable so the client can store and
# forward it on subsequent requests.
_CORS_EXPOSE_HEADERS = ["Mcp-Session-Id"]


class BearerAuthMiddleware:
    """Static bearer-token guard for the MCP HTTP endpoint.

    Plain `Authorization: Bearer <token>` check, returning 401 with a
    `WWW-Authenticate: Bearer realm="..."` header. Deliberately does NOT
    advertise OAuth resource metadata, so spec-compliant MCP clients send
    the pre-shared token directly instead of attempting an OAuth discovery
    flow against an authorization server we don't run.
    """

    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._expected = f"Bearer {token}"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        provided = _bearer_header(scope)
        if not hmac.compare_digest(provided, self._expected):
            await _send_unauthorized(send)
            return
        await self._app(scope, receive, send)


def _bearer_header(scope: Scope) -> str:
    for name, value in scope.get("headers") or ():
        if name == b"authorization":
            return value.decode("latin-1")
    return ""


async def _send_unauthorized(send: Send) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", f'Bearer realm="{_REALM}"'.encode("latin-1")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": _UNAUTHORIZED_BODY})


class Runtime:
    def __init__(self, settings: ServerSettings):
        self.settings = settings
        self.vault = Vault(settings.vault, settings.embeddings)


def create_mcp(settings: ServerSettings | None = None) -> FastMCP:
    settings = settings or load_settings()
    _validate_auth_posture(settings)
    mcp = FastMCP(
        "Obsidian Vault MCP",
        instructions="Headless tools for an Obsidian-flavored Markdown vault.",
        host=settings.host,
        port=settings.port,
        stateless_http=True,
        json_response=True,
    )
    runtime = Runtime(settings)
    _register_tools(mcp, runtime)
    return mcp


def build_asgi_app(settings: ServerSettings, mcp: FastMCP) -> ASGIApp:
    """ASGI stack: CORS (outermost) → BearerAuth → FastMCP StreamableHTTP.

    CORS goes outermost so browser preflights succeed without auth — the
    actual POST/GET that follows passes through BearerAuth and must carry
    the token.
    """
    app: ASGIApp = mcp.streamable_http_app()
    if settings.auth_token:
        app = BearerAuthMiddleware(app, settings.auth_token)
    app = CORSMiddleware(
        app,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=_CORS_ALLOW_HEADERS,
        expose_headers=_CORS_EXPOSE_HEADERS,
    )
    return app


def _validate_auth_posture(settings: ServerSettings) -> None:
    is_loopback = settings.host in LOOPBACK_HOSTS
    if not settings.auth_token and not is_loopback:
        raise RuntimeError(_AUTH_DISABLED_NON_LOOPBACK)
    if settings.auth_token and settings.public_url is None and not is_loopback:
        log.warning(_PUBLIC_URL_MISSING_WARNING, settings.resolved_public_url)
    if not settings.auth_token:
        log.warning(_AUTH_DISABLED_LOOPBACK_WARNING, settings.host)


def _register_tools(mcp: FastMCP, runtime: Runtime) -> None:
    """Tool surface. Defaults here are deliberately minimal — only those that
    materially improve client UX (search mode/limit, default destructive
    strategy=trash). Everything else is required."""

    @mcp.tool()
    def vault_list(path: str) -> list[dict[str, Any]]:
        """List files and directories under a vault-relative path. Pass "" for the vault root."""
        return runtime.vault.list(path)

    @mcp.tool()
    def vault_read(path: str) -> dict[str, Any]:
        """Read a Markdown file with frontmatter, links, tags, and content."""
        return runtime.vault.read(path)

    @mcp.tool()
    def vault_search(
        query: str,
        limit: int = DEFAULT_SEARCH_LIMIT,
        mode: SearchMode = SearchMode.HYBRID,
    ) -> dict[str, Any]:
        """Search vault notes. Hybrid combines FTS5 + embeddings; vector requires OPENAI_API_KEY."""
        return runtime.vault.search(query, limit, mode)

    @mcp.tool()
    def vault_create_note(
        path: str,
        content: str,
        frontmatter: dict[str, Any] | None,
        overwrite: bool,
    ) -> dict[str, Any]:
        """Create a Markdown note. Pass `overwrite=true` to replace an existing one."""
        return runtime.vault.create_note(path, content, frontmatter, overwrite)

    @mcp.tool()
    def vault_update_note(
        path: str,
        content: str | None,
        frontmatter_patch: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Replace a note body and/or patch YAML frontmatter. Null patch values delete keys."""
        return runtime.vault.update_note(path, content, frontmatter_patch)

    @mcp.tool()
    def vault_move_path(
        source: str,
        destination: str,
        rewrite_links: bool,
        overwrite: bool,
    ) -> dict[str, Any]:
        """Move or rename a file/directory, with wikilink rewriting for note renames."""
        return runtime.vault.move_path(source, destination, rewrite_links, overwrite)

    @mcp.tool()
    def vault_delete_path(
        path: str,
        recursive: bool,
        strategy: DeleteStrategy = DeleteStrategy.TRASH,
    ) -> dict[str, Any]:
        """Delete a file or directory. `strategy=trash` (default) preserves the file in .trash/."""
        return runtime.vault.delete_path(path, recursive, strategy)

    @mcp.tool()
    def vault_backlinks(path: str) -> dict[str, Any]:
        """Find notes that link to a target note via Obsidian wikilinks."""
        return runtime.vault.backlinks(path)

    @mcp.tool()
    def vault_reindex() -> dict[str, Any]:
        """Mark the search index dirty; the next search rebuilds from disk."""
        runtime.vault.invalidate_index()
        return {"ok": True}


def main() -> None:
    settings = load_settings()
    mcp = create_mcp(settings)
    app = build_asgi_app(settings, mcp)
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
