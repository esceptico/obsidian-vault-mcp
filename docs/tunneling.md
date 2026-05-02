# Tunneling

Use tunneling when a remote MCP client needs to reach a server running on your machine.

Keep `headless-obsidian-mcp` bound to localhost and let the tunnel provide the public HTTPS URL:

```bash
export HEADLESS_OBSIDIAN_MCP_VAULT_ROOT="$HOME/path/to/vault"
export HEADLESS_OBSIDIAN_MCP_AUTH_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"

headless-obsidian-mcp run --host 127.0.0.1 --port 8000
```

Configure the MCP client with:

```text
https://<tunnel-host>/mcp
Authorization: Bearer <HEADLESS_OBSIDIAN_MCP_AUTH_TOKEN>
```

## Cloudflare Quick Tunnel

Good for temporary remote access without DNS setup:

```bash
cloudflared tunnel --url http://localhost:8000
```

Use the generated `https://*.trycloudflare.com/mcp` URL in the MCP client.

Quick Tunnel URLs are ephemeral. For a stable URL, use a named Cloudflare Tunnel.

## ngrok

Good for quick public HTTPS access and request inspection:

```bash
ngrok http 8000
```

Use the generated `https://*.ngrok.app/mcp` URL in the MCP client.

## Tailscale

Use Tailscale Serve for private access inside your tailnet:

```bash
tailscale serve 8000
```

Use Tailscale Funnel only when the client must reach the server from the public internet:

```bash
tailscale funnel 8000
```

## Safety Notes

- Always set `HEADLESS_OBSIDIAN_MCP_AUTH_TOKEN` before exposing the server.
- Prefer `--host 127.0.0.1`; do not bind the MCP server directly to `0.0.0.0` unless you have a separate network boundary.
- Treat the tunnel URL as sensitive. Anyone with the URL and bearer token can read or modify the vault through enabled tools.
- Use short-lived tunnels for ad-hoc work and stable named tunnels only when you need a durable endpoint.

## References

- [Cloudflare Quick Tunnels](https://try.cloudflare.com/)
- [Cloudflare Tunnel local management](https://developers.cloudflare.com/tunnel/advanced/local-management/)
- [ngrok HTTP endpoints](https://ngrok.com/docs/universal-gateway/http)
- [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve)
- [Tailscale Funnel](https://tailscale.com/docs/features/tailscale-funnel)
