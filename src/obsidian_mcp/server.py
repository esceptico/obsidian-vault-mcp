import hmac
from typing import Any

from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from pydantic import AnyHttpUrl

from obsidian_mcp.config import ServerSettings, load_settings
from obsidian_mcp.logging import get_logger
from obsidian_mcp.vault import Vault

log = get_logger("server")
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class StaticTokenVerifier(TokenVerifier):
    def __init__(self, token: str):
        self.token = token

    async def verify_token(self, token: str) -> AccessToken | None:
        if not hmac.compare_digest(token, self.token):
            return None
        return AccessToken(token=token, client_id="obsidian-mcp-client", scopes=["vault"])


class Runtime:
    def __init__(self, settings: ServerSettings):
        self.settings = settings
        self.vault = Vault(settings.vault, settings.embeddings)


def create_mcp(settings: ServerSettings | None = None) -> FastMCP:
    settings = settings or load_settings()

    if not settings.auth_token and settings.host not in LOOPBACK_HOSTS:
        raise RuntimeError(
            "AUTH DISABLED: refusing to bind a non-loopback host without OBSIDIAN_MCP_AUTH_TOKEN. "
            "Set the token, or bind to 127.0.0.1."
        )

    if settings.auth_token and settings.public_url is None and settings.host not in LOOPBACK_HOSTS:
        log.warning(
            "auth_token is set but OBSIDIAN_MCP_PUBLIC_URL is not; OAuth metadata will advertise %s. "
            "Reverse-proxied deployments must set OBSIDIAN_MCP_PUBLIC_URL.",
            settings.resolved_public_url,
        )

    if not settings.auth_token:
        log.warning(
            "auth_token not set; tools are exposed without authentication on %s",
            settings.host,
        )

    auth = None
    verifier = None
    if settings.auth_token:
        auth = AuthSettings(
            issuer_url=AnyHttpUrl(settings.resolved_public_url),
            resource_server_url=AnyHttpUrl(settings.resolved_public_url),
            required_scopes=["vault"],
        )
        verifier = StaticTokenVerifier(settings.auth_token)

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

    @mcp.tool()
    def vault_list(path: str = "") -> list[dict[str, Any]]:
        """List files and directories under a vault-relative path."""
        return runtime.vault.list(path)

    @mcp.tool()
    def vault_read(path: str) -> dict[str, Any]:
        """Read a Markdown file with frontmatter, links, tags, and content."""
        return runtime.vault.read(path)

    @mcp.tool()
    def vault_search(query: str, limit: int = 10, mode: str = "hybrid") -> dict[str, Any]:
        """Search vault notes. Hybrid mode is BM25-only until embeddings are configured."""
        return runtime.vault.search(query=query, limit=limit, mode=mode)

    @mcp.tool()
    def vault_create_note(
        path: str,
        content: str = "",
        frontmatter: dict[str, Any] | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Create a Markdown note, optionally with YAML frontmatter."""
        return runtime.vault.create_note(path, content, frontmatter, overwrite)

    @mcp.tool()
    def vault_update_note(
        path: str,
        content: str | None = None,
        frontmatter_patch: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Replace a note body and/or patch YAML frontmatter. Null patch values delete keys."""
        return runtime.vault.update_note(path, content, frontmatter_patch)

    @mcp.tool()
    def vault_move_path(
        source: str,
        destination: str,
        rewrite_links: bool = True,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Move or rename a file/directory, with Obsidian wikilink rewriting for note renames."""
        return runtime.vault.move_path(source, destination, rewrite_links, overwrite)

    @mcp.tool()
    def vault_delete_path(path: str, recursive: bool = False, strategy: str = "trash") -> dict[str, Any]:
        """Delete a file or directory. Uses the configured vault trash folder by default."""
        return runtime.vault.delete_path(path, recursive, strategy)

    @mcp.tool()
    def vault_backlinks(path: str) -> dict[str, Any]:
        """Find notes that link to a target note via Obsidian wikilinks."""
        return runtime.vault.backlinks(path)

    @mcp.tool()
    def vault_reindex() -> dict[str, Any]:
        """Clear the in-process search index; the next search rebuilds it."""
        runtime.vault.invalidate_index()
        return {"ok": True}

    return mcp


def main() -> None:
    create_mcp().run(transport="streamable-http")


if __name__ == "__main__":
    main()
