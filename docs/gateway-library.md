# Gateway library

`mcp_gtw` is a generic, subclassable core. You build a real application by subclassing `Gateway`
and overriding what you need. It knows nothing about any domain.

## Classes

| Class | Module | Purpose |
| --- | --- | --- |
| `Gateway` | `gateway` | The application and composition root. Subclass and override to add behavior. |
| `GatewaySettings` | `config` | Configuration (pydantic-settings). |
| `Channel` | `channel` | One session: provider connection, tools, pending calls. |
| `ChannelRegistry` | `registry` | Owns channels and resolves tokens. |
| `TokenProvider` | `tokens` | Mints and compares tokens (default `SecretsTokenProvider`). |
| `OriginPolicy` | `origin` | Allows or denies an `Origin` header (default `ListOriginPolicy`). |
| `ExpiryPolicy` | `expiry` | Decides when an idle channel is reclaimed (default `TtlExpiryPolicy`). |
| `ProtocolCodec` | `codec` | Parses an untrusted provider frame (default `JsonProtocolCodec`). |
| `Authenticator` | `authenticator` | Maps a connection to a channel or denies it (default `TokenAuthenticator`). |
| `GatewayListener` | `listeners` | The lifecycle hook interface that `Gateway` implements. |

Every behaviour above is a swappable strategy — the full contract of each, and how to swap it, is in
[extensibility.md](extensibility.md). Because every `__init__.py` is empty, import from the submodule:

```python
from mcp_gtw.gateway import Gateway
from mcp_gtw.channel import Channel
from mcp_gtw.config import GatewaySettings
```

## The `Gateway` class

```python
from mcp_gtw.gateway import Gateway

gateway = Gateway()          # uses GatewaySettings() from the environment
app = gateway.create_app()   # a ready to serve FastAPI application
```

`create_app` wires CORS, the routes (`/mcp`, `/provider`, `/health`, `/`) and the lifespan (the MCP
session manager, a channel reaper and your `serve` background tasks).

### Class attributes to override

| Attribute | Default | Purpose |
| --- | --- | --- |
| `settings_class` | `GatewaySettings` | Configuration class to instantiate. |
| `registry_class` | `ChannelRegistry` | Registry implementation. |
| `channel_class` | `Channel` | Channel implementation (subclass to store extra per-session state). |
| `token_provider_class` | `SecretsTokenProvider` | How tokens are minted and compared. |
| `origin_policy_class` | `ListOriginPolicy` | How an `Origin` header is admitted. |
| `expiry_policy_class` | `TtlExpiryPolicy` | When an idle channel is reclaimed. |
| `codec_class` | `JsonProtocolCodec` | How an untrusted provider frame is parsed. |
| `authenticator_class` | `TokenAuthenticator` | How a connection maps to a channel or is denied. |
| `mcp_server_name` | `"mcp-gtw"` | The MCP server identifier. |

Each can also be passed as a built instance to `Gateway(...)` for dependency injection
(`tokens=`, `codec=`, `provider_origins=`, `mcp_origins=`, `expiry_policy=`, `registry=`,
`authenticator=`). Contracts and examples: [extensibility.md](extensibility.md). Authentication
models (token, username/password, client-supplied token): [auth-recipes.md](auth-recipes.md).

### Lifecycle hooks

`Gateway` implements `GatewayListener`, so overriding these methods is how you react to sessions:

```python
class MyGateway(Gateway):
    async def on_channel_created(self, channel): ...
    async def on_channel_removed(self, channel): ...
    async def on_provider_connected(self, channel): ...
    async def on_provider_disconnected(self, channel): ...
```

### Adding routes

Call `super().register_routes(app)` and add your own:

```python
class MyGateway(Gateway):
    def register_routes(self, app):
        super().register_routes(app)
        app.add_api_route("/sessions", self.create_session, methods=["POST"])
```

Override `home` to replace the landing page, and `health` to change the health payload.

### Background tasks

Override `serve` — an async context manager that runs for the lifetime of the app:

```python
import asyncio, contextlib

class MyGateway(Gateway):
    @contextlib.asynccontextmanager
    async def serve(self):
        task = asyncio.create_task(self.background_loop())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
```

## Channels and tokens

Create a session from the gateway. It mints two independent tokens:

```python
channel = await gateway.create_channel(metadata={"name": "Neo"})
channel.mcp_token       # the MCP client authenticates with this
channel.provider_token   # the provider authenticates with this
```

Never share one token across both sides. The registry resolves them back:

- `gateway.registry.resolve_mcp_token(token)`
- `gateway.registry.resolve_provider_token(token)`
- `gateway.registry.remove_channel(channel_id)` closes the channel and fires `on_channel_removed`.

To store extra per-session state, subclass `Channel` and set `channel_class`, or keep a store keyed
by `channel.channel_id` and populate it from `on_provider_connected`.

## What happens on a tool call

`Channel.execute_tool` is called by the MCP handlers. It:

1. rejects unknown tools with an error result,
2. validates the arguments against **that channel's** input schema,
3. refuses if the provider is offline or too many calls are pending,
4. sends a `request` (`tools/call`) over the WebSocket and awaits a `Future`,
5. applies the timeout, cancelling the provider call if it expires,
6. validates the returned `structuredContent` against the output schema,
7. resolves the original `tools/call` with a `CallToolResult`.

Input validation lives in the channel because a single MCP server serves every channel — see
[architecture](architecture.md).

### Per-call timeouts

The timeout for a relayed call is resolved by `Channel.call_timeout_seconds(method, params)`, which by
default returns `tool_call_timeout_seconds`. Override it on a `channel_class` to vary the timeout per
tool (or per resource/prompt) without touching the global setting. Return `None` for **no timeout** —
the call then waits until the provider responds or disconnects:

```python
class MyChannel(Channel):
    def call_timeout_seconds(self, method, params):
        if method == "tools/call" and params.get("name") == "render_video":
            return None          # wait as long as it takes

        return super().call_timeout_seconds(method, params)

class MyGateway(Gateway):
    channel_class = MyChannel
```

The `GATEWAY_TOOL_CALL_TIMEOUT_SECONDS` setting is validated `> 0`, so the global default is always
finite — only an override can opt a specific call out of the timeout.

## A complete extension

A full extension is a single `Gateway` subclass: react to providers in `on_provider_connected`,
run any background work in `serve`, and add your own HTTP and WebSocket routes in `register_routes`.
Point your provider at `/provider` and your MCP client at `/mcp`, and the gateway relays every
`tools/call` between them.

## Auto-connecting on page load

The common case is a page that opens its own connection and publishes its tools the moment a visitor
arrives, with no button to click. Mint a channel while serving the page and inject the provider URL,
so the browser connects immediately:

```python
from fastapi.responses import HTMLResponse
from starlette.requests import Request

class MyGateway(Gateway):
    async def home(self, request: Request) -> HTMLResponse:
        channel = await self.create_channel()
        scheme = "wss" if request.url.scheme == "https" else "ws"
        provider_url = f"{scheme}://{request.url.netloc}/provider?token={channel.provider_token}"
        return HTMLResponse(PAGE.format(provider_url=provider_url, mcp_token=channel.mcp_token))
```

`Request` must be imported at module level: with `from __future__ import annotations` the annotation
is a string that FastAPI resolves against the module's globals, so a handler that takes `request`
only receives the injected `Request` when the type is importable there.

The page's module script connects with no user action and registers its tools once, up front:

```javascript
import { McpGtwProvider } from "mcp-gtw-provider";

const provider = new McpGtwProvider({ url: PROVIDER_URL });
provider.registerTool({ name: "greet", description: "Say hello" }, () => "hello");
await provider.connect();
```

That is it — the socket is open and the tools are live before the visitor does anything. Hand the
matching `mcp_token` (and `/mcp/<channel_id>`) to whoever drives the MCP client. Each visitor gets
their own channel, and it is reclaimed automatically after `offline_ttl_seconds` once the tab closes.
