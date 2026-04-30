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
uv run obsidian-mcp run --host 127.0.0.1 --port 8000
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

`vault_list` supports sorting:

```text
sort_by: name | modified_at | created_at | size
sort_order: asc | desc
```

`vault_read` and `vault_list` include filesystem metadata such as `size`, `created_at`, and `modified_at`. Creation time may be `null` on filesystems that do not expose it.

## Project Layout

- `obsidian_mcp.app`: CLI entrypoints
- `obsidian_mcp.core`: settings, constants, logging, shared enums
- `obsidian_mcp.index`: FTS/vector indexing and SQLite persistence
- `obsidian_mcp.markdown`: Obsidian-flavored Markdown parsing and rewriting
- `obsidian_mcp.transport`: HTTP MCP transport
- `obsidian_mcp.vault`: safe filesystem operations and file watching

## Local Daemon

```bash
uv run obsidian-mcp start --host 127.0.0.1 --port 8000
uv run obsidian-mcp status
uv run obsidian-mcp logs
uv run obsidian-mcp logs -f
uv run obsidian-mcp stop
```

Daemon state is stored at:

```text
~/Library/Application Support/obsidian-mcp/
```

## Remote Access via Cloudflare Tunnel

To expose the server over HTTPS without opening ports, use a [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/).

### One-time setup

```bash
brew install cloudflared
cloudflared tunnel login
cloudflared tunnel create obsidian-mcp
cloudflared tunnel route dns obsidian-mcp vault.example.com
```

Create `~/.cloudflared/config.yml`:

```yaml
tunnel: <TUNNEL_ID>
credentials-file: /Users/<you>/.cloudflared/<TUNNEL_ID>.json

ingress:
  - hostname: vault.example.com
    service: http://127.0.0.1:8000
  - service: http_status:404
```

Test it:

```bash
cloudflared tunnel run obsidian-mcp
```

Always keep `OBSIDIAN_MCP_AUTH_TOKEN` set when exposing the tunnel publicly. Clients must send `Authorization: Bearer <token>`.

### Run as a background service (macOS, launchd)

Create `~/Library/LaunchAgents/com.user.cloudflared.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.user.cloudflared</string>
  <key>ProgramArguments</key>
  <array>
    <string>/opt/homebrew/bin/cloudflared</string>
    <string>tunnel</string>
    <string>run</string>
    <string>obsidian-mcp</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/cloudflared.log</string>
  <key>StandardErrorPath</key><string>/tmp/cloudflared.log</string>
</dict>
</plist>
```

Load it:

```bash
launchctl load -w ~/Library/LaunchAgents/com.user.cloudflared.plist
```

Manage it:

```bash
launchctl list | grep cloudflared
launchctl unload ~/Library/LaunchAgents/com.user.cloudflared.plist
tail -f /tmp/cloudflared.log
```

The tunnel will now start automatically on login and restart on crash.

### Run as a background service (Linux, systemd)

```bash
sudo cloudflared service install
```

This generates a systemd unit reading from `/etc/cloudflared/config.yml`.


## Notes

`vault_search(mode="hybrid")` combines SQLite FTS5 and OpenAI embeddings when `OPENAI_API_KEY` or `OBSIDIAN_MCP_OPENAI_API_KEY` is set. Without an API key, hybrid search falls back to FTS5 and returns a warning.

The search index is stored inside the vault at `.obsidian-mcp/index.sqlite`.

Embedding settings:

```bash
export OBSIDIAN_MCP_EMBEDDING_MODEL="text-embedding-3-small"
export OBSIDIAN_MCP_EMBEDDING_DIMENSIONS="1536" # optional
export OBSIDIAN_MCP_EMBEDDING_BATCH_SIZE="64"
```
