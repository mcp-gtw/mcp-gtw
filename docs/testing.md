# Testing

The library is covered by a unit, integration, security and stress suite behind a **100% coverage
gate** (branch coverage included). The suite runs in a few seconds.

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
├── support.py            # shared FakeWebSocket / FakeProviderWebSocket helpers
├── conftest.py           # settings, tool and websocket fixtures
├── test_entrypoints.py   # the uvicorn entrypoint
└── gateway/
    ├── test_config.py         test_protocol.py       test_codec.py
    ├── test_tokens.py         test_origin.py         test_expiry.py
    ├── test_authenticator.py  test_listeners.py      test_registry.py
    ├── test_channel.py        test_gateway.py        # the Gateway class end to end
    ├── test_extensibility.py  # every strategy swapped by class attribute and by injection
    ├── test_auth_recipes.py   # the own-token and username/password models, with abuse cases
    ├── test_security.py       # token bypass / confusion / cross-channel attempts
    └── test_concurrency.py    test_stress.py         # races and thousands-of-channels scale
```

## Approach

- **Unit tests** exercise the channel and registry directly, including every edge and failure branch:
  timeouts, cancellation, provider replacement, invalid schemas, pending-call limits and reaping.
- **Strategy tests** cover each swappable default and its abstract contract, and prove that a custom
  `TokenProvider` / `OriginPolicy` / `ExpiryPolicy` / `ProtocolCodec` / `Authenticator` / registry /
  channel takes effect end to end, both by class attribute and by `__init__` injection.
- **Auth-recipe tests** implement the own-token (client-supplied UUID, upserted) and username/password
  models and attack them: malformed tokens, upsert floods, user enumeration, malformed login bodies,
  and token reuse.
- **Security tests** attempt to bypass or confuse the two tokens — a provider token on `/mcp`, an mcp
  token on `/provider`, a removed channel's tokens, empty tokens, a cross-channel path.
- **In-process MCP integration** drives the real MCP client (`streamable_http_client`) against the
  ASGI app through an `ASGITransport`, with a fake provider answering the calls — proving the
  full `list_tools` / `call_tool` path.
- **Provider endpoint tests** feed a fake WebSocket through `Gateway.provider_endpoint`, covering
  registration, ping, protocol errors, oversized text and binary messages and provider replacement.
- **Concurrency and stress tests** run thousands of channels and concurrent create/remove churn,
  asserting unique tokens, a consistent registry, and that `admin_stats` stays correct under load.

## Linting

```bash
make lint          # ruff check + ruff format --check
make format        # apply formatting and safe fixes
```
