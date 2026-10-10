# Configuration

Settings are read from environment variables (optionally from a `.env` file) through
[pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/). Copy
`.env.example` to `.env` to override defaults locally.

## Gateway settings

Prefix: `GATEWAY_`. Defined in [`config.py`](../src/mcpgtw/config.py).

| Variable | Default | Description |
| --- | --- | --- |
| `GATEWAY_APP_NAME` | `MCP Gateway` | Display name, also shown on the home page. |
| `GATEWAY_APP_VERSION` | installed package version | Version reported to MCP clients. Defaults to the `mcp-gtw` version from package metadata. |
| `GATEWAY_EXPOSE_VERSION` | `false` | Show the version on the HTTP surface (the `/admin` stats and `/openapi.json`). Off by default so the version cannot be fingerprinted anonymously. The authenticated MCP `serverInfo` reports it regardless (protocol requirement). |
| `GATEWAY_HOST` | `127.0.0.1` | Interface the bundled runner (`python -m mcpgtw.main`) binds to. |
| `GATEWAY_PORT` | `8000` | Port the bundled runner binds to. Also reads the platform-standard `PORT` (with `GATEWAY_PORT` taking precedence) so PaaS one-click deploys work unchanged. |
| `GATEWAY_MAXIMUM_CONCURRENT_CONNECTIONS` | empty (unlimited) | Caps simultaneous connections at the server (uvicorn `limit_concurrency`). Empty delegates to the server default. |
| `GATEWAY_ALLOWED_PROVIDER_ORIGINS` | `localhost`/`127.0.0.1` on `8000` | Origins allowed to open the private provider WebSocket. `*` allows any origin. |
| `GATEWAY_ALLOWED_MCP_ORIGINS` | empty | Origins allowed on `/mcp`. Empty accepts native clients (no `Origin`); `*` allows any origin. |
| `GATEWAY_CORS_ALLOW_ORIGINS` | `*` | Origins allowed by CORS on the HTTP endpoints. |
| `GATEWAY_TOOL_CALL_TIMEOUT_SECONDS` | `60` | Default time a relayed call waits for the provider before timing out. Empty means no timeout. Override `Channel.call_timeout_seconds(method, params)` to vary it per tool. |
| `GATEWAY_MAXIMUM_TOOLS` | `128` | Maximum number of tools a provider may register. Empty means unlimited. |
| `GATEWAY_MAXIMUM_TOOL_DEFINITION_BYTES` | `65536` | Maximum serialized size of a single tool definition. Empty means unlimited (still bounded by the WebSocket message size). |
| `GATEWAY_MAXIMUM_WEBSOCKET_MESSAGE_BYTES` | `524288` | Maximum size of a provider WebSocket message. Always enforced (protects the process). The bundled runner also applies this as the transport frame limit (uvicorn `ws_max_size`), so oversized frames are refused before they are buffered. |
| `GATEWAY_MAXIMUM_JSON_DEPTH` | `100` | Maximum container nesting allowed in a provider message. Always enforced (guards against stack exhaustion). |
| `GATEWAY_MAXIMUM_PENDING_CALLS_PER_CHANNEL` | `64` | Concurrent in-flight calls allowed per channel. Empty means unlimited. |
| `GATEWAY_MAXIMUM_MCP_SESSIONS_PER_CHANNEL` | `16` | MCP sessions remembered per channel for `list_changed` notifications. Empty means unlimited. |
| `GATEWAY_MAXIMUM_SUBSCRIPTIONS_PER_CHANNEL` | `1024` | Distinct resource URIs a channel may subscribe to. Empty means unlimited. |
| `GATEWAY_MAXIMUM_CHANNELS` | `10000` | Maximum number of live channels in the registry. Empty means unlimited. |
| `GATEWAY_OFFLINE_TTL_SECONDS` | `300` | Grace period before a channel with no connected provider is reclaimed. |
| `GATEWAY_REAPER_INTERVAL_SECONDS` | `30` | How often the background reaper checks for channels to reclaim. |
| `GATEWAY_MCP_JSON_RESPONSE` | `false` | Return plain JSON instead of SSE on `/mcp`. |
| `GATEWAY_MCP_STATELESS` | `false` | Run the MCP transport in stateless mode: no `Mcp-Session-Id`, so a server restart never invalidates a client's transport (the token still resolves the channel). The trade-off is no out-of-band server push — `tools/list_changed`, resource-updated notifications, and reverse calls (`sampling`/`elicitation`) need a stateful session. Use it when the client only calls tools and re-lists on demand. |
| `GATEWAY_MCP_SESSION_IDLE_TIMEOUT_SECONDS` | `900` | Idle timeout for an MCP session. Applies to stateful mode only; it is ignored when `GATEWAY_MCP_STATELESS` is true (there is no session to expire). |
| `GATEWAY_ADMIN_ENABLED` | `false` | Enable the admin dashboard (`/admin`) and its stats API. Requires `GATEWAY_ADMIN_KEY`. |
| `GATEWAY_ADMIN_KEY` | empty | Required when admin is enabled; the dashboard requires `?key=<value>`. Enabling admin with an empty or unset key raises `GatewayConfigurationError` at startup. |
| `GATEWAY_ADMIN_PATH` | `/admin` | Path the admin dashboard (and its `<path>/stats` API) is served at. Change it to an obscure value so the admin surface is harder to find. Must start with `/`, not be `/`, and not end with `/`; a value that collides with a built-in route raises `GatewayConfigurationError` at startup. |

List values accept a comma separated string, for example:

```dotenv
GATEWAY_ALLOWED_PROVIDER_ORIGINS=http://localhost:8000,https://app.example.com
```

Numeric limits and timeouts must be positive (`offline_ttl_seconds` may be `0`), and `port` must be
`1`–`65535`. An out-of-range value fails validation at startup rather than degrading the server at
runtime.

The capacity limits (`maximum_tools`, `maximum_tool_definition_bytes`, `maximum_pending_calls_per_channel`,
`maximum_mcp_sessions_per_channel`, `maximum_subscriptions_per_channel`, `maximum_channels`,
`maximum_concurrent_connections`, and `tool_call_timeout_seconds`) accept an **empty value to mean
unlimited**, so the operator can remove any policy ceiling. The defaults stay safe, so an untouched
deployment is protected. The two process-safety limits — `maximum_websocket_message_bytes` and
`maximum_json_depth` — are **always enforced** and cannot be disabled, because a single frame must
never be able to exhaust the gateway's memory or stack.

## Loading in code

```python
from mcpgtw.config import GatewaySettings

settings = GatewaySettings()  # reads GATEWAY_* and .env
settings = GatewaySettings(maximum_channels=1000)  # explicit overrides win
```

## Custom settings

Add your own fields by subclassing and pointing the gateway at your class:

```python
from mcpgtw.config import GatewaySettings
from mcpgtw.gateway import Gateway


class MySettings(GatewaySettings):
    welcome_message: str = "hello"


class MyGateway(Gateway):
    settings_class = MySettings
```

## OAuth settings

Lists are comma separated. OAuth requires an application-provided channel access policy; enabling it on the bare runner fails closed. See [OAuth](oauth.md).

| Variable | Default |
| --- | --- |
| `GATEWAY_OAUTH_MODE` | `off` |
| `GATEWAY_OAUTH_RESOURCE_URL` | `` |
| `GATEWAY_OAUTH_AUTHORIZATION_SERVERS` | `` |
| `GATEWAY_OAUTH_REQUIRED_SCOPES` | `mcp:access` |
| `GATEWAY_OAUTH_SUPPORTED_SCOPES` | Empty derives the required scopes; include additional tool scopes here |
| `GATEWAY_OAUTH_ALLOW_STATIC_MCP_TOKENS` | `false` |
| `GATEWAY_OAUTH_ALLOW_LOCALHOST_HTTP` | `false` |
| `GATEWAY_OAUTH_TOKEN_VERIFIER` | `jwt` |
| `GATEWAY_OAUTH_JWKS_URL` | `` |
| `GATEWAY_OAUTH_JWT_ALLOWED_ALGORITHMS` | `RS256,ES256` |
| `GATEWAY_OAUTH_INTROSPECTION_URL` | `` |
| `GATEWAY_OAUTH_INTROSPECTION_CLIENT_ID` | `` |
| `GATEWAY_OAUTH_INTROSPECTION_CLIENT_SECRET` | `` |
| `GATEWAY_OAUTH_HTTP_TIMEOUT_SECONDS` | `5.0` |
| `GATEWAY_OAUTH_MAX_TOKEN_BYTES` | `8192` |
| `GATEWAY_OAUTH_MAX_METADATA_BYTES` | `65536` |
| `GATEWAY_OAUTH_JWKS_CACHE_TTL_SECONDS` | `300.0` |
| `GATEWAY_OAUTH_CLOCK_SKEW_SECONDS` | `30` |
| `GATEWAY_OAUTH_RATE_LIMIT_REQUESTS` | `120` |
| `GATEWAY_OAUTH_RATE_LIMIT_WINDOW_SECONDS` | `1.0` |
| `GATEWAY_OAUTH_RATE_LIMIT_MAXIMUM_KEYS` | `10000` |
| `GATEWAY_OAUTH_MAXIMUM_SESSIONS` | `10000` |

| Embedded setting | Default |
| --- | --- |
| `GATEWAY_OAUTH_EMBEDDED_ISSUER` | First configured authorization server when embedded; otherwise empty |
| `GATEWAY_OAUTH_EMBEDDED_CIMD_ENABLED` | `true` |
| `GATEWAY_OAUTH_EMBEDDED_CIMD_ALLOWED_ORIGINS` | Empty permits public HTTPS origins; optional exact CSV origin allowlist |
| `GATEWAY_OAUTH_EMBEDDED_DCR_ENABLED` | `false` |
| `GATEWAY_OAUTH_EMBEDDED_AUTH_CODE_TTL_SECONDS` | `120` (maximum 300) |
| `GATEWAY_OAUTH_EMBEDDED_ACCESS_TOKEN_TTL_SECONDS` | `900` |
| `GATEWAY_OAUTH_EMBEDDED_REFRESH_TOKEN_TTL_SECONDS` | `2592000` |

Embedded lifetimes are finite positive integers and access lifetime cannot exceed refresh lifetime. The issuer must be a root origin and match the sole authorization server; JWKS must be `<issuer>/oauth/jwks`. Inject a complete authorization server and explicit channel policy; see [OAuth](oauth.md).

### OAuth request budgets

All limits are finite, positive and process-local. HTTP 429 responses include `Retry-After` and
`Cache-Control: no-store`. Verified OAuth requests consume separate IP, client and principal budgets.
Unverified values never create principals. Login attempts additionally consume a username digest
budget, preventing an attacker from bypassing the account budget by changing IP addresses.

| Environment variable | Default | Meaning |
|---|---|---|
| `GATEWAY_OAUTH_RATE_LIMIT_BACKOFF_SECONDS` | `1` | Initial backoff after budget exhaustion |
| `GATEWAY_OAUTH_RATE_LIMIT_MAXIMUM_BACKOFF_SECONDS` | `30` | Maximum progressive backoff, at least the initial delay |
| `GATEWAY_OAUTH_EMBEDDED_RATE_LIMIT_WINDOW_SECONDS` | `60` | Embedded endpoint and identity budget window |
| `GATEWAY_OAUTH_EMBEDDED_BROWSER_REQUESTS` | `30` | Browser actions per trusted client address |
| `GATEWAY_OAUTH_EMBEDDED_TOKEN_REQUESTS` | `120` | Token and revocation requests per address |
| `GATEWAY_OAUTH_EMBEDDED_REGISTRATION_REQUESTS` | `10` | DCR requests per address |
| `GATEWAY_OAUTH_EMBEDDED_METADATA_REQUESTS` | `120` | AS discovery and JWKS requests per address |
| `GATEWAY_OAUTH_EMBEDDED_CIMD_REQUESTS` | `10` | Outbound uncached CIMD lookups |
| `GATEWAY_OAUTH_EMBEDDED_CLIENT_REQUESTS` | `120` | Requests per OAuth client ID |
| `GATEWAY_OAUTH_EMBEDDED_PRINCIPAL_REQUESTS` | `120` | Consent and exchange requests per verified subject |
| `GATEWAY_OAUTH_EMBEDDED_LOGIN_ATTEMPTS` | `10` | Password attempts per normalized username digest |

Every budget is capped by `GATEWAY_OAUTH_RATE_LIMIT_MAXIMUM_KEYS`. Exhausting the key capacity
rejects new keys until expiry. The expiry queue retains at most one entry per tracked key.
