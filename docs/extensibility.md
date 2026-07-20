# Extensibility

`Gateway` is a **composition root**. Every behaviour that has more than one reasonable
implementation is a **strategy**: a small abstract base class with one default. The conventional
path is every default, and it is secure out of the box. Change any single behaviour by swapping one
class — you never copy or override a transport method to do it.

Two ways to swap, both first class:

- **Class attribute** on a subclass — the idiomatic default-changing path.
- **Instance injection** through `__init__` — for dependency injection, tests, or wiring you build
  yourself.

```python
from mcp_gtw.gateway import Gateway
from mcp_gtw.tokens import SecretsTokenProvider

class MyGateway(Gateway):
    token_provider_class = MyTokenProvider        # swap by class

gateway = MyGateway()
gateway = Gateway(tokens=MyTokenProvider())       # or swap by instance
```

## The strategies

| Concern | Base class | Module | Default | `Gateway` attribute / `__init__` arg |
| --- | --- | --- | --- | --- |
| Configuration | `GatewaySettings` | `config` | `GatewaySettings` | `settings_class` / `settings` |
| Token minting & comparison | `TokenProvider` | `tokens` | `SecretsTokenProvider` | `token_provider_class` / `tokens` |
| Origin admission | `OriginPolicy` | `origin` | `ListOriginPolicy` | `origin_policy_class` / `provider_origins`, `mcp_origins` |
| Idle-channel reclamation | `ExpiryPolicy` | `expiry` | `TtlExpiryPolicy` | `expiry_policy_class` / `expiry_policy` |
| Wire frame parsing | `ProtocolCodec` | `codec` | `JsonProtocolCodec` | `codec_class` / `codec` |
| Connection admission (auth) | `Authenticator` | `authenticator` | `TokenAuthenticator` | `authenticator_class` / `authenticator` |
| Channel storage & lifecycle | `ChannelRegistry` | `registry` | `ChannelRegistry` | `registry_class` / `registry` |
| Per-session behaviour | `Channel` | `channel` | `Channel` | `channel_class` |
| Lifecycle observation | `GatewayListener` | `listeners` | `Gateway` itself | override the hooks |

Every base class rejects direct instantiation (its abstract methods have no body), so a partial
implementation fails loudly instead of silently doing nothing.

### `TokenProvider` — how tokens look and compare

```python
class TokenProvider(ABC):
    def generate(self, nbytes: int = 32) -> str: ...
    def equals(self, received: str | None, expected: str) -> bool: ...
```

`generate` mints channel ids and the two per-channel tokens. `equals` compares the admin key (and
whatever your own code compares) and **must be constant time**. The default is
`secrets.token_urlsafe` plus `hmac.compare_digest`. Swap it to derive tokens deterministically (for
example `HMAC(server_secret, subject)`), to change their length, or to change the encoding.

### `OriginPolicy` — which `Origin` headers are allowed

```python
class OriginPolicy(ABC):
    def allows(self, origin: str | None) -> bool          # None is always allowed
    def allows_origin(self, origin: str) -> bool: ...     # you implement this
```

A missing origin is always allowed (non-browser clients omit it), so you only implement the
non-null case. The default `ListOriginPolicy` allows origins in a fixed list, or every origin when
the list holds `*`. The gateway builds **two** instances — `provider_origins` from
`allowed_provider_origins` and `mcp_origins` from `allowed_mcp_origins`.

### `ExpiryPolicy` — when an idle channel is reclaimed

```python
class ExpiryPolicy(ABC):
    def initial_deadline(self, now: float, ttl_seconds: float | None) -> float: ...
    def connected_deadline(self) -> float: ...
    def disconnected_deadline(self, now: float) -> float: ...
    def is_expired(self, deadline: float, now: float) -> bool: ...
    def reclaim_in(self, deadline: float, now: float) -> float | None: ...
```

The registry owns the stored deadlines and the connected state. This policy owns only the time
math. The default `TtlExpiryPolicy` never expires a connected channel and reclaims an offline one
after `offline_ttl_seconds`. Swap it for "never expire", "reclaim immediately", or a per-metadata
TTL.

### `ProtocolCodec` — parsing an untrusted provider frame

```python
class ProtocolCodec(ABC):
    def decode(self, text: str | None) -> dict[str, Any]: ...
```

The default `JsonProtocolCodec` bounds the container nesting before parsing (so a hostile payload
cannot exhaust the interpreter stack) and requires a JSON object. A `None` frame (a binary message)
raises, which the gateway turns into a `protocol.error`. Swap it to speak a different wire format.

### `Authenticator` — which channel a connection may use

```python
class Authenticator(ABC):
    async def authenticate_provider(self, websocket) -> Channel | None: ...
    async def authenticate_client(self, request) -> Channel | None: ...
```

This is the single seam for every access model. The default `TokenAuthenticator` admits a
connection that carries a known token — a query `token` for a provider, a bearer header for an MCP
client — and returns `None` (deny) otherwise. Override it to authenticate by username/password, to
validate and upsert a client-supplied token, or anything else. Full walk-throughs live in
[auth-recipes.md](auth-recipes.md).

An `authenticator_class` is constructed as `authenticator_class(registry)`, so a custom class swapped
by attribute must accept that signature. It reaches the token provider through `registry.tokens`
(the same instance that mints channel tokens). Anything else — a different constructor, extra
dependencies — is injected as a built instance via `authenticator=`.

## Injecting tokens when creating a channel

`create_channel` mints both tokens by default, but accepts either explicitly. This is the primitive
that lets an authenticator honour a client-supplied token or a derived one:

```python
channel = await gateway.create_channel(
    channel_id="stable-id",        # optional, defaults to a random id
    provider_token="…",            # optional, defaults to a fresh unique token
    mcp_token="…",                 # optional, defaults to a fresh unique token
    ttl_seconds=float("inf"),      # optional, defaults to offline_ttl_seconds
    metadata={"user": "alice"},
)
```

A duplicate `channel_id` or a token that already maps to a channel raises `ChannelCapacityError`,
so a concurrent "create if missing" converges on one channel instead of corrupting the indexes.

## What never changes — the invariants

The strategies decide policy. The core keeps a floor of safety that applies to **every** strategy,
so a naive extension cannot introduce a race or bypass a limit it never touched:

- **Atomic channel lifecycle.** Create, remove and reclaim run under the registry lock, keyed by a
  deterministic id, so concurrent connects converge and the token indexes never tear.
- **Deny by default.** An authenticator that returns `None` closes the connection. Nothing is
  created for an unauthenticated connection unless *your* authenticator chooses to.
- **Bounded by default, enforced by the core.** No strategy can bypass a limit. The capacity limits
  (`maximum_channels`, `maximum_pending_calls_per_channel`, `maximum_tools`,
  `maximum_tool_definition_bytes`, `maximum_mcp_sessions_per_channel`,
  `maximum_subscriptions_per_channel`) default to a safe value and accept an empty value to mean
  unlimited — that is operator policy, not a strategy seam. The process-safety limits
  (`maximum_websocket_message_bytes`, `maximum_json_depth`) are always enforced and cannot be disabled.
- **Two independent tokens.** A channel always has a separate `mcp_token` and `provider_token`.

You *can* override any of this — nothing is sealed — but you never inherit it by accident, and you
never lose it by swapping an unrelated strategy. See [security.md](security.md) for the trust
boundaries these invariants protect.
