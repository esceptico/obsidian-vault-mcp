import argparse
import os

from obsidian_mcp.config import load_settings
from obsidian_mcp.vault import Vault


def main() -> None:
    parser = argparse.ArgumentParser(prog="obsidian-mcp")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="Run the Streamable HTTP MCP server")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)

    search = subparsers.add_parser("search", help="Run a local BM25 search")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10)

    args = parser.parse_args()

    if args.command == "serve":
        if args.host:
            os.environ["FASTMCP_HOST"] = args.host
        if args.port:
            os.environ["FASTMCP_PORT"] = str(args.port)
        from obsidian_mcp.logging import configure_default_logging
        from obsidian_mcp.server import main as serve_main

        configure_default_logging()
        serve_main()
        return

    settings = load_settings()
    vault = Vault(settings.vault, settings.embeddings)
    if args.command == "search":
        print(vault.search(args.query, limit=args.limit, mode="bm25"))


if __name__ == "__main__":
    main()
