import os

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
    from obsidian_mcp.app.daemon import DaemonService

    pid = DaemonService.from_settings(host=host, port=port).start()
    click.echo(f"started, pid={pid}")


@cli.command()
@click.option("--timeout", type=float, default=None)
def stop(timeout: float | None) -> None:
    from obsidian_mcp.app.daemon import DaemonService

    service = DaemonService.from_settings()
    click.echo(service.stop(timeout) if timeout is not None else service.stop())


@cli.command()
@click.option("--host", default=None)
@click.option("--port", type=int, default=None)
def status(host: str | None, port: int | None) -> None:
    from obsidian_mcp.app.daemon import DaemonService

    click.echo(DaemonService.from_settings(host=host, port=port).status())


@cli.command()
@click.option("-f", "--follow", is_flag=True)
def logs(follow: bool) -> None:
    from obsidian_mcp.app.daemon import DaemonService

    DaemonService.from_settings().logs(follow=follow)


@cli.command()
@click.argument("query")
@click.option("--limit", type=int, default=DEFAULT_SEARCH_LIMIT)
def search(query: str, limit: int) -> None:
    settings = load_settings()
    vault = Vault(settings.vault, settings.embeddings)
    click.echo(vault.search(query, limit=limit, mode=SearchMode.BM25))


def main() -> None:
    cli()


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
