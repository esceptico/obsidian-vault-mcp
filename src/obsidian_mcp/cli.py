import argparse
import os

from obsidian_mcp.config import load_settings
from obsidian_mcp.constants import DEFAULT_SEARCH_LIMIT
from obsidian_mcp.types import SearchMode
from obsidian_mcp.vault import Vault


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="obsidian-mcp")
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve = subparsers.add_parser("serve", help="Run the Streamable HTTP MCP server")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    search = subparsers.add_parser("search", help="Run a local BM25 search")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=DEFAULT_SEARCH_LIMIT)
    return parser.parse_args()


def main(args: argparse.Namespace | None = None) -> None:
    args = args if args is not None else _parse_args()

    if args.command == "serve":
        # CLI flags take precedence; ServerSettings reads OBSIDIAN_MCP_* from env/.env.
        if args.host:
            os.environ["OBSIDIAN_MCP_HOST"] = args.host
        if args.port:
            os.environ["OBSIDIAN_MCP_PORT"] = str(args.port)
        from obsidian_mcp.logging import configure_default_logging
        from obsidian_mcp.server import main as serve_main

        configure_default_logging()
        serve_main()
        return

    settings = load_settings()
    vault = Vault(settings.vault, settings.embeddings)
    if args.command == "search":
        print(vault.search(args.query, limit=args.limit, mode=SearchMode.BM25))


if __name__ == "__main__":
    main()
