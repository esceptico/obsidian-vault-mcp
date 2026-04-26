import argparse
import os

from obsidian_mcp.core.config import load_settings
from obsidian_mcp.core.constants import DEFAULT_SEARCH_LIMIT
from obsidian_mcp.core.types import SearchMode
from obsidian_mcp.vault.service import Vault


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="obsidian-mcp")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="Run the Streamable HTTP MCP server in the foreground")
    run.add_argument("--host", default=None)
    run.add_argument("--port", type=int, default=None)
    start = subparsers.add_parser("start", help="Start the server in the background")
    start.add_argument("--host", default=None)
    start.add_argument("--port", type=int, default=None)
    stop = subparsers.add_parser("stop", help="Stop the background server")
    stop.add_argument("--timeout", type=float, default=None)
    status = subparsers.add_parser("status", help="Show background server status")
    status.add_argument("--host", default=None)
    status.add_argument("--port", type=int, default=None)
    logs = subparsers.add_parser("logs", help="Show background server logs")
    logs.add_argument("-f", "--follow", action="store_true")
    search = subparsers.add_parser("search", help="Run a local BM25 search")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=DEFAULT_SEARCH_LIMIT)
    return parser.parse_args()


def main(args: argparse.Namespace | None = None) -> None:
    args = args if args is not None else _parse_args()

    if args.command == "run":
        # CLI flags take precedence; ServerSettings reads OBSIDIAN_MCP_* from env/.env.
        if args.host:
            os.environ["OBSIDIAN_MCP_HOST"] = args.host
        if args.port is not None:
            os.environ["OBSIDIAN_MCP_PORT"] = str(args.port)
        from obsidian_mcp.core.logging import configure_default_logging
        from obsidian_mcp.transport.http import main as serve_main

        configure_default_logging()
        serve_main()
        return

    if args.command == "start":
        from obsidian_mcp.app.daemon import start_daemon

        pid = start_daemon(args.host, args.port)
        print(f"started, pid={pid}")
        return

    if args.command == "stop":
        from obsidian_mcp.app.daemon import STOP_TIMEOUT_SECONDS, stop_daemon

        print(stop_daemon(args.timeout if args.timeout is not None else STOP_TIMEOUT_SECONDS))
        return

    if args.command == "status":
        from obsidian_mcp.app.daemon import daemon_status

        print(daemon_status(args.host, args.port))
        return

    if args.command == "logs":
        from obsidian_mcp.app.daemon import show_logs

        show_logs(args.follow)
        return

    settings = load_settings()
    vault = Vault(settings.vault, settings.embeddings)
    if args.command == "search":
        print(vault.search(args.query, limit=args.limit, mode=SearchMode.BM25))


if __name__ == "__main__":
    main()
