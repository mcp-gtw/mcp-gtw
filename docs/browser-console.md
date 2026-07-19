# Any page as a provider (browser console)

Any open web page can become an MCP provider: paste a snippet into the DevTools console, register
tools that act on the page, and connect. An MCP client then drives that tab through those tools. The
provider is a single dependency-free ES module, so nothing needs to be installed in the page.

## What you need

- A reachable gateway of your own, served over `wss://` when the target page is `https`.
- A way to mint a channel and hand the browser a **provider token**. The base `Gateway` does not
  expose this on purpose — you add a tiny endpoint (below).
- The page's origin allowed on the provider WebSocket (or `*`).

## 1. Expose a session endpoint

The gateway is a multi-session relay, so it hands you a **channel** — a provider token paired with an
mcp token (see [architecture](architecture.md)) — instead of a single global connection. Add one
endpoint that mints a channel and returns the URLs:

```python
from fastapi import FastAPI, Request

from mcp_gtw.gateway import Gateway


class ConsoleGateway(Gateway):
    def register_routes(self, app: FastAPI) -> None:
        super().register_routes(app)
        app.add_api_route("/sessions", self.new_session, methods=["POST"])

    async def new_session(self, request: Request) -> dict:
        channel = await self.create_channel()
        host = request.headers["host"]
        ws = "wss" if request.url.scheme == "https" else "ws"
        return {
            "providerWsUrl": f"{ws}://{host}/provider?token={channel.provider_token}",
            "mcpUrl": f"{request.url.scheme}://{host}/mcp/{channel.channel_id}",
            "mcpToken": channel.mcp_token,
        }
```

## 2. Allow the origin

The provider WebSocket checks the browser's `Origin`. To connect from arbitrary pages, allow any
origin (the `POST /sessions` fetch is already covered by CORS, which defaults to `*`):

```dotenv
GATEWAY_ALLOWED_PROVIDER_ORIGINS=*
```

Or list the exact origins you inject from. `*` only widens who may *attempt* a connection — the token
is still the credential, and a page can only touch the channel it just minted. See
[security](security.md).

## 3. Paste into the console

```javascript
const GATEWAY = "https://your-gateway.example.com";

const s = await (await fetch(`${GATEWAY}/sessions`, { method: "POST" })).json();
const { McpGtwProvider } = await import("https://esm.sh/mcp-gtw-provider");

const provider = new McpGtwProvider({ url: s.providerWsUrl, onStatusChange: (x) => console.log("mcp:", x) });

provider.registerTool(
    {
        name: "read_page",
        description: "Return the visible text of the current page.",
        inputSchema: { type: "object", properties: {}, additionalProperties: false },
    },
    () => document.body.innerText.slice(0, 8000),
);

provider.registerTool(
    {
        name: "click",
        description: "Click the first element matching a CSS selector.",
        inputSchema: {
            type: "object",
            properties: { selector: { type: "string" } },
            required: ["selector"],
            additionalProperties: false,
        },
    },
    ({ selector }) => {
        const el = document.querySelector(selector);
        if (!el) {
            throw new Error(`No element matches ${selector}`);
        }
        el.click();
        return { clicked: selector };
    },
);

await provider.connect();
console.log("MCP endpoint:", s.mcpUrl, "\nmcp token:", s.mcpToken);
```

Hand the printed `mcpUrl` and `mcpToken` to your MCP client (as `Authorization: Bearer <mcpToken>`).
Every call it makes now runs in the open tab. The handlers can read and drive the DOM, call the
page's own APIs, or forward to a backend — anything the page's own scripts could do.

## Bookmarklet

Wrap the snippet in an async IIFE and save it as a bookmark for one-click injection:

```text
javascript:(async () => { /* the snippet above */ })()
```

## Caveats

- **Mixed content** — an `https` page can only open a `wss://` gateway, never `ws://`. Use TLS.
- **Content-Security-Policy** — strict sites restrict what a page may load and connect to:
  - If `import()` from `esm.sh` is blocked but the WebSocket is allowed, paste the contents of the
    provider's `src/index.js` inline (drop the leading `export` on the class) and then
    `new McpGtwProvider(...)` directly.
  - If the site's `connect-src` blocks your gateway, the console cannot bypass it — that page's CSP
    wins. A browser extension's content script is the only way around a hostile `connect-src`.
- **Trust** — the gateway treats every provider as untrusted regardless of origin. A page can only
  register and answer for the channel it minted, so `*` does not let one page hijack another's channel.
