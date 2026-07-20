# Provider protocol

The gateway and the provider speak a small, private, versioned protocol over the WebSocket at
`/provider`. It is **not** MCP and is never exposed to MCP clients; it is a thin JSON-RPC-shaped
relay that mirrors the MCP surface. Message builders live in
[`protocol.py`](../src/mcp_gtw/protocol.py); the protocol version is `mcp-gtw-provider/1`.

Every frame is a JSON object with a `type`. There are three shapes:

- **request/response** — one side asks, the other answers a matching `requestId`. The gateway drives
  requests for MCP capabilities the provider serves (`request` → `result`); the provider drives
  requests for capabilities the MCP client serves (`call` → `response`).
- **register** — the provider publishes a whole registry at once; the gateway replies with `ack`.
- **notify** — one-way messages (progress, logging, resource-updated, ping/pong).

## Connecting

The provider opens:

```text
ws://host/provider?token=<provider_token>&providerName=<name>&providerId=<optional>
```

- `token` (required) — the channel's provider token.
- `providerName`, `providerId` (optional) — labels for observability.

The connection is rejected with close code `1008` if the token is invalid or the `Origin` is not in
`GATEWAY_ALLOWED_PROVIDER_ORIGINS`. A message larger than
`GATEWAY_MAXIMUM_WEBSOCKET_MESSAGE_BYTES` closes it with `1009`.

## Gateway → provider

### `hello.ack`

Sent immediately after the socket is accepted.

```json
{ "type": "hello.ack", "protocolVersion": "mcp-gtw-provider/1", "channelId": "…" }
```

### `request`

Asks the provider to serve one MCP operation. The gateway has already validated the client input
against the registered schema (for tools). `method` is the MCP method; `params` is method-specific.

```json
{ "type": "request", "requestId": "5f4d…", "method": "tools/call", "params": { … } }
```

| `method`                 | `params`                                             | expected `result`                                             |
| ------------------------ | ---------------------------------------------------- | ------------------------------------------------------------- |
| `tools/call`             | `{ "name": …, "arguments": { … } }`                  | an MCP `CallToolResult`, or any JSON value (auto-normalized)  |
| `resources/read`         | `{ "uri": … }`                                       | `{ "contents": [ { "uri"?, "mimeType"?, "text" \| "blob" } ] }` |
| `prompts/get`            | `{ "name": …, "arguments": { … } }`                  | an MCP `GetPromptResult` (`{ "description"?, "messages": [ … ] }`) |
| `completion/complete`    | `{ "ref": …, "argument": …, "context": … \| null }`  | `{ "values": [ … ], "total"?, "hasMore"? }`                   |
| `resources/subscribe`    | `{ "uri": … }`                                       | any value (ignored; acknowledges the subscription)            |
| `resources/unsubscribe`  | `{ "uri": … }`                                       | any value (ignored)                                           |

A `blob` is standard base64. The provider answers with a `result` frame (below).

### `cancel`

Asks the provider to abort an in-flight `request` (on timeout or client cancellation).

```json
{ "type": "cancel", "requestId": "5f4d…", "reason": "timeout" }
```

`reason` is `"timeout"` or `"mcp_client_cancelled"`.

### `response`

Answers a provider-initiated `call` (sampling/elicitation). Either a `result` or an `error`:

```json
{ "type": "response", "requestId": "c1", "result": { "model": "…", "content": { … } } }
{ "type": "response", "requestId": "c1", "error": "No MCP client is connected" }
```

### `ack`, `pong`, `protocol.error`

```json
{ "type": "ack", "registry": "tools", "count": 5 }
{ "type": "pong" }
{ "type": "protocol.error", "message": "…" }
```

`ack` confirms a `register`. `protocol.error` reports a malformed or rejected frame.

## Provider → gateway

### `register`

Atomically replaces one registry for this channel. `registry` is one of `tools`, `resources`,
`resourceTemplates`, `prompts`; `items` is the full list for that registry.

```json
{
  "type": "register",
  "registry": "tools",
  "items": [
    {
      "name": "move",
      "description": "Moves the player",
      "inputSchema": {
        "type": "object",
        "properties": { "direction": { "type": "string", "enum": ["up", "down", "left", "right"] } },
        "required": ["direction"],
        "additionalProperties": false
      }
    }
  ]
}
```

Each item is validated against its MCP model (`Tool`, `Resource`, `ResourceTemplate`, `Prompt`). A
tool's `inputSchema` and optional `outputSchema` must be valid JSON Schema. Duplicate identifiers,
oversized definitions, or exceeding `GATEWAY_MAXIMUM_TOOLS` are rejected with a `protocol.error`. The
gateway replies with `ack` and notifies MCP clients of the corresponding `*_list_changed`.

Publishing an empty `items` list clears that registry (used when the last entry is unregistered).

### `result`

Answers a `request`. A full MCP result:

```json
{
  "type": "result",
  "requestId": "5f4d…",
  "result": {
    "content": [{ "type": "text", "text": "{\"moved\":true}" }],
    "structuredContent": { "moved": true },
    "isError": false
  }
}
```

A plain value that the gateway normalizes automatically (objects also become `structuredContent`):

```json
{ "type": "result", "requestId": "5f4d…", "result": { "moved": true } }
```

Or an execution error:

```json
{ "type": "result", "requestId": "5f4d…", "error": "Target is too far away" }
```

A late `result` whose `requestId` is no longer pending is silently ignored.

### `call`

Asks the gateway to run an operation the **MCP client** serves. Either sampling or elicitation:

```json
{
  "type": "call",
  "requestId": "c1",
  "method": "sampling/createMessage",
  "originatingRequestId": "5f4d…",
  "params": {
    "messages": [{ "role": "user", "content": { "type": "text", "text": "Summarize" } }],
    "maxTokens": 256,
    "systemPrompt": "…",
    "modelPreferences": { "hints": [{ "name": "claude-3" }] }
  }
}
```

`originatingRequestId` (optional) is the `requestId` of the provider `request` the call is made from
(a running tool/resource/prompt handler). When present and still in flight, the gateway routes the
reverse call to **that** client — the one whose invocation triggered it. When absent (an out-of-band
call), it routes to the most recently active client session.

```json
{
  "type": "call",
  "requestId": "c2",
  "method": "elicitation/create",
  "params": { "message": "Your name?", "requestedSchema": { "type": "object", "properties": { … } } }
}
```

The gateway forwards to the client and answers with a `response` frame. If no MCP client is
connected, the `response` carries an `error`.

### `notify`

One-way messages. `method` is an MCP notification method:

```json
{ "type": "notify", "method": "notifications/resources/updated", "params": { "uri": "mem://a" } }
{ "type": "notify", "method": "notifications/progress",
  "params": { "requestId": "5f4d…", "progress": 0.5, "total": 1, "message": "half" } }
{ "type": "notify", "method": "notifications/message",
  "params": { "level": "info", "data": "…", "logger": "app" } }
```

- `notifications/resources/updated` reaches only sessions subscribed to that `uri`.
- `notifications/progress` is delivered only if its `requestId` is still pending **and** the client
  sent a `progressToken` on the originating request.
- `notifications/message` is filtered per session by the level set via `logging/setLevel`.

### `ping`

```json
{ "type": "ping" }
```

The gateway replies with `pong`.

## Reliability

- A new connection for the same channel **atomically replaces** the previous one: pending calls fail,
  the old socket is closed with `1012`, the registries are cleared, and MCP clients receive
  `tools/list_changed`, `resources/list_changed` and `prompts/list_changed` so they never keep a
  stale list.
- When the provider disconnects, all registries are cleared, pending calls fail with an offline
  error, and MCP clients receive the same `*_list_changed` notifications.
- Client subscriptions are **client state**, not a provider registry: they survive a provider
  reconnect, so a client stays subscribed to a resource across a provider bounce. Right after
  `hello.ack`, the gateway replays a `resources/subscribe` for every still-subscribed `uri` to the
  new provider, so it resumes emitting `notifications/resources/updated` without the client
  resubscribing.
- At most `GATEWAY_MAXIMUM_PENDING_CALLS_PER_CHANNEL` requests may be in flight at once; a request
  that is not answered within `GATEWAY_TOOL_CALL_TIMEOUT_SECONDS` fails and a `cancel` is sent.
