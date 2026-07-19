# Provider SDK

The provider side is plain JavaScript — this library never ships or requires a specific frontend. The
official, framework-agnostic [`mcp-gtw-provider`](https://github.com/mcp-gtw/mcp-gtw-provider) package
is a small, dependency-free `McpGtwProvider` ES module that speaks the
[provider protocol](provider-protocol.md). Install it with `npm install mcp-gtw-provider`, or adapt the
pattern below to any framework.

## Constructing

```javascript
import { McpGtwProvider } from "mcp-gtw-provider";

const provider = new McpGtwProvider({
    url: "ws://127.0.0.1:8000/provider?token=PROVIDER_TOKEN&providerName=my-app",
    onStatusChange: (status) => console.log("gateway:", status),
});
```

| Option | Default | Description |
| --- | --- | --- |
| `url` | required | The `/provider` WebSocket URL including the token. |
| `reconnect` | `true` | Reconnect automatically on drop. |
| `reconnectMinDelayMs` / `reconnectMaxDelayMs` | `500` / `10000` | Backoff bounds. |
| `heartbeatIntervalMs` | `20000` | `ping` interval. |
| `onStatusChange` | `null` | Called with `"connected"` / `"disconnected"`. |

## Registering tools

`registerTool(definition, handler)` adds a tool and returns an unregister function. The definition is
a standard MCP tool. The handler is an async function that receives the arguments and a context.

```javascript
provider.registerTool(
    {
        name: "move",
        description: "Moves the player a number of steps in a direction.",
        inputSchema: {
            type: "object",
            properties: {
                direction: { type: "string", enum: ["up", "down", "left", "right"] },
                steps: { type: "integer", minimum: 1, maximum: 6 },
            },
            required: ["direction"],
            additionalProperties: false,
        },
    },
    async ({ direction, steps }, { signal }) => {
        return await runMove(direction, steps, signal);
    },
);
```

The handler context provides:

- `signal` — an `AbortSignal` that fires on `cancel`; forward it to `fetch` and timers.
- `requestId`, `toolName` — identifiers for logging.
- `progress(progress, total?, message?)` — reports incremental progress (see [Progress](#progress)).

The return value may be a plain object (normalized into `structuredContent`), a string, or a full
MCP result with a `content` array. Throwing rejects the call with an error result.

## Connecting and lifecycle

```javascript
await provider.connect();   // opens the socket and publishes the current tools
provider.disconnect();      // closes and aborts every in-flight call
```

Tools registered before or after connecting are always published: `registerTool` re-sends the full
list if the socket is open, and the whole list is re-published after every reconnect.

## Read-only and dynamic tools

Mark side-effect-free tools so hosts can treat them as safe:

```javascript
provider.registerTool(
    {
        name: "look_around",
        description: "Returns what is around the agent.",
        inputSchema: { type: "object", properties: {}, additionalProperties: false },
        annotations: { readOnlyHint: true },
    },
    async () => currentView(),
);
```

Because `registerTool` returns an unregister function, tools can appear and disappear with the UI
state — a menu, a dialog, a checkout step — and the gateway always reflects the current set.

## Progress

Long-running handlers can stream progress. The gateway forwards each update to the MCP client, but
only when that client attached a `progressToken` to the request — otherwise the call still runs and
the updates are dropped.

```javascript
provider.registerTool({ name: "build" }, async (_args, { progress, signal }) => {
    for (let step = 1; step <= 3; step += 1) {
        await doStep(step, signal);
        progress(step / 3, 1, `step ${step}/3`);
    }
    return "done";
});
```

## Resources

`registerResource(definition, reader)` publishes a concrete resource and its reader. The reader
receives the `uri` and the same context as a tool, and may return a full MCP read result, an array of
contents, a string (wrapped as `text`), or a single contents object.

```javascript
provider.registerResource(
    { uri: "mem://state", name: "state", mimeType: "application/json" },
    async () => JSON.stringify(currentState()),
);
```

Binary contents use a base64 `blob`:

```javascript
provider.registerResource({ uri: "img://logo", name: "logo", mimeType: "image/png" }, async () => ({
    blob: pngBase64,
}));
```

`registerResourceTemplate(definition)` advertises an RFC 6570 template in the resource list (reads
still go through a concrete `registerResource`):

```javascript
provider.registerResourceTemplate({ uriTemplate: "mem://item/{id}", name: "item" });
```

## Prompts

`registerPrompt(definition, handler)` publishes a prompt. The handler receives the requested
arguments and returns an MCP `GetPromptResult`.

```javascript
provider.registerPrompt(
    { name: "greet", arguments: [{ name: "who", required: true }] },
    async ({ who }) => ({
        messages: [{ role: "user", content: { type: "text", text: `Say hi to ${who}` } }],
    }),
);
```

## Completion

Argument completion is a single optional callback, `onComplete(ref, argument, context)`. Return an
array of strings or a full `{ values, total?, hasMore? }`. When unset, completion yields nothing.

```javascript
provider.onComplete = (ref, argument) =>
    ["up", "down", "left", "right"].filter((v) => v.startsWith(argument.value ?? ""));
```

## Subscriptions and resource updates

Set `onSubscribe` / `onUnsubscribe` to track which resources a client watches, then call
`notifyResourceUpdated(uri)` when one changes. The gateway delivers the update only to sessions
subscribed to that `uri`.

```javascript
provider.onSubscribe = (uri) => watched.add(uri);
provider.onUnsubscribe = (uri) => watched.delete(uri);

// later, when the resource changes:
provider.notifyResourceUpdated("mem://state");
```

## Logging

`log(level, data, logger?)` sends a structured log message to the client. The `level` is an MCP
logging level (`debug` … `emergency`); the gateway filters per session by the level the client set
via `logging/setLevel`.

```javascript
provider.log("info", { event: "spawned", id }, "arena");
```

## Sampling and elicitation

A provider can call back into the MCP client. `requestSampling(params)` asks the client's LLM for a
completion; `requestElicit(message, requestedSchema)` asks the user for structured input. Both reject
if no client is connected.

**Inside a handler, call them on the context** so the gateway routes them back to the exact client
whose invocation triggered the handler — not another client sharing the channel:

```javascript
provider.registerTool({ name: "summarize" }, async ({ text }, ctx) => {
    const reply = await ctx.requestSampling({
        messages: [{ role: "user", content: { type: "text", text: `Summarize: ${text}` } }],
        maxTokens: 128,
    });
    return reply.content.text;
});
```

The same `ctx.requestElicit(message, requestedSchema)` is available. The provider-level
`provider.requestSampling` / `provider.requestElicit` remain for out-of-band calls made outside any
handler; those route to the most recently active client (best-effort, since there is no initiating
request to correlate).

## Forwarding to an authoritative server

For multiplayer, the handler should not be the source of truth. Forward the command to your server
and return its response, passing the abort signal through:

```javascript
async ({ direction }, { signal }) => {
    const response = await fetch("/commands", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${providerToken}` },
        body: JSON.stringify({ command: "move", arguments: { direction } }),
        signal,
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail ?? `Command failed: ${response.status}`);
    return payload;
}
```

Wire each tool this way, forwarding to your own authoritative server when the provider should not be
the source of truth.
