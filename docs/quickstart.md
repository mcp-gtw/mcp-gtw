# Quick start

## Prerequisites

- Python 3.12 or newer
- [`uv`](https://docs.astral.sh/uv/) (recommended)
- Any MCP client

## Install

```bash
make install
```

This runs `uv sync --extra dev`, creating a virtual environment with the runtime and development
dependencies.

## Run the bare gateway

```bash
make run
```

The generic gateway is served on <http://127.0.0.1:8000>. Useful endpoints:

- `/` — a minimal home page,
- `/mcp` — the MCP Streamable HTTP endpoint,
- `/provider` — the private provider WebSocket,
- `/health` — a status check.

On its own it publishes no tools until a provider connects and registers some. To build a
real application, subclass `Gateway` — see the [Gateway library](gateway-library.md) guide.

## Create a session

Sessions are created by your application, not by the bare library. A minimal helper:

```python
from mcp_gtw.gateway import Gateway

gateway = Gateway()
channel = await gateway.create_channel(metadata={"name": "Neo"})
print(channel.mcp_token)      # give this to the MCP client
print(channel.provider_token)  # give this to the provider
```

Expose that behind an HTTP route in your subclass (see [gateway library](gateway-library.md)).

## Connect an MCP client

```text
Endpoint:  http://127.0.0.1:8000/mcp
Header:    Authorization: Bearer <MCP_TOKEN>
```

See [MCP clients](mcp-clients.md) for Claude Code, generic configuration and the Inspector.

## Check health

```bash
curl http://127.0.0.1:8000/health
```

```json
{ "status": "ok", "channels": 0, "providersConnected": 0 }
```
