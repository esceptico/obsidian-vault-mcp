import os
from typing import Any

import click

from obsidian_mcp.core.config import load_settings
from obsidian_mcp.core.constants import DEFAULT_SEARCH_LIMIT
from obsidian_mcp.core.types import SearchMode
from obsidian_mcp.vault.service import Vault


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def cli() -> None:
    pass


@cli.command()
@click.option("--host", default=None)
@click.option("--port", type=int, default=None)
def run(host: str | None, port: int | None) -> None:
    run_server(host, port)


@cli.command()
@click.option("--host", default=None)
@click.option("--port", type=int, default=None)
def start(host: str | None, port: int | None) -> None:
    from obsidian_mcp.app.daemon import start_daemon

    click.echo(f"started, pid={start_daemon(host, port)}")


@cli.command()
@click.option("--timeout", type=float, default=None)
def stop(timeout: float | None) -> None:
    from obsidian_mcp.app.daemon import stop_daemon

    click.echo(stop_daemon(timeout) if timeout is not None else stop_daemon())


@cli.command()
@click.option("--host", default=None)
@click.option("--port", type=int, default=None)
def status(host: str | None, port: int | None) -> None:
    from obsidian_mcp.app.daemon import daemon_status

    click.echo(daemon_status(host, port))


@cli.command()
@click.option("-f", "--follow", is_flag=True)
def logs(follow: bool) -> None:
    from obsidian_mcp.app.daemon import show_logs

    show_logs(follow)


@cli.command()
@click.argument("query")
@click.option("--limit", type=int, default=DEFAULT_SEARCH_LIMIT)
def search(query: str, limit: int) -> None:
    settings = load_settings()
    vault = Vault(settings.vault, settings.embeddings)
    click.echo(vault.search(query, limit=limit, mode=SearchMode.BM25))


def main(args: Any | None = None) -> None:
    if args is not None:
        run_namespace(args)
        return
    cli()


def run_namespace(args: Any) -> None:
    if args.command == "run":
        run_server(args.host, args.port)
        return

    if args.command == "start":
        from obsidian_mcp.app.daemon import start_daemon

        print(f"started, pid={start_daemon(args.host, args.port)}")
        return

    if args.command == "stop":
        from obsidian_mcp.app.daemon import stop_daemon

        print(stop_daemon(args.timeout) if args.timeout is not None else stop_daemon())
        return

    if args.command == "status":
        from obsidian_mcp.app.daemon import daemon_status

        print(daemon_status(args.host, args.port))
        return

    if args.command == "logs":
        from obsidian_mcp.app.daemon import show_logs

        show_logs(args.follow)
        return

    if args.command == "search":
        settings = load_settings()
        vault = Vault(settings.vault, settings.embeddings)
        print(vault.search(args.query, limit=args.limit, mode=SearchMode.BM25))


def run_server(host: str | None, port: int | None) -> None:
    if host:
        os.environ["OBSIDIAN_MCP_HOST"] = host
    if port is not None:
        os.environ["OBSIDIAN_MCP_PORT"] = str(port)
    from obsidian_mcp.core.logging import configure_default_logging
    from obsidian_mcp.transport.http import main as serve_main

    configure_default_logging()
    serve_main()


if __name__ == "__main__":
    main()
