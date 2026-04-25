import hmac
from typing import Any

from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from pydantic import AnyHttpUrl

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
    "auth_token is set but OBSIDIAN_MCP_PUBLIC_URL is not; OAuth metadata will advertise %s. "
    "Reverse-proxied deployments must set OBSIDIAN_MCP_PUBLIC_URL."
)
_AUTH_DISABLED_LOOPBACK_WARNING = (
    "auth_token not set; tools are exposed without authentication on %s"
)
_CLIENT_ID = "obsidian-mcp-client"
_TRANSPORT = "streamable-http"


class StaticTokenVerifier(TokenVerifier):
    def __init__(self, token: str):
        self.token = token

    async def verify_token(self, token: str) -> AccessToken | None:
        if not hmac.compare_digest(token, self.token):
            return None
        return AccessToken(token=token, client_id=_CLIENT_ID, scopes=["vault"])


class Runtime:
    def __init__(self, settings: ServerSettings):
        self.settings = settings
        self.vault = Vault(settings.vault, settings.embeddings)


def create_mcp(settings: ServerSettings | None = None) -> FastMCP:
    settings = settings or load_settings()
    _validate_auth_posture(settings)

    auth, verifier = _build_auth(settings)
    mcp = FastMCP(
        "Obsidian Vault MCP",
        instructions="Headless tools for an Obsidian-flavored Markdown vault.",
        host=settings.host,
        port=settings.port,
        stateless_http=True,
        json_response=True,
        auth=auth,
        token_verifier=verifier,
    )
    runtime = Runtime(settings)
    _register_tools(mcp, runtime)
    return mcp


def _validate_auth_posture(settings: ServerSettings) -> None:
    is_loopback = settings.host in LOOPBACK_HOSTS
    if not settings.auth_token and not is_loopback:
        raise RuntimeError(_AUTH_DISABLED_NON_LOOPBACK)
    if settings.auth_token and settings.public_url is None and not is_loopback:
        log.warning(_PUBLIC_URL_MISSING_WARNING, settings.resolved_public_url)
    if not settings.auth_token:
        log.warning(_AUTH_DISABLED_LOOPBACK_WARNING, settings.host)


def _build_auth(settings: ServerSettings) -> tuple[AuthSettings | None, TokenVerifier | None]:
    if not settings.auth_token:
        return None, None
    auth = AuthSettings(
        issuer_url=AnyHttpUrl(settings.resolved_public_url),
        resource_server_url=AnyHttpUrl(settings.resolved_public_url),
        required_scopes=["vault"],
    )
    return auth, StaticTokenVerifier(settings.auth_token)


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
    create_mcp().run(transport=_TRANSPORT)


if __name__ == "__main__":
    main()
