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
- **Extending** — `Gateway` is a composition root. Every behaviour is a swappable strategy with a
  secure default, changed by a `*_class` attribute or an `__init__` instance:
  `token_provider_class` (`TokenProvider`), `origin_policy_class` (`OriginPolicy`),
  `expiry_policy_class` (`ExpiryPolicy`), `codec_class` (`ProtocolCodec`), `authenticator_class`
  (`Authenticator`), plus `channel_class`/`registry_class`/`settings_class`. Contracts + invariants:
  [docs/extensibility.md](docs/extensibility.md); override points:
  [docs/gateway-library.md](docs/gateway-library.md).
- **Authentication** — who may open a channel is the `Authenticator` seam (default `TokenAuthenticator`
  admits known tokens). Recipes for the token model, a client-supplied token from `localStorage`, and
  username/password: [docs/auth-recipes.md](docs/auth-recipes.md). `create_channel` accepts injected
  `provider_token`/`mcp_token` so an authenticator can honour a client's or a derived token.
- **Configuration** — all `GATEWAY_*` env vars (or `.env`) map to `GatewaySettings`. Lists are comma
  separated (`NoDecode`, not JSON). Numeric limits/timeouts are validated positive and `port` is
  bounded, so a bad value fails at startup. `port` also reads the platform-standard `PORT`
  (`GATEWAY_PORT` wins) so PaaS one-click deploys just work. Table:
  [docs/configuration.md](docs/configuration.md).
- **Runtime, limits & performance** — everything runs on one event loop and is fully async, with no
  blocking IO on request/websocket paths. Every per-request/per-connection hot path is O(1): token
  resolution is a dict lookup, the origin check is a `frozenset` membership, tool dispatch is a dict
  lookup, so it scales to many thousands of channels and connections. Tool validators are compiled
  once at registration, not per call. A channel holds at most one live provider socket (a new
  connection atomically replaces the old). The **capacity** limits (`maximum_channels`, `maximum_tools`,
  `maximum_tool_definition_bytes`, pending calls, remembered sessions, subscriptions,
  `maximum_concurrent_connections`, `tool_call_timeout_seconds`) are operator policy: bounded by a safe
  default, and each accepts an empty value (`None`) to mean unlimited — the enforcement is guarded by
  an `is not None` check, secure by default, unlimited by choice. The two **process-safety** limits
  (`maximum_websocket_message_bytes`, `maximum_json_depth`) are always enforced and cannot be disabled,
  so a single frame can never exhaust memory or the stack. The bundled runner
  (`python -m mcp_gtw.main`) sets the transport frame limit (`ws_max_size`) to
  `maximum_websocket_message_bytes` and applies `maximum_concurrent_connections` — so run it that way
  in production, not bare `uvicorn`. Details: [docs/security.md](docs/security.md).
- **Admin dashboard** — off by default and fully inert when off: the `/admin` and `/admin/stats`
  routes are not registered, and the registry does not even track per-channel creation time (the
  only state that exists solely for the dashboard's `ageSeconds`). `GATEWAY_ADMIN_ENABLED=true`
  registers the routes, gated by `GATEWAY_ADMIN_KEY` (required non-empty — enabling admin with an
  empty or unset key raises `GatewayConfigurationError` at construction). `GATEWAY_ADMIN_PATH` (default
  `/admin`) relocates the dashboard and its `<path>/stats` API off the default so it cannot be guessed
  (a value colliding with a built-in route raises `GatewayConfigurationError`). The stats payload
  carries the version only when `GATEWAY_EXPOSE_VERSION=true`. Details: [docs/admin.md](docs/admin.md).
- **Security** — trust boundaries, the two tokens, origin checks, WebSocket robustness, resource
  limits, and version fingerprinting (the version is off the HTTP surface unless
  `GATEWAY_EXPOSE_VERSION=true`): [docs/security.md](docs/security.md).
- **Browser console** — turn any open page into a provider from DevTools (a `/sessions` subclass, an
  origin `*`, a paste-in snippet), plus the CSP/mixed-content caveats:
  [docs/browser-console.md](docs/browser-console.md).
- **Deployment** — the `Dockerfile` runs production-ready as a non-root process binding `0.0.0.0`
  through the bundled runner. A `render.yaml` Blueprint + README button give one-click deploy; any
  persistent-server host (Render/Railway/Fly/VPS) works, serverless does not. Full guide:
  [docs/deployment.md](docs/deployment.md).
- **Quickstart / MCP clients / testing** — [docs/quickstart.md](docs/quickstart.md),
  [docs/mcp-clients.md](docs/mcp-clients.md), [docs/testing.md](docs/testing.md).

## Key modules (`src/mcp_gtw/`)

- `gateway.py` — the `Gateway` class, the composition root: it wires the strategies below, is the
  FastAPI app factory, CORS, routes (`/mcp`, `/provider`, `/health`, `/`, `/logo.svg`, optional
  admin at `GATEWAY_ADMIN_PATH`), the `/mcp` ASGI wrapper, the `/provider` websocket pump, the admin dashboard, and the
  lifespan (session manager + reaper + `serve`). Endpoints delegate every decision to a strategy.
- `channel.py` — `Channel`: attach/detach a provider, compile/replace the registries (tools,
  resources, resource templates, prompts), relay a request to the provider (correlate a `Future` over
  the WebSocket, timed out by `call_timeout_seconds(method, params)` — overridable per tool, `None`
  disables it), run reverse `call`s against the client, fan out notifications (progress, logging,
  resource-updated), validate output, notify MCP sessions, and replay client subscriptions to a
  reconnected provider (`resync_subscriptions`).
- `registry.py` — `ChannelRegistry`: create/remove/resolve channels (create accepts injected tokens),
  `admin_channels`; delegates token minting to a `TokenProvider` and deadline math to an `ExpiryPolicy`.
- `authenticator.py` — `Authenticator` + default `TokenAuthenticator` (maps a connection to a channel
  or denies it); `extract_bearer_token` lives here.
- `tokens.py` — `TokenProvider` + default `SecretsTokenProvider` (generate + constant-time compare).
- `origin.py` — `OriginPolicy` + default `ListOriginPolicy` (list membership + `*`).
- `expiry.py` — `ExpiryPolicy` + default `TtlExpiryPolicy` (offline-TTL reclamation math).
- `codec.py` — `ProtocolCodec` + default `JsonProtocolCodec` (depth-bounded JSON parse).
- `protocol.py` — private message builders and the wire vocabulary constants (no parsing).
- `compiled_tool.py` / `pending_request.py` / `json_websocket.py` — the channel's value types.
- `config.py` — `GatewaySettings` (pydantic-settings, env prefix `GATEWAY_`).
- `listeners.py` — `GatewayListener` hook interface that `Gateway` implements.
- `main.py` — the bundled runner: builds the default `Gateway().create_app()` and runs uvicorn with
  the transport limits (`ws_max_size`, `limit_concurrency`). This is the production entrypoint.

## Module organization

- **One class per module.** Each strategy (its abstract base + the shipped default), each value type
  (`CompiledTool`, `PendingRequest`, `JsonWebSocket`) and each domain class lives in its own file.
  Strategies get an abstract base; value types are plain dataclasses/`Protocol` with no base.
- `errors.py` is the exceptions module (the whole hierarchy lives there).
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

- `.env.example` lists every `GATEWAY_*` setting (commented, with defaults). When you add or change a
  setting in `config.py`, update `.env.example` and `docs/configuration.md` in the same change so the
  three never drift.
- The home page reads `web/index.html` at import and `str.format`s `{name}`/`{initial}` — keep that
  file free of other `{`/`}`. `web/admin.html` is served raw (its JS braces are fine).
- Package data (`web/*.html`, `web/logo.svg`, `py.typed`) must ship in the wheel — hatchling includes
  everything under the package.
