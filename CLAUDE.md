# CLAUDE.md

Guidance for working in this repository.

## How to use this file

**CLAUDE.md is a map, not a copy.** Each subject gets a one-line essence here and a pointer to the
`docs/*.md` that owns the full detail. Never duplicate doc content into this file — when the code
changes, update the doc and keep the pointer accurate. The docs are the source of truth for
behaviour. This file is the source of truth for the conventions and repo mechanics that have no doc
(module layout, code style, commands, gotchas). If a subject has a doc, read the doc.

## What this is

`mcp-gtw` is a generic, installable [Model Context Protocol](https://modelcontextprotocol.io)
gateway. It publishes a real MCP endpoint over Streamable HTTP whose capabilities are **registered
and executed by connected providers** (a browser app or any WebSocket client) over a private
WebSocket. The gateway never knows them in advance — a provider connects, publishes its own MCP
tools, resources, resource templates and prompts, and runs their handlers; it can also serve
completion, logging, progress, and reverse calls (sampling, elicitation). The gateway only registers,
publishes and routes.

It is a **library**: you build a real app by subclassing `Gateway`, never by editing this package.
This repo is published to PyPI as `mcp-gtw` (the import package stays `mcp_gtw`).

## Subjects (essence + where the detail lives)

- **How it works / architecture** — public transport `MCP Client ⇄ Streamable HTTP ⇄ /mcp`. Private
  transport `Gateway ⇄ WebSocket ⇄ provider (/provider)`. A **channel** is one session (one provider,
  its registries, its pending calls) holding two tokens: `mcp_token` (client) and `provider_token`
  (provider). One MCP `Server` + one `StreamableHTTPSessionManager` serve every channel, resolved per
  request by Bearer token on the ASGI scope, with per-channel input validation. Full flows and
  diagrams: [docs/architecture.md](docs/architecture.md).
- **Provider protocol** — a JSON-RPC-shaped relay of MCP: gateway→provider `request`/`cancel`/
  `response`, provider→gateway `register`/`result`/`call`/`notify`, plus `hello.ack`/`ack`/`ping`/
  `pong`/`protocol.error`, version `mcp-gtw-provider/1`. See
  [docs/provider-protocol.md](docs/provider-protocol.md).
- **Provider SDK** — the JavaScript side is the [`mcp-gtw-provider`](https://github.com/mcp-gtw/mcp-gtw-provider)
  npm package (`registerTool`/`registerResource`/`registerResourceTemplate`/`registerPrompt`,
  `onComplete`/`onSubscribe`, `notifyResourceUpdated`, `log`, `requestSampling`/`requestElicit`).
  Usage guide in [docs/provider-sdk.md](docs/provider-sdk.md).
- **Extending** — subclass `Gateway`, override the hooks, swap `channel_class`/`registry_class`/
  `settings_class`. Every override point: [docs/gateway-library.md](docs/gateway-library.md).
- **Configuration** — all `GATEWAY_*` env vars (or `.env`) map to `GatewaySettings`. Lists are comma
  separated (`NoDecode`, not JSON). Numeric limits/timeouts are validated positive and `port` is
  bounded, so a bad value fails at startup. Table: [docs/configuration.md](docs/configuration.md).
- **Runtime, limits & performance** — everything runs on one event loop and is fully async, with no
  blocking IO on request/websocket paths. Tool validators are compiled once at registration, not per
  call. A channel holds at most one live provider socket (a new connection atomically replaces the
  old); channels are capped by `maximum_channels` (checked before allocation); every externally-fed
  collection (pending calls, remembered sessions, tools) is bounded. The bundled runner
  (`python -m mcp_gtw.main`) sets the transport frame limit (`ws_max_size`) to
  `maximum_websocket_message_bytes` and applies `maximum_concurrent_connections` — so run it that way
  in production, not bare `uvicorn`. Details: [docs/security.md](docs/security.md).
- **Admin dashboard** — off by default. `GATEWAY_ADMIN_ENABLED=true` registers `/admin` and
  `/admin/stats`, gated by `GATEWAY_ADMIN_KEY` (required — enabling admin without it raises
  `GatewayConfigurationError` at construction). Details: [docs/admin.md](docs/admin.md).
- **Security** — trust boundaries, the two tokens, origin checks, WebSocket robustness, resource
  limits: [docs/security.md](docs/security.md).
- **Browser console** — turn any open page into a provider from DevTools (a `/sessions` subclass, an
  origin `*`, a paste-in snippet), plus the CSP/mixed-content caveats:
  [docs/browser-console.md](docs/browser-console.md).
- **Quickstart / MCP clients / testing / deployment** — [docs/quickstart.md](docs/quickstart.md),
  [docs/mcp-clients.md](docs/mcp-clients.md), [docs/testing.md](docs/testing.md),
  [docs/deployment.md](docs/deployment.md).

## Key modules (`src/mcp_gtw/`)

- `gateway.py` — the `Gateway` class: FastAPI app factory, CORS, routes (`/mcp`, `/provider`,
  `/health`, `/`, `/logo.svg`, optional `/admin`), the `/mcp` ASGI wrapper, the `/provider` websocket
  pump, the admin dashboard, and the lifespan (session manager + reaper + `serve`).
- `channel.py` — `Channel`: attach/detach a provider, compile/replace the registries (tools,
  resources, resource templates, prompts), relay a request to the provider (correlate a `Future` over
  the WebSocket with timeout), run reverse `call`s against the client, fan out notifications
  (progress, logging, resource-updated), validate output, notify MCP sessions.
- `registry.py` — `ChannelRegistry`: create/remove/resolve channels, expiry, `admin_channels`.
- `protocol.py` — private message builders and `decode_message` (depth-bounded JSON parse).
- `config.py` — `GatewaySettings` (pydantic-settings, env prefix `GATEWAY_`).
- `helpers/security.py` — token generation, bearer extraction, origin check, constant-time compare.
- `listeners.py` — `GatewayListener` hook interface that `Gateway` implements.

## Module organization

- One public class per module, plus its small private support types (`channel.py` keeps
  `CompiledTool`, `PendingRequest`, `JsonWebSocket`).
- `errors.py` is the exceptions module (the whole hierarchy lives there).
- Pure utility functions live under `helpers/`.
- Every `__init__.py` is empty, so import from the concrete submodule.

## Conventions

- Managed with `uv`. Ruff with `line-length = 100`, formatter is the source of truth.
- **100% branch coverage is a hard gate** (`fail_under = 100`). Every change keeps it at 100%.
- `__init__.py` files are **empty** — import from submodules (`from mcp_gtw.gateway import Gateway`).
- Code and comments are in **English**. Comments are **rare** — only for genuinely non-obvious intent.
  No narrating comments, no artificial section separators, no semicolons splitting sentences.
- **Separate blocks with a blank line.** A compound block (`if`/`for`/`while`/`try`/`with`/`def`/
  `class`) gets a blank line between it and the adjacent statement, so each block reads as its own
  unit. Never stack blocks directly on top of each other.
- **No legacy, no back-compat, no fallbacks.** Build the final version and refactor freely. Do not add
  checks that only exist because something used to be different.
- Prefer single-line signatures and calls where they fit the line length.

## Supported Python

- `requires-python = ">=3.12"`. Supported versions are **3.12, 3.13, 3.14**.
- `.python-version` pins **3.12** for local `uv` and the Docker image (`python:3.12-slim`).
- CI (`.github/workflows/ci.yml`) runs the full `make install` + `make lint` + `make coverage` matrix
  across all three versions; `UV_PYTHON` selects the interpreter per matrix leg.

## Commands

```bash
make install       # uv sync --extra dev
make lint          # ruff check + ruff format --check
make format        # apply formatting and safe fixes
make test          # pytest
make coverage      # pytest behind the 100% gate
make version v=X.Y.Z  # rewrite the pyproject.toml version (validates semver)
make build         # uv build (wheel + sdist)
make run           # serve the bare gateway on 127.0.0.1:8000
```

## Versioning and releasing

`pyproject.toml` is the single source of the version: `GatewaySettings.app_version` reads it back at
runtime through `importlib.metadata.version("mcp-gtw")`, so never hardcode a version elsewhere.
Bump with `make version v=X.Y.Z` (semver `X.Y.Z`, validated), then push a matching `v<version>` tag.
`.github/workflows/release.yml` verifies the tag equals the `pyproject.toml` version, runs lint + the
coverage gate, builds, and `uv publish --trusted-publishing always`.

Publishing uses PyPI **Trusted Publishing** (OIDC) — no `PYPI_API_TOKEN` secret. The workflow grants
`id-token: write`. PyPI supports a **pending publisher**, so configure it before the first release
(pypi.org → Account → Publishing → Add a pending publisher): PyPI project `mcp-gtw`, owner
`mcp-gtw`, repository `mcp-gtw`, workflow `release.yml`. Pushing the first `v<version>` tag then
creates the project token-lessly.

## Documentation policy

Docs must stay consistent with the code. When you change a setting, default, endpoint, module path,
or behaviour, update the affected `docs/*.md`, `README.md`, and the pointer in this file in the same
change. Treat a doc that describes something the code no longer does as a bug.

## Gotchas

- `.env` and `.env.example` are under a hard permission deny rule here (Read, Edit, Bash, and Write
  all fail). The file is effectively immutable in this environment — make `config.py` and
  `docs/configuration.md` the source of truth and flag any drift to the user.
- The home page reads `web/index.html` at import and `str.format`s `{name}`/`{initial}` — keep that
  file free of other `{`/`}`. `web/admin.html` is served raw (its JS braces are fine).
- Package data (`web/*.html`, `web/logo.svg`, `py.typed`) must ship in the wheel — hatchling includes
  everything under the package.
