# OAuth client authorization

OAuth protects the public MCP transport. The private `/provider` WebSocket still uses its separate provider token and Origin policy. OAuth never passes an upstream token to providers. `oauth_mode=off` is the default and preserves the existing `Authenticator` path through `TokenMcpAccessController`.

## Resource server

Subclass `Gateway` and inject a `ChannelAccessPolicy`. The default policy is intentionally not an implicit account-to-channel mapping: startup requires an explicit policy in OAuth mode. `DenyUnlessGranted` uses a `ChannelGrantStore`; grant ownership is `(issuer, subject, OAuth client_id)`. An access token alone does not authorize a channel. The bare `/mcp` route resolves only one unambiguous grant; `/mcp/<channel_id>` resolves only an explicitly granted channel. A guessed channel ID is insufficient.

```python
from mcpgtw.config import GatewaySettings
from mcpgtw.gateway import Gateway
from mcpgtw.oauth.channel_access import DenyUnlessGranted
from mcpgtw.oauth.sqlite_grants import SqliteChannelGrantStore

settings = GatewaySettings(
    oauth_mode="resource_server",
    oauth_resource_url="https://gateway.example/mcp",
    oauth_authorization_servers=["https://identity.example"],
    oauth_jwks_url="https://identity.example/jwks",
)
grants = SqliteChannelGrantStore("/data/grants.sqlite")
gateway = Gateway(settings, channel_access=DenyUnlessGranted(grants))
```

After authenticating an owner and obtaining explicit consent in your application, call `await grants.grant(verified_principal, channel.channel_id)`. Never construct the principal from unverified browser fields. Removal of a channel revokes its grants. SQLite is a durable single-host adapter with transactional quotas and grants expiring at the supplied verified principal deadline. Reconsent can renew that deadline; expired/orphaned grants are purged during database operations. New database files use mode 0600. `MemoryChannelGrantStore` is bounded development storage. Keep the database in a private directory owned by the service account. Neither store is a distributed multi-replica backend. OAuth grant lookup costs O(log N + owner grants) with SQLite and is not the token mode’s O(1) dictionary lookup.

`JwtAccessTokenVerifier` validates asymmetric signatures against the configured JWKS URL, exact issuer, the canonical MCP resource audience, expiry, temporal claims, subject, client and required scopes. It requires access-token `typ=at+jwt` (or `application/at+jwt`). Configure the IdP to issue RFC 9068 access tokens containing `client_id` and `scope`. ID tokens are not MCP credentials. A legacy provider token is never accepted as an OAuth credential.

`IntrospectionAccessTokenVerifier` supports opaque tokens with server-side client credentials. Its response must include `active: true`, `iss`, `aud`, `exp`, `sub`, `client_id` (or `azp`) and a scope string. The secret is a Pydantic `SecretStr`. `oauth_token_verifier=custom` requires an injected `AccessTokenVerifier`; trusted issuer/resource enforcement is the implementer's responsibility.

The gateway closes its own verifier HTTP client at shutdown; owners of injected verifiers/clients control their lifecycle. HTTP clients use finite timeouts, no redirects and no environment proxies. JSON downloads and JWKS key counts are bounded; compressed metadata is rejected. JWKS caching is bounded and unknown-key refreshes are throttled. Signature, scope, channel and session failures fail closed. Verifier network failures return 503, never static-token fallback.

## Discovery, transport and sessions

Both `/.well-known/oauth-protected-resource` and the RFC 9728 path variant (for example `/.well-known/oauth-protected-resource/mcp`) advertise configured URLs and scopes. Missing or invalid credentials produce a Bearer `WWW-Authenticate` challenge with `resource_metadata`; CORS exposes this header and `Mcp-Session-Id`. No discovery URL is derived from Host or forwarded headers. Only one well-formed Bearer header is accepted, with a finite size limit; query credentials are not read.

The authorization decision installs an immutable `McpAccessContext` on the ASGI scope. Before entering the SDK manager, every supplied `Mcp-Session-Id` is checked against channel, credential kind, principal, issuer and client. Scopes are evaluated on each request. Valid token renewal for the same identity can reuse a session. GET, POST and DELETE use the same gate. Bindings are process-local because the SDK sessions are process-local; they expire, have a capacity limit, and are removed on DELETE, channel removal and shutdown.

Before each emitted response chunk, the gateway checks current grants, expiry and channel identity again. A revocation stops new emissions. An already admitted request/tool execution is not canceled, and a chunk already handed to the server may finish draining. An idle SSE connection closes on the next attempted emission; there is no background idle-stream expiry timer. Correlated reverse calls remain supported; uncorrelated OAuth reverse calls are denied to avoid selecting another client's session.

The configurable fixed-window request budget is keyed by the trusted ASGI client address. Configure proxy trust explicitly and restrict origin access; a reverse proxy that groups clients under one address will share the budget. This process-local policy is not a distributed limiter. Introspection may require an IdP-side cache/rate policy for high-volume SSE streams.

## Hybrid mode

`oauth_allow_static_mcp_tokens=true` requires an explicit `static_channel_eligible` callback. It may mark only deliberately created legacy channels. An OAuth-owned channel's internally generated MCP token is not accepted. OAuth rejection does not downgrade authentication. Static clients and OAuth clients remain isolated by credential kind and owner in MCP session bindings.

## Embedded authorization server

`oauth_mode=embedded` raises `GatewayConfigurationError`. No authorization, token, refresh, CIMD or DCR endpoint is exposed. A production authorization server with trustworthy identity, explicit consent, durable code/refresh storage, secure signing and safe client registration is not implemented. This is the fail-closed option required by the implementation plan and is an outstanding feature, not a completed third mode. Use a configured external IdP for resource-server deployment.

## Deployment and clients

Use an exact HTTPS public resource URL, such as `https://mcpgame.paulox.dev/mcp` in the game. A client authorization request and token exchange must include that exact `resource`, even when connecting to a channel-specific path. Register exact callback URLs and public MCP client IDs in the IdP, with Authorization Code and PKCE S256. The gateway neither creates an IdP nor registers ChatGPT/Claude clients. HTTPS localhost exceptions require the separate development flag; keep that flag disabled in production.

The bundled runners disable HTTP access logs in OAuth mode. An `OAuthLogFilter` also redacts query strings from Uvicorn's WebSocket error logger, which otherwise records provider credentials and tickets. If you run a different server or configure additional loggers, redact credentials there as well. The supplied nginx example logs `$uri` without query strings and forwards discovery, Authorization and WebSocket upgrades without buffering MCP SSE.

Official MCP client integration tests pass locally. Real ChatGPT and Claude OAuth handshakes have not been run, so compatibility with their deployed client registration flows is not claimed. No public game deployment was modified.

References: [MCP authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization), [RFC 9728](https://www.rfc-editor.org/rfc/rfc9728), [RFC 8707](https://www.rfc-editor.org/rfc/rfc8707), [RFC 9068](https://www.rfc-editor.org/rfc/rfc9068), [RFC 9700](https://www.rfc-editor.org/rfc/rfc9700).
