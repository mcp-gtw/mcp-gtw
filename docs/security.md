# Security

The gateway is designed to be exposed to untrusted MCP clients and untrusted providers. This page
describes the trust boundaries and the controls that enforce them.

## Two tokens, two sides

Every channel has two independent, high-entropy tokens (`secrets.token_urlsafe`):

- the **MCP token** authorizes listing and calling tools on `/mcp`,
- the **provider token** authorizes registering tools and answering calls on `/provider` (and any
  application routes your subclass adds, such as command or stream endpoints).

They are never interchangeable and never shared. Tokens are resolved by constant-time dictionary
lookup on random values, so there is nothing to brute force in practice.

## Transport and origin

- The MCP endpoint requires `Authorization: Bearer <mcp_token>`; anything else returns `401`.
- When an `Origin` header is present it must be in `GATEWAY_ALLOWED_MCP_ORIGINS`, otherwise `403`.
  Native clients without an `Origin` are accepted, matching the Streamable HTTP guidance.
- The provider WebSocket requires a valid provider token and an allowed origin
  (`GATEWAY_ALLOWED_PROVIDER_ORIGINS`), otherwise it closes with `1008`.
- CORS is applied to the HTTP endpoints via `GATEWAY_CORS_ALLOW_ORIGINS`.
- In production, terminate TLS so tokens and payloads travel over HTTPS and WSS. Bind to `127.0.0.1`
  for local use rather than `0.0.0.0`.

## Input and output validation

- Tool definitions are validated as MCP tools with valid JSON Schema before being accepted.
- Arguments are validated **per channel** against that channel's own `inputSchema` before a call is
  forwarded — a single shared server never mixes schemas between sessions.
- If a tool declares an `outputSchema`, the provider's `structuredContent` is validated against it
  before the result reaches the client.

## WebSocket robustness

The private WebSocket only ever accepts bounded, well-formed input:

- Only text frames are processed. A binary frame is answered with a `protocol.error` and never
  crashes the connection.
- Message nesting is bounded (`GATEWAY_MAXIMUM_JSON_DEPTH`) **before** the payload is parsed, so a
  deeply nested JSON string cannot exhaust the interpreter stack.
- A malformed message (bad JSON, non-object, unknown type, invalid tool definition) yields a single
  `protocol.error` and the connection keeps serving; it is never a `500` or a dropped process.
- Oversized messages close the socket with `1009`; a replacement connection closes the previous one
  with `1012`. The bundled runner sets the transport frame limit (uvicorn `ws_max_size`) to
  `GATEWAY_MAXIMUM_WEBSOCKET_MESSAGE_BYTES`, so an oversized frame is refused during reassembly rather
  than buffered whole and then rejected. The application-level check remains as defense in depth.
- Every send to a provider is serialized through one lock, so frames from concurrent tasks (a
  `request` and a `protocol.error`) can never interleave on the wire. A transport send failure is
  surfaced as an offline error, never a raw exception that could escape a request or background task.

## Resource limits

Every limit is configurable (see [configuration](configuration.md)) and enforced by the gateway:

| Limit | Setting |
| --- | --- |
| Tools per provider | `GATEWAY_MAXIMUM_TOOLS` |
| Tool definition size | `GATEWAY_MAXIMUM_TOOL_DEFINITION_BYTES` |
| WebSocket message size | `GATEWAY_MAXIMUM_WEBSOCKET_MESSAGE_BYTES` |
| JSON nesting depth | `GATEWAY_MAXIMUM_JSON_DEPTH` |
| In-flight calls per channel | `GATEWAY_MAXIMUM_PENDING_CALLS_PER_CHANNEL` |
| Remembered MCP sessions per channel | `GATEWAY_MAXIMUM_MCP_SESSIONS_PER_CHANNEL` |
| Live channels | `GATEWAY_MAXIMUM_CHANNELS` |
| Call duration | `GATEWAY_TOOL_CALL_TIMEOUT_SECONDS` |
| Offline channel grace | `GATEWAY_OFFLINE_TTL_SECONDS` (reaped in the background) |

Oversized messages close the socket with `1009`. Exceeding the pending-call limit returns an error
result rather than queuing unbounded work. A channel lives while its provider WebSocket is connected
and is reclaimed once it has had no provider for the offline grace, so abandoned sessions never
accumulate. The reaper survives transient failures, so the reclamation always eventually happens.

## How a limit surfaces to the caller

Nothing fails silently to the party that can act on it. What the caller sees depends on which side
hit the limit:

| Condition | Setting | What the caller receives |
| --- | --- | --- |
| Unknown tool | — | MCP tool result `isError: true`, text `Unknown tool: <name>` |
| Arguments fail the tool's `inputSchema` | — | `isError: true`, `Input validation error for '<name>': …` |
| Provider is offline | — | `isError: true`, `The channel provider is offline` |
| Too many in-flight calls | `GATEWAY_MAXIMUM_PENDING_CALLS_PER_CHANNEL` | `isError: true`, `Too many pending calls for this channel` |
| Tool call exceeds its deadline | `GATEWAY_TOOL_CALL_TIMEOUT_SECONDS` | `isError: true`, `Tool execution timed out after N seconds` |
| Result fails the tool's `outputSchema` | — | `isError: true`, `Output validation error for '<name>': …` |
| Send to the provider fails mid-call | — | `isError: true`, `Failed to send to the channel provider` |
| Missing or invalid MCP token | — | HTTP `401` |
| Wrong channel id in the path | — | HTTP `404` |
| Disallowed `Origin` on `/mcp` | `GATEWAY_ALLOWED_MCP_ORIGINS` | HTTP `403` |
| Server concurrency cap reached | `GATEWAY_MAXIMUM_CONCURRENT_CONNECTIONS` | HTTP `503` |
| Too many tools / oversized or duplicate / invalid schema | `GATEWAY_MAXIMUM_TOOLS`, `GATEWAY_MAXIMUM_TOOL_DEFINITION_BYTES` | `protocol.error` frame to the **provider**; the tools are not registered |
| Oversized WebSocket frame | `GATEWAY_MAXIMUM_WEBSOCKET_MESSAGE_BYTES` | provider socket closed with `1009` (and refused at the transport) |
| Bad provider token or disallowed origin | `GATEWAY_ALLOWED_PROVIDER_ORIGINS` | provider handshake refused, closed with `1008` |
| Registry is full | `GATEWAY_MAXIMUM_CHANNELS` | `ChannelCapacityError` raised into your channel-creation route, which maps it to a response (the demo returns HTTP `429`) |
| Too many remembered MCP sessions | `GATEWAY_MAXIMUM_MCP_SESSIONS_PER_CHANNEL` | silent — the oldest session simply stops receiving `tools/list_changed` |

So an MCP client always learns of a failed **call** through an `isError` result or an HTTP status, a
**provider** learns of a rejected registration through a `protocol.error` frame, and only the
session-remembering cap is invisible (by design, it never affects a live call).

## Connection model

A channel holds at most one live provider socket: a new connection atomically replaces the previous
one, so a valid token cannot accumulate sockets. The number of channels is capped by
`GATEWAY_MAXIMUM_CHANNELS`, enforced before allocation. An invalid token or disallowed origin is
refused during the WebSocket handshake, before `accept`. The gateway does not otherwise cap the number
of simultaneous connections itself — set `GATEWAY_MAXIMUM_CONCURRENT_CONNECTIONS` (applied as the
server's `limit_concurrency`) and rate-limit at the reverse proxy to bound connection floods.

## No arbitrary execution

The gateway never runs arbitrary code. It only routes calls to tools the provider explicitly
registered. Avoid publishing dangerous generic tools (`execute_javascript`, `read_all_local_storage`
and the like) and prefer specific, well-scoped tools.

## Authoritative domain rules

Schema validation is not authorization. For any competitive or multi-user domain, the provider's tool
list grants nothing: the authoritative server must re-check identity, ownership, range, cooldowns and
every other rule. Keep the provider handler a thin forwarder and enforce the rules server-side.

## Prompt injection

Tool descriptions are attacker-controlled content. In a multi-user deployment: authenticate
providers, cap description sizes, audit registrations, show users the published tools and allow
revoking a channel at any time.

## Deployment considerations

These are inherent to the design. Plan for them when the provider is not fully trusted.

- **Provider token in the query string.** Browsers cannot set an `Authorization` header on a
  WebSocket, so the provider token travels in the URL. Access logs may capture it — configure your
  proxy to strip query strings from logs, prefer short-lived per-session tokens, and always use WSS.
- **DNS rebinding.** The `/mcp` and `/provider` endpoints require a high-entropy token an attacker
  page cannot know, and the provider WebSocket also enforces an allowed `Origin`. Bind to `127.0.0.1`
  locally so a rebinding page still cannot present a valid token.
- **Provider-supplied JSON Schema.** Input validation runs the provider's `inputSchema` against
  client arguments with the standard `jsonschema` library, which uses backtracking regular
  expressions. A malicious provider could craft a catastrophic pattern (ReDoS). When providers are
  untrusted, review or constrain the schemas they may register.
- **Event loop.** Validation and result serialization are synchronous. Payloads are bounded by the
  size and depth limits above, but very expensive provider schemas or results still run on the event
  loop. Keep the limits tight for multi-tenant deployments.
