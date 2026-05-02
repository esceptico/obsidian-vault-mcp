# obsidian-mcp

Headless HTTP MCP server for an Obsidian-flavored Markdown vault.

The server works directly against a vault directory. Obsidian Desktop does not
need to be running.

## Features

- Streamable HTTP MCP transport
- Obsidian-flavored Markdown support: YAML frontmatter, wikilinks, embeds,
  inline tags, block IDs, and Markdown links
- Safe vault-relative file operations
- Create, update, delete, move, and list notes/directories
- Wikilink rewriting when Markdown notes are renamed
- SQLite FTS5 lexical search
- Optional OpenAI-compatible embeddings for vector and hybrid search
- Chunk-level indexing for large notes
- Markdown tool responses plus structured MCP output
- File watcher for out-of-band vault edits

## Requirements

- Python 3.14+
- `uv`
- SQLite with FTS5 support
- Loadable SQLite extensions for `sqlite-vec`

## Install

```bash
uv sync
```

## Configure

Required:

```bash
export OBSIDIAN_MCP_VAULT_ROOT="$HOME/path/to/vault"
export OBSIDIAN_MCP_AUTH_TOKEN="change-me"
```

Optional embeddings:

```bash
export OBSIDIAN_MCP_OPENAI_API_KEY="sk-..."
export OBSIDIAN_MCP_OPENAI_BASE_URL="https://openrouter.ai/api/v1"
export OBSIDIAN_MCP_EMBEDDING_MODEL="text-embedding-3-small"
export OBSIDIAN_MCP_EMBEDDING_DIMENSIONS="1536"
export OBSIDIAN_MCP_EMBEDDING_BATCH_SIZE="64"
```

Server bind settings:

```bash
export OBSIDIAN_MCP_HOST="127.0.0.1"
export OBSIDIAN_MCP_PORT="8000"
```

## Run

Foreground:

```bash
uv run obsidian-mcp run --host 127.0.0.1 --port 8000
```

Background daemon:

```bash
uv run obsidian-mcp start --host 127.0.0.1 --port 8000
uv run obsidian-mcp status
uv run obsidian-mcp logs
uv run obsidian-mcp logs -f
uv run obsidian-mcp stop
```

The MCP endpoint is:

```text
http://127.0.0.1:8000/mcp
```

The health endpoint is:

```text
http://127.0.0.1:8000/health
```

Daemon PID/log state is stored outside the vault. On macOS the default is:

```text
~/Library/Application Support/obsidian-mcp/
```

Set `OBSIDIAN_MCP_STATE_DIR` to override it.

## Tools

- `vault_list`: list files and directories with file metadata
- `vault_read`: read a Markdown note, with optional character pagination
- `vault_search`: search notes with BM25, vector, or hybrid mode
- `vault_create_note`: create a Markdown note
- `vault_update_note`: replace note content and/or patch frontmatter
- `vault_move_path`: move or rename files/directories
- `vault_delete_path`: trash or permanently delete files/directories
- `vault_backlinks`: find notes linking to a target note
- `vault_reindex`: rescan the vault from disk

Tool results include Markdown text in `content` and machine-readable data in
`structuredContent`.

## Search

`vault_search` supports:

- `mode="bm25"`: SQLite FTS5 lexical search
- `mode="vector"`: embedding search
- `mode="hybrid"`: reciprocal-rank fusion over FTS5 and vector results

Hybrid/vector search require `OBSIDIAN_MCP_OPENAI_API_KEY`. Without an API key,
hybrid search falls back to BM25 and returns a warning.

The search index is stored inside the vault at:

```text
.obsidian-mcp/index.sqlite
```

This directory is hidden from tool listing and protected from tool writes.

## Security

Binding to a non-loopback host without `OBSIDIAN_MCP_AUTH_TOKEN` is refused.
For remote access, put the server behind HTTPS and require:

```text
Authorization: Bearer <OBSIDIAN_MCP_AUTH_TOKEN>
```

## Development

Run linting and formatting checks:

```bash
uv run ruff format --check .
uv run ruff check .
```

Run tests:

```bash
uv run python -m unittest -v
```

Format before committing:

```bash
uv run ruff format .
```

## Project Layout

- `obsidian_mcp.app`: CLI and daemon lifecycle
- `obsidian_mcp.core`: settings, constants, logging, shared enums
- `obsidian_mcp.index`: FTS/vector indexing and SQLite persistence
- `obsidian_mcp.markdown`: Obsidian Markdown parsing and rewriting
- `obsidian_mcp.transport`: HTTP MCP transport, tool registration, formatting
- `obsidian_mcp.vault`: safe filesystem operations, sync, and file watching
