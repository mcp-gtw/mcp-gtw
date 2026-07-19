# Testing

The library is covered by a unit and integration suite behind a **100% coverage gate** (branch
coverage included). The suite runs in about one second.

## Running

```bash
make test          # quiet run
make coverage      # run behind the 100% gate, prints missing lines, writes coverage.xml
```

The gate is configured in [`pyproject.toml`](../pyproject.toml):

```toml
[tool.coverage.run]
branch = true
source = ["mcp_gtw"]

[tool.coverage.report]
fail_under = 100
```

## Layout

```text
tests/
├── support.py            # a shared FakeWebSocket helper
├── conftest.py           # settings, tool and websocket fixtures
├── test_entrypoints.py   # the uvicorn entrypoint
└── gateway/
    ├── test_config.py    test_security.py   test_protocol.py
    ├── test_channel.py   test_registry.py   test_listeners.py
    └── test_gateway.py   # the Gateway class end to end
```

## Approach

- **Unit tests** exercise the channel and registry directly, including every edge and failure branch:
  timeouts, cancellation, provider replacement, invalid schemas, pending-call limits and reaping.
- **In-process MCP integration** drives the real MCP client (`streamable_http_client`) against the
  ASGI app through an `ASGITransport`, with a fake provider answering the calls — proving the
  full `list_tools` / `call_tool` path.
- **Provider endpoint tests** feed a fake WebSocket through `Gateway.provider_endpoint`, covering
  registration, ping, protocol errors, oversized messages and provider replacement.

## Linting

```bash
make lint          # ruff check + ruff format --check
make format        # apply formatting and safe fixes
```
