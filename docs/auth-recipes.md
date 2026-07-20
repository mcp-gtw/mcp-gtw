# Authentication recipes

Who may open a channel, and how a connection maps to one, is the `Authenticator` strategy (see
[extensibility.md](extensibility.md)). The default admits known tokens. This page shows three
concrete models. Each is a real, tested shape — swap one class, or add one route, and nothing else
in the gateway changes.

Two tokens are always in play: the provider (the browser or WebSocket client) carries a
`provider_token`, and the MCP client carries an `mcp_token`. The gateway is always the authority on
token values — a client may bring an *identifier*, never the secret it is compared against, unless
you deliberately choose otherwise.

## 1. The default — token model

Nothing to write. `TokenAuthenticator` is the default: mint a channel, hand its `provider_token` to
the provider and its `mcp_token` to the MCP client, and each side authenticates by presenting its
token. This is the model behind [gateway-library.md](gateway-library.md)'s auto-connect page.

```python
channel = await gateway.create_channel()
# provider connects to  /provider?token={channel.provider_token}
# MCP client calls       /mcp/{channel.channel_id}  with  Authorization: Bearer {channel.mcp_token}
```

Use it when the gateway itself decides when a channel exists (a page mints one on load, a backend
mints one behind its own login).

## 2. Username / password (an app or a game)

Authentication that mints a channel is **additive**: add a route, keep the default authenticator.
The route verifies credentials, mints a channel, and returns both tokens. The provider then connects
with the `provider_token`, which now exists, so the default authenticator admits it unchanged.

```python
from typing import ClassVar

from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from mcp_gtw.gateway import Gateway


class LoginGateway(Gateway):
    users: ClassVar[dict[str, str]] = {"alice": "s3cret"}   # replace with your user store

    def register_routes(self, app: FastAPI) -> None:
        super().register_routes(app)
        app.add_api_route("/login", self.login, methods=["POST"])

    async def login(self, request: Request) -> dict:
        body = await request.json()
        expected = self.users.get(body.get("username"))

        if expected is None or not self.tokens.equals(body.get("password"), expected):
            raise HTTPException(status_code=401, detail="Invalid credentials")

        channel = await self.create_channel(metadata={"user": body["username"]})
        return {
            "channelId": channel.channel_id,
            "providerToken": channel.provider_token,
            "mcpToken": channel.mcp_token,
        }
```

- `self.tokens.equals` is the `TokenProvider`'s constant-time comparison — use it for the password so
  a wrong password is not distinguishable by timing.
- The unknown-user branch returns the same 401 as a wrong password.
- Swap the `users` dict for a real user store, hash comparison, or an OAuth exchange — the shape is
  the same: authenticate, then `create_channel`, then return the tokens.

The client logs in once, then connects the provider with the returned `providerToken` and points the
MCP client at `/mcp/{channelId}` with the `mcpToken`.

## 3. A client-supplied token from `localStorage`

Here the browser keeps its own token so it does not mint a new channel on every reload. The token is
a **format** the server validates, not an arbitrary string — a UUID passes, `token-paulo` does not.
This is one `authenticator_class` swap.

```python
import hashlib
import re

from mcp_gtw.authenticator import TokenAuthenticator
from mcp_gtw.errors import ChannelCapacityError
from mcp_gtw.gateway import Gateway

_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class OwnTokenAuthenticator(TokenAuthenticator):
    async def authenticate_provider(self, websocket):
        token = websocket.query_params.get("token")

        if token is None or _UUID.match(token) is None:
            return None

        existing = self._registry.resolve_provider_token(token)

        if existing is not None:
            return existing

        try:
            return await self._registry.create_channel(
                channel_id=hashlib.sha256(token.encode()).hexdigest()[:16],
                provider_token=token,
                mcp_token=f"mcp-{token}",
                ttl_seconds=float("inf"),
            )
        except ChannelCapacityError:
            return self._registry.resolve_provider_token(token)


class OwnTokenGateway(Gateway):
    authenticator_class = OwnTokenAuthenticator
```

How it behaves:

- **Validate the format.** A token that is not a UUID is denied — the connection closes with `1008`.
- **Resolve or create.** A known token reuses its channel. A new valid token creates one whose
  `provider_token` is the client's token.
- **Idempotent under a race.** The `channel_id` is derived by hashing the token, so two simultaneous
  connects with the same token collide on the id — the second `create_channel` raises
  `ChannelCapacityError` and the `except` re-resolves, converging on one channel.
- **Hashed id.** The id is a hash, not the raw token, so the secret never lands in logs or the admin
  dashboard.

The browser mints and stores the token once, then connects with it:

```javascript
function getProviderToken() {
    let token = localStorage.getItem("mcpProviderToken");

    if (!token) {
        token = crypto.randomUUID();
        localStorage.setItem("mcpProviderToken", token);
    }

    return token;
}

const provider = new McpGtwProvider({
    url: `wss://your-gateway.example.com/provider?token=${getProviderToken()}`,
});
await provider.connect();
```

The MCP client uses `mcp-{token}` as its bearer — the same derivation as the server, so it needs no
round trip. Reconnections reuse the same token and channel; a gateway restart recreates the channel
on the next connect and the provider SDK republishes its registrations automatically.

> The security of this model rests entirely on the token being a strong random secret. `crypto.randomUUID()`
> is fine. A token derived from a user id or anything guessable is not — see [security.md](security.md).

## Deriving the token on the server (strongest)

If you want a stable per-user channel without trusting the client to pick a strong secret, keep the
client identifier non-secret and derive the real token on the server with a `TokenProvider`:

```python
import hmac

from mcp_gtw.tokens import SecretsTokenProvider

class DerivedTokenProvider(SecretsTokenProvider):
    def __init__(self, secret: bytes) -> None:
        self._secret = secret

    def derive(self, subject: str) -> str:
        return hmac.new(self._secret, subject.encode(), "sha256").hexdigest()
```

Wire it with `token_provider_class = DerivedTokenProvider`. An authenticator reads the non-secret
`subject` from the connection, calls `self._registry.tokens.derive(subject)` for the unguessable
token, and upserts the channel exactly as in recipe 3. The client stores only its `subject`; the
secret never leaves the server.
