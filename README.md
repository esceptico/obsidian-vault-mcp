# obsidian-mcp

Headless HTTP MCP server for an Obsidian-flavored Markdown vault.

The server works directly against a vault directory. It does not require Obsidian Desktop or the official Obsidian CLI to be running.

## Scope

- Streamable HTTP MCP transport only
- Python 3.14+
- Obsidian-flavored Markdown parsing: frontmatter, wikilinks, embeds, inline tags, block ids
- Safe vault-relative filesystem operations
- SQLite FTS5 lexical search
- Optional OpenAI embeddings for vector and hybrid search
- Link-aware note renames for Obsidian wikilinks

## Install

```bash
uv sync
```

## Run

```bash
export OBSIDIAN_MCP_VAULT_ROOT="$HOME/path/to/vault"
export OBSIDIAN_MCP_AUTH_TOKEN="change-me"
export OPENAI_API_KEY="sk-..."
uv run obsidian-mcp serve --host 127.0.0.1 --port 8000
```

The MCP endpoint is:

```text
http://127.0.0.1:8000/mcp
```

For remote access, put the server behind HTTPS and keep `OBSIDIAN_MCP_AUTH_TOKEN` enabled.

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

## Notes

`vault_search(mode="hybrid")` combines SQLite FTS5 and OpenAI embeddings when `OPENAI_API_KEY` or `OBSIDIAN_MCP_OPENAI_API_KEY` is set. Without an API key, hybrid search falls back to FTS5 and returns a warning.

The search index is stored inside the vault at `.obsidian-mcp/index.sqlite`.

Embedding settings:

```bash
export OBSIDIAN_MCP_EMBEDDING_MODEL="text-embedding-3-small"
export OBSIDIAN_MCP_EMBEDDING_DIMENSIONS="1536" # optional
export OBSIDIAN_MCP_EMBEDDING_BATCH_SIZE="64"
```
