import hmac
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, ToolAnnotations
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from obsidian_mcp.core.config import ServerSettings, load_settings
from obsidian_mcp.core.constants import (
    DEFAULT_LIST_LIMIT,
    DEFAULT_SEARCH_LIMIT,
    LOOPBACK_HOSTS,
    MAX_LIST_LIMIT,
    MAX_SEARCH_LIMIT,
)
from obsidian_mcp.core.logging import get_logger
from obsidian_mcp.core.types import DeleteStrategy, ListSortBy, SearchMode, SortOrder
from obsidian_mcp.transport.formatters import (
    format_backlinks,
    format_create_note,
    format_delete_path,
    format_list,
    format_move_path,
    format_read,
    format_reindex,
    format_search,
    format_update_note,
    text_result,
)
from obsidian_mcp.transport.pagination import page_items, validate_page
from obsidian_mcp.vault.service import Vault

log = get_logger("server")

_AUTH_DISABLED_NON_LOOPBACK = (
    "AUTH DISABLED: refusing to bind a non-loopback host without OBSIDIAN_MCP_AUTH_TOKEN. "
    "Set the token, or bind to 127.0.0.1."
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
_HEALTH_PATH = "/health"
_READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
_REINDEX = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
_CREATE_NOTE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)
_UPDATE_NOTE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False)
_MOVE_PATH = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)
_DELETE_PATH = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)


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


class HealthMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path") == _HEALTH_PATH:
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": b'{"ok":true}'})
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


def create_mcp(settings: ServerSettings | None = None, vault: Vault | None = None) -> FastMCP:
    settings = settings or load_settings()
    _validate_auth_posture(settings)
    if vault is None:
        vault = Vault(settings.vault, settings.embeddings)
    # DNS rebinding protection: disabled when running behind a tunnel/proxy
    # (Cloudflare etc.) since the Host header will be the tunnel domain.
    # BearerAuth middleware provides auth instead.
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    )
    mcp = FastMCP(
        "Obsidian Vault MCP",
        instructions=(
            "Headless tools for an Obsidian-flavored Markdown vault. Tool results include "
            "Markdown text in content and machine-readable data in structuredContent."
        ),
        host=settings.host,
        port=settings.port,
        stateless_http=True,
        json_response=True,
        transport_security=transport_security,
    )
    _register_tools(mcp, vault)
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
    app = HealthMiddleware(app)
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
    if not settings.auth_token:
        log.warning(_AUTH_DISABLED_LOOPBACK_WARNING, settings.host)


def _register_tools(mcp: FastMCP, vault: Vault) -> None:
    """Tool surface. Defaults here are deliberately minimal — only those that
    materially improve client UX (search mode/limit, default destructive
    strategy=trash). Everything else is required."""

    @mcp.tool(annotations=_READ_ONLY, structured_output=False)
    def vault_list(
        path: str = "",
        sort_by: ListSortBy = ListSortBy.NAME,
        sort_order: SortOrder = SortOrder.ASC,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> CallToolResult:
        """List files and directories with size/created_at/modified_at metadata.

        Omit path for the vault root. Sort with sort_by=name|modified_at|created_at|size
        and sort_order=asc|desc. Page with limit and offset.
        Returns Markdown text plus structuredContent entries.
        """
        validate_page(limit, offset, MAX_LIST_LIMIT)
        all_entries = vault.list(path, sort_by, sort_order)
        page = page_items(all_entries, limit, offset)
        structured = {
            "path": path,
            "sort_by": ListSortBy(sort_by).value,
            "sort_order": SortOrder(sort_order).value,
            "limit": page.limit,
            "offset": page.offset,
            "total": page.total,
            "has_more": page.has_more,
            "next_offset": page.next_offset,
            "entries": page.items,
            # Backward compatibility for clients that cached the old
            # list[dict] output schema inferred by FastMCP. That schema
            # wrapped the list under a required "result" key.
            "result": page.items,
        }
        return text_result(
            format_list(path, page, ListSortBy(sort_by), SortOrder(sort_order)),
            structured,
        )

    @mcp.tool(annotations=_READ_ONLY, structured_output=False)
    def vault_read(path: str) -> CallToolResult:
        """Read a Markdown file. Returns Markdown text plus structuredContent metadata."""
        result = vault.read(path)
        return text_result(format_read(result), result)

    @mcp.tool(annotations=_READ_ONLY, structured_output=False)
    def vault_search(
        query: str,
        limit: int = DEFAULT_SEARCH_LIMIT,
        offset: int = 0,
        mode: SearchMode = SearchMode.HYBRID,
    ) -> CallToolResult:
        """Search vault notes. Page with limit and offset. Returns Markdown plus structuredContent."""
        validate_page(limit, offset, MAX_SEARCH_LIMIT)
        search_mode = SearchMode(mode)
        requested = min(MAX_SEARCH_LIMIT, offset + limit + 1)
        result = vault.search(query, requested, search_mode)
        page = page_items(result.get("hits") or [], limit, offset)
        warnings = result.get("warnings") or []
        paged = {
            "hits": page.items,
            "warnings": warnings,
            "has_more": page.has_more,
            "next_offset": page.next_offset,
        }
        structured = {
            "query": query,
            "limit": page.limit,
            "offset": page.offset,
            "returned": page.returned,
            "mode": search_mode.value,
            **paged,
        }
        return text_result(format_search(query, search_mode, page, warnings), structured)

    @mcp.tool(annotations=_CREATE_NOTE, structured_output=False)
    def vault_create_note(
        path: str,
        content: str,
        frontmatter: dict[str, Any] | None = None,
        overwrite: bool = False,
    ) -> CallToolResult:
        """Create a Markdown note. Returns a Markdown summary plus structuredContent data."""
        result = vault.create_note(path, content, frontmatter, overwrite)
        return text_result(format_create_note(result), result)

    @mcp.tool(annotations=_UPDATE_NOTE, structured_output=False)
    def vault_update_note(
        path: str,
        content: str | None = None,
        frontmatter_patch: dict[str, Any] | None = None,
    ) -> CallToolResult:
        """Replace note body and/or patch frontmatter. Returns text plus structuredContent."""
        result = vault.update_note(path, content, frontmatter_patch)
        return text_result(format_update_note(result), result)

    @mcp.tool(annotations=_MOVE_PATH, structured_output=False)
    def vault_move_path(
        source: str,
        destination: str,
        rewrite_links: bool = True,
        overwrite: bool = False,
    ) -> CallToolResult:
        """Move or rename a file/directory. Returns a text summary plus structuredContent."""
        result = vault.move_path(source, destination, rewrite_links, overwrite)
        return text_result(format_move_path(result), result)

    @mcp.tool(annotations=_DELETE_PATH, structured_output=False)
    def vault_delete_path(
        path: str,
        recursive: bool = False,
        strategy: DeleteStrategy = DeleteStrategy.TRASH,
    ) -> CallToolResult:
        """Delete a file or directory. Returns a text summary plus structuredContent."""
        result = vault.delete_path(path, recursive, strategy)
        return text_result(format_delete_path(result), result)

    @mcp.tool(annotations=_READ_ONLY, structured_output=False)
    def vault_backlinks(path: str) -> CallToolResult:
        """Find notes that link to a target note. Returns Markdown plus structuredContent."""
        result = vault.backlinks(path)
        return text_result(format_backlinks(result), result)

    @mcp.tool(annotations=_REINDEX, structured_output=False)
    def vault_reindex() -> CallToolResult:
        """Re-scan the vault from disk and bring the index up to date.
        Returns a Markdown summary plus structuredContent diff counts."""
        result = {"ok": True, **vault.reindex()}
        return text_result(format_reindex(result), result)


def main() -> None:
    settings = load_settings()
    vault = Vault(settings.vault, settings.embeddings)
    mcp = create_mcp(settings, vault)
    vault.start_watching()
    try:
        app = build_asgi_app(settings, mcp)
        uvicorn.run(app, host=settings.host, port=settings.port, forwarded_allow_ips="*")
    finally:
        vault.stop_watching()


if __name__ == "__main__":
    main()
