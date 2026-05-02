# obsidian-vault-mcp

Headless HTTP MCP server for an Obsidian-flavored Markdown vault

## Install

```bash
uv sync
```

Run from PyPI without installing into the current project:

```bash
uvx obsidian-vault-mcp run --host 127.0.0.1 --port 8000
uvx --from obsidian-vault-mcp ovm run --host 127.0.0.1 --port 8000
```

## Configure

```bash
export OBSIDIAN_VAULT_MCP_VAULT_ROOT="$HOME/path/to/vault"
export OBSIDIAN_VAULT_MCP_AUTH_TOKEN="change-me"
```

Optional OpenAI-compatible embeddings:

```bash
export OBSIDIAN_VAULT_MCP_OPENAI_API_KEY="sk-..."
export OBSIDIAN_VAULT_MCP_OPENAI_BASE_URL="https://openrouter.ai/api/v1"
export OBSIDIAN_VAULT_MCP_EMBEDDING_MODEL="text-embedding-3-small"
```

## Run

```bash
uv run obsidian-vault-mcp run --host 127.0.0.1 --port 8000
```

Shortcut:

```bash
uv run ovm run --host 127.0.0.1 --port 8000
```

MCP endpoint:

```text
http://127.0.0.1:8000/mcp
```

Daemon commands:

```bash
uv run obsidian-vault-mcp start
uv run obsidian-vault-mcp status
uv run obsidian-vault-mcp logs -f
uv run obsidian-vault-mcp stop
```

## Tools

- `vault_list`
- `vault_read`
- `vault_search`
- `vault_create_note`
- `vault_update_note`
- `vault_move_path`
- `vault_delete_path`
- `vault_backlinks`
- `vault_reindex`

Tool results return Markdown text for agents and `structuredContent` for programmatic clients.

The Markdown `content` is the compatibility path and should contain everything an agent needs. `structuredContent` is useful for clients that expose it, but some MCP clients still treat it as secondary metadata.

## Development

```bash
uv sync --extra dev
uv run ruff format --check .
uv run ruff check .
uv run python -m unittest -v
```
