# Connecting MCP clients

The gateway exposes a conventional MCP endpoint over Streamable HTTP, so **any** MCP-capable host
works. It is also model agnostic: the host speaks MCP, the model never sees the transport, so it
works the same whether the model is Claude, GPT, Gemini or a local model served by Ollama or LM
Studio.

```text
Endpoint:   http://127.0.0.1:8000/mcp            (token-routed)
       or   http://127.0.0.1:8000/mcp/<channel>  (addressed, token must match)
Header:     Authorization: Bearer <MCP_TOKEN>
```

Create a session in your application to obtain the token (the bare library mints it with
`gateway.create_channel()`). Each connected provider is its own service — see
[architecture](architecture.md#many-services-one-gateway). Keep the provider connected while
you call tools.

Most hosts share one JSON shape and only differ in the key names. The URL and the Bearer header are
always the same.

## CLI agents

**Claude Code**

```bash
claude mcp add --transport http my-gateway http://127.0.0.1:8000/mcp \
  --header "Authorization: Bearer <MCP_TOKEN>"
```

**OpenAI Codex** — add to `~/.codex/config.toml` (streamable HTTP needs a recent Codex):

```toml
[mcp_servers.my-gateway]
url = "http://127.0.0.1:8000/mcp"
bearer_token = "<MCP_TOKEN>"
```

## Removing a server

If the server is already configured and you want to drop it, remove it by the name you registered it
under. In Claude Code:

```bash
claude mcp remove my-gateway
```

For config-file hosts (Cursor, Windsurf, Cline, Continue, LM Studio, Antigravity) delete the entry
from their `mcpServers` object. For Codex, delete the `[mcp_servers.my-gateway]` block from
`~/.codex/config.toml`.

## Skipping the approval prompt

Hosts ask before running a tool by default. Approve the server once so its tools run without
prompting.

**Claude Code** — allowlist it in `.claude/settings.json` (project) or `~/.claude/settings.json`
(user):

```json
{
  "permissions": {
    "allow": ["mcp__my-gateway"]
  }
}
```

`mcp__<server>` approves every tool from that server (use the name you registered it under). Use
`mcp__<server>__<tool>` to approve a single tool. You can also click **Always allow** the first time
you are prompted, or run `/permissions`.

**Other hosts** expose the same idea under different names — Cursor's auto-run, Cline's auto-approve,
and Codex's `approval_policy` (set it to `never` in `~/.codex/config.toml`). Marking read-only tools
with `annotations.readOnlyHint` when you register them also lets some hosts run them without asking.

## Editors and IDEs

**Cursor** — `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "my-gateway": {
      "url": "http://127.0.0.1:8000/mcp",
      "headers": { "Authorization": "Bearer <MCP_TOKEN>" }
    }
  }
}
```

**Windsurf** — `~/.codeium/windsurf/mcp_config.json` uses `serverUrl` instead of `url`:

```json
{
  "mcpServers": {
    "my-gateway": {
      "serverUrl": "http://127.0.0.1:8000/mcp",
      "headers": { "Authorization": "Bearer <MCP_TOKEN>" }
    }
  }
}
```

**Cline** (VS Code) — add `"type": "streamableHttp"` to the same shape.

**Continue**, **Google Antigravity** and other MCP hosts accept the standard `mcpServers` object with
`url` and `headers` — paste the endpoint and the Bearer header into their MCP settings.

## Desktop apps with local models

**LM Studio** — `mcp.json`:

```json
{
  "mcpServers": {
    "my-gateway": {
      "url": "http://127.0.0.1:8000/mcp",
      "headers": { "Authorization": "Bearer <MCP_TOKEN>" }
    }
  }
}
```

**Claude Desktop** — add a custom connector with the URL and token, or use the generic JSON above.

Because the gateway is a normal MCP server, any tool that drives a local model through MCP (LM
Studio, or an Ollama model wired into Cursor, Cline or Continue) reaches the provider-registered tools
exactly like a cloud model does.

## MCP Inspector

```bash
npx -y @modelcontextprotocol/inspector
```

Choose the **Streamable HTTP** transport, set the URL to `http://127.0.0.1:8000/mcp` and add the
`Authorization` header. The provider page must be connected before the tools appear.

## From Python

The repository's tests drive the gateway with the official client — a compact reference:

```python
import httpx
from httpx import ASGITransport
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

http = httpx.AsyncClient(
    base_url="http://127.0.0.1:8000",
    headers={"Authorization": f"Bearer {mcp_token}"},
    follow_redirects=True,
)
async with streamable_http_client("http://127.0.0.1:8000/mcp", http_client=http) as (r, w, _):
    async with ClientSession(r, w) as session:
        await session.initialize()
        tools = await session.list_tools()
        result = await session.call_tool(tools.tools[0].name, {})
```

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `401 Unauthorized` | Missing or wrong Bearer token. |
| `404 Unknown MCP service` | The `/mcp/<channel>` path does not match the token's channel. |
| Tools list is empty | The provider page is not connected, or registered no tools. |
| Tool call times out | The handler never returned, or the provider disconnected or its tab is closed. |
| `403 Origin not allowed` | An `Origin` header is set and not in `GATEWAY_ALLOWED_MCP_ORIGINS`. |
