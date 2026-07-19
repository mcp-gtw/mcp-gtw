# Configuration

Settings are read from environment variables (optionally from a `.env` file) through
[pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/). Copy
`.env.example` to `.env` to override defaults locally.

## Gateway settings

Prefix: `GATEWAY_`. Defined in [`config.py`](../src/mcp_gtw/config.py).

| Variable | Default | Description |
| --- | --- | --- |
| `GATEWAY_APP_NAME` | `MCP Gateway` | Display name, also shown on the home page. |
| `GATEWAY_APP_VERSION` | installed package version | Version reported to MCP clients. Defaults to the `mcp-gtw` version from package metadata. |
| `GATEWAY_HOST` | `127.0.0.1` | Interface the bundled runner (`python -m mcp_gtw.main`) binds to. |
| `GATEWAY_PORT` | `8000` | Port the bundled runner binds to. |
| `GATEWAY_MAXIMUM_CONCURRENT_CONNECTIONS` | empty (unlimited) | Caps simultaneous connections at the server (uvicorn `limit_concurrency`). Empty delegates to the server default. |
| `GATEWAY_ALLOWED_PROVIDER_ORIGINS` | `localhost`/`127.0.0.1` on `8000` | Origins allowed to open the private provider WebSocket. `*` allows any origin. |
| `GATEWAY_ALLOWED_MCP_ORIGINS` | empty | Origins allowed on `/mcp`. Empty accepts native clients (no `Origin`); `*` allows any origin. |
| `GATEWAY_CORS_ALLOW_ORIGINS` | `*` | Origins allowed by CORS on the HTTP endpoints. |
| `GATEWAY_TOOL_CALL_TIMEOUT_SECONDS` | `60` | How long a `tools/call` waits for the provider before timing out. |
| `GATEWAY_MAXIMUM_TOOLS` | `128` | Maximum number of tools a provider may register. |
| `GATEWAY_MAXIMUM_TOOL_DEFINITION_BYTES` | `65536` | Maximum serialized size of a single tool definition. |
| `GATEWAY_MAXIMUM_WEBSOCKET_MESSAGE_BYTES` | `524288` | Maximum size of a provider WebSocket message. The bundled runner also applies this as the transport frame limit (uvicorn `ws_max_size`), so oversized frames are refused before they are buffered. |
| `GATEWAY_MAXIMUM_JSON_DEPTH` | `100` | Maximum container nesting allowed in a provider message (guards against stack exhaustion). |
| `GATEWAY_MAXIMUM_PENDING_CALLS_PER_CHANNEL` | `64` | Concurrent in-flight calls allowed per channel. |
| `GATEWAY_MAXIMUM_MCP_SESSIONS_PER_CHANNEL` | `16` | MCP sessions remembered per channel for `list_changed` notifications. |
| `GATEWAY_MAXIMUM_CHANNELS` | `10000` | Maximum number of live channels in the registry. |
| `GATEWAY_OFFLINE_TTL_SECONDS` | `300` | Grace period before a channel with no connected provider is reclaimed. |
| `GATEWAY_REAPER_INTERVAL_SECONDS` | `30` | How often the background reaper checks for channels to reclaim. |
| `GATEWAY_MCP_JSON_RESPONSE` | `false` | Return plain JSON instead of SSE on `/mcp`. |
| `GATEWAY_MCP_STATELESS` | `false` | Run the MCP transport in stateless mode. |
| `GATEWAY_MCP_SESSION_IDLE_TIMEOUT_SECONDS` | `900` | Idle timeout for an MCP session. |
| `GATEWAY_ADMIN_ENABLED` | `false` | Enable the admin dashboard (`/admin`) and its stats API. Requires `GATEWAY_ADMIN_KEY`. |
| `GATEWAY_ADMIN_KEY` | empty | Required when admin is enabled; the dashboard requires `?key=<value>`. Enabling admin without it raises `GatewayConfigurationError` at startup. |

List values accept a comma separated string, for example:

```dotenv
GATEWAY_ALLOWED_PROVIDER_ORIGINS=http://localhost:8000,https://app.example.com
```

Numeric limits and timeouts must be positive (`offline_ttl_seconds` may be `0`), and `port` must be
`1`–`65535`. An out-of-range value fails validation at startup rather than degrading the server at
runtime.

## Loading in code

```python
from mcp_gtw.config import GatewaySettings

settings = GatewaySettings()                       # reads GATEWAY_* and .env
settings = GatewaySettings(maximum_channels=1000)  # explicit overrides win
```

## Custom settings

Add your own fields by subclassing and pointing the gateway at your class:

```python
from mcp_gtw.config import GatewaySettings
from mcp_gtw.gateway import Gateway

class MySettings(GatewaySettings):
    welcome_message: str = "hello"

class MyGateway(Gateway):
    settings_class = MySettings
```
