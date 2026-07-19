# Admin dashboard

An optional, self-contained dashboard shows what the gateway is doing right now: how many channels
exist, which providers are online, the tools they registered and the in-flight calls.

It is **disabled by default**. Enabling it **requires** a key — the two settings go together.

```dotenv
GATEWAY_ADMIN_ENABLED=true
GATEWAY_ADMIN_KEY=a-long-random-key
```

- `GATEWAY_ADMIN_ENABLED=false` (default): no admin routes exist at all — `/admin` returns `404`.
- `GATEWAY_ADMIN_ENABLED=true` with `GATEWAY_ADMIN_KEY` set: every request must carry `?key=<value>`;
  a wrong or missing key returns `403` (the key is compared in constant time).
- `GATEWAY_ADMIN_ENABLED=true` **without** a key: constructing the `Gateway` raises
  `GatewayConfigurationError` at startup. The dashboard is never served unauthenticated.

## Pages

| Route | Purpose |
| --- | --- |
| `GET /admin` | The dashboard — a single HTML file using Tailwind CSS 4 (CDN) and jQuery. |
| `GET /admin/stats` | The JSON the dashboard polls. |

Open `http://127.0.0.1:8000/admin?key=<key>` in a browser. The page reads the key from its own URL
and polls the stats endpoint every few seconds.

## Stats payload

```json
{
  "app": { "name": "MCP Gateway", "version": "0.1.0" },
  "totals": { "channels": 2, "providersConnected": 1, "tools": 5, "pendingCalls": 0 },
  "channels": [
    {
      "channelId": "…",
      "providerConnected": true,
      "providerId": "…",
      "providerName": "service-a",
      "toolCount": 5,
      "tools": ["look_around", "move", "attack", "shoot", "return_to_base"],
      "pendingCalls": 0,
      "ageSeconds": 42.1,
      "reclaimInSeconds": null
    }
  ]
}
```

`reclaimInSeconds` is `null` while the provider is connected (the channel is not up for reclamation)
and counts down once it goes offline. See [configuration](configuration.md) for
`GATEWAY_OFFLINE_TTL_SECONDS`.

## Customizing

`admin_stats()` on the `Gateway` builds the payload from `registry.admin_channels()` and each
`Channel.snapshot()`. Override `admin_stats` to add your own fields, or override `admin_page` to serve
a different dashboard. Behind the flag, the routes are registered by `register_routes`, so a subclass
can add more admin endpoints there.
