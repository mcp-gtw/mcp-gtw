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

After authenticating an owner and obtaining explicit consent in your application, call `await grants.grant(verified_principal, channel.channel_id)`. Never construct the principal from unverified browser fields. Removal of a channel revokes its grants. SQLite is a durable single-host adapter with transactional quotas and grants expiring at the supplied verified principal deadline. Reconsent can renew that deadline; expired/orphaned grants are purged during database operations. New database files use mode 0600. `MemoryChannelGrantStore` is bounded development storage with the same scope/expiry boundaries. Renewing a token cannot renew an expired grant. Keep the database in a private directory owned by the service account. Neither store is a distributed multi-replica backend. OAuth grant lookup costs O(log N + owner grants) with SQLite and is not the token mode’s O(1) dictionary lookup.

`JwtAccessTokenVerifier` validates asymmetric signatures against the configured JWKS URL, exact issuer, the canonical MCP resource audience, expiry, temporal claims, subject, client and required scopes. It requires access-token `typ=at+jwt` (or `application/at+jwt`). Configure the IdP to issue RFC 9068 access tokens containing `client_id` and `scope`. ID tokens are not MCP credentials. A legacy provider token is never accepted as an OAuth credential.

`IntrospectionAccessTokenVerifier` supports opaque tokens with server-side client credentials. Its response must include `active: true`, `iss`, `aud`, `exp`, `sub`, `client_id` (or `azp`) and a scope string. The secret is a Pydantic `SecretStr`. `oauth_token_verifier=custom` requires an injected `AccessTokenVerifier`; trusted issuer/resource enforcement is the implementer's responsibility.

The gateway closes its own verifier HTTP client at shutdown; owners of injected verifiers/clients control their lifecycle. HTTP clients use finite timeouts, no redirects and no environment proxies. JSON downloads and JWKS key counts are bounded; compressed metadata is rejected. JWKS caching is bounded and unknown-key refreshes are throttled. Signature, scope, channel and session failures fail closed. Verifier network failures and unavailable SQLite state/identity/grants return neutral no-store HTTP 503 responses. They never downgrade authorization or recreate consent.

## Discovery, transport and sessions

Both `/.well-known/oauth-protected-resource` and the RFC 9728 path variant (for example `/.well-known/oauth-protected-resource/mcp`) advertise configured URLs and scopes. Missing or invalid credentials produce a Bearer `WWW-Authenticate` challenge with `resource_metadata`; CORS exposes this header and `Mcp-Session-Id`. No discovery URL is derived from Host or forwarded headers. Only one well-formed Bearer header is accepted, with a finite size limit; query credentials are not read.

The authorization decision installs an immutable `McpAccessContext` on the ASGI scope. Before entering the SDK manager, every supplied `Mcp-Session-Id` is checked against channel, credential kind, principal, issuer and client. Scopes are evaluated on each request. Valid token renewal for the same identity can reuse a session. GET, POST and DELETE use the same gate. Bindings are process-local because the SDK sessions are process-local; they expire, have a capacity limit, and are removed on DELETE, channel removal and shutdown.

Before each emitted response chunk, the gateway checks current grants, expiry and channel identity again. A revocation stops new emissions. An already admitted request/tool execution is not canceled, and a chunk already handed to the server may finish draining. An idle SSE connection closes on the next attempted emission; there is no background idle-stream expiry timer. Correlated reverse calls remain supported; uncorrelated OAuth reverse calls are denied to avoid selecting another client's session.

The configurable request budgets track the trusted ASGI client address, verified OAuth client and principal. Budget exhaustion applies progressive backoff and returns HTTP 429 with Retry-After. Configure proxy trust explicitly and restrict origin access; a reverse proxy that groups clients under one address will share the budget. This process-local policy is not a distributed limiter. Introspection may require an IdP-side cache/rate policy for high-volume SSE streams.

## Hybrid mode

`oauth_allow_static_mcp_tokens=true` requires an explicit `static_channel_eligible` callback. It may mark only deliberately created legacy channels. An OAuth-owned channel's internally generated MCP token is not accepted. OAuth rejection does not downgrade authentication. Static clients and OAuth clients remain isolated by credential kind and owner in MCP session bindings.

## Embedded authorization server

`oauth_mode=embedded` accepts an injected `AuthorizationServer`. The shipped `EmbeddedAuthorizationServer` composes `IdentityAuthenticator`, `ConsentPolicy`, `OAuthStateStore`, `OAuthSigningKey` and `OAuthClientRegistry`. The bare library still fails at startup without these dependencies; it never invents users or enables an unauthenticated login. The demo game wires a real persistent username/password identity and game-channel consent policy.

The configured issuer serves OAuth/OIDC metadata, `/oauth/authorize`, login and explicit consent, `/oauth/token`, `/oauth/jwks` and RFC 7009 `/oauth/revoke`. Only Authorization Code with PKCE S256 is supported; exact registered redirects and canonical resource are checked in authorization and exchange. Public clients use `none`, the browser BFF uses `client_secret_basic`. ID tokens carry the browser nonce and client audience; they are never accepted as MCP access tokens.

Login and consent transactions use `GATEWAY_OAUTH_EMBEDDED_AUTHORIZATION_TTL_SECONDS` (600 seconds,
maximum 1800), including their transaction and CSRF cookies. Their deadline is absolute and is not
extended by retrying credentials or opening a form again. Authorization codes retain their separate
120-second lifetime. Expired browser flows return an HTML explanation and require a new sign-in
from the application or MCP client. Protocol endpoints continue returning OAuth JSON errors.

`IdentityAuthenticator.registration_enabled` explicitly declares whether account creation is
available. The login form only offers account creation when enabled, and forged registration
submissions are rejected before invoking the identity strategy when disabled. Username and password
requirements are visible in the form. Failed credentials use a neutral HTML message that does not
distinguish unknown accounts, incorrect passwords or duplicate registrations and never echoes either
credential. `SqlitePasswordIdentity` disables registration by default.

Embedded sign-in, consent and expired-flow pages share a responsive, dark, script-free template at
`web/oauth.html`. Labels are associated with their fields, short credential hints remain visible,
errors are announced through an alert, and keyboard focus is visible. Consent keeps the client name
and requested scopes visible while exact client, resource and callback values are available in
expandable connection details. Long values wrap without overflowing narrow screens.
`EmbeddedAuthorizationServer.page_class` selects the `AuthorizationPage` renderer for application
styling. The renderer escapes the application name and page title. Page body fragments are built
by the server with escaped client metadata and messages.

`SqliteOAuthStateStore` hashes opaque identifiers, expires records, enforces finite capacity, consumes codes atomically and rotates refresh tokens in one transaction. Reuse revokes the token family; JWT verification consults the family on every admission/emission. `ConsentPolicy.approve` records an explicit user decision only at consent. `ConsentPolicy.validate` checks the existing approval during code exchange and refresh without writing or re-granting rights. `ConsentPolicy.binding` freezes the application-specific authorization target at consent and rejects exchange/refresh if that target changes. Subject revocation removes login sessions, pending codes and access/refresh families. In-flight tool work may still finish.

`OAuthSigningKey` persists an RSA key with private permissions and publishes only its public JWK. Keep the key and database in a private persistent directory; keys and identities survive restart. Initial key generation occurs during construction, outside request paths. SQLite and password hashing execute off the event loop. The default password strategy uses 600,000 PBKDF2-HMAC-SHA256 iterations with per-user salts and a finite worker pool. Saturated password workers reject new work with HTTP 429 rather than creating an unbounded queue. Self-service registration is explicitly opt-in and there are no default passwords.

Pre-registration is always available. CIMD is enabled by default: a public client can use the exact HTTPS URL of its JSON metadata as its client ID, which must match the document. `HttpsClientMetadataResolver` resolves DNS, rejects every non-public address (including mapped IPv6), pins the connection to a validated numeric IP, checks the actual peer, and retains the original hostname for certificate verification/SNI. No redirects, environment proxies, compressed documents or secondary logo/policy/JWKS fetches are allowed. Fetch size, total timeout and concurrency are bounded. An optional exact origin allowlist narrows permitted publishers. Valid client metadata is cached durably for at most 300 seconds and respects shorter max-age/no-store/no-cache; errors are not cached. Resolution attempts have a bounded per-client budget. `ClientMetadataResolver` is injectable independently of the client registry.

`oauth_embedded_dcr_enabled` is false by default; when explicitly enabled, `/oauth/register` accepts bounded, expiring public clients with exact HTTPS/loopback callbacks, quotas and rate budgets. Both CIMD and DCR support public `none` clients using PKCE. CIMD reads the plural token_endpoint_auth_methods_supported field and selects `none` from the intersection with the server's supported methods, even if the document's singular preference is private_key_jwt. This matches the current public ChatGPT client metadata; public JWKS URLs in such documents are ignored, never fetched. Shared secrets/private key material and clients offering only unsupported private_key_jwt are rejected; assertions are rejected and private_key_jwt is not advertised. Public MCP hosts may request `openid` alongside MCP scopes and receive a separate ID token addressed to their client ID; only the MCP access token has the resource audience. The SDK 2.3 client metadata model is reused; application-owned routes add OIDC, durable identity/consent and family enforcement that its generic provider helpers do not supply. Resource and AS metadata/JWKS, browser actions, token, revocation and registration endpoints all have finite request budgets. `EmbeddedAuthorizationServer.limits_class` and the `limits=` constructor argument replace the complete `OAuthEndpointLimits` strategy. Its separate IP, client, principal and normalized username budgets are configurable in [configuration.md](configuration.md#oauth-request-budgets). Rate limits are enforced before password work and before consuming authorization codes or refresh tokens.

## Tool authorization

OAuth tool listings carry `securitySchemes` at the top level and in `_meta`, with scopes computed by the injected `ToolAccessPolicy`. Provider definitions are copied rather than changed. Static Token listings retain their original wire shape and never advertise anonymous access. Immediately before an OAuth tool is dispatched, its credentials are reverified, the original channel and principal are confirmed, and its required scopes checked; denial returns an error tool result with `_meta["mcp/www_authenticate"]`, including the canonical resource metadata URL, requested scopes, error and error_description. No denied tool is sent to the provider. HTTP authentication remains mandatory before discovery of a channel's tools.

The default `RequiredScopesToolAccess` requires the transport's minimum scopes on every tool. Override `required_scopes(channel, tool_name)` to add operation-specific scopes, inject `Gateway(tool_access_policy=...)` or set `tool_access_policy_class` on a subclass. Include every available scope in `GATEWAY_OAUTH_SUPPORTED_SCOPES`; only `GATEWAY_OAUTH_REQUIRED_SCOPES` is mandatory for all transport requests. The embedded AS requests explicit consent for those scopes and never expands them on refresh. Valid renewed tokens retain the same session identity.

The issuer is an exact HTTPS URI and may include a path prefix. RFC 8414 discovery is at `/.well-known/oauth-authorization-server<issuer-path>` and OIDC discovery at `<issuer-path>/.well-known/openid-configuration`. Authorization routes and cookie paths follow the issuer prefix, while CSRF verifies the issuer origin. Form pages use same-origin referrers so browser form Origin remains verifiable; redirects and credential responses suppress referrers. All pages disallow framing, external scripts and objects. Form submissions require a transaction cookie, CSRF cookie/field equality and exact Origin. Duplicate cookies/fields, oversized bodies, unsupported auth methods and unsafe redirects fail closed.

## Deployment and clients

Add the application's MCP endpoint to the MCP client. The client discovers the authorization server
and starts authorization with its registered callback, state and PKCE challenge. `/oauth/login`
and `/oauth/authorize` are transaction routes, not standalone connection URLs. A valid issuer login
session skips the sign-in form and proceeds to explicit consent. The embedded demo game exposes
its canonical public endpoint before browser login. Verified account login and explicit MCP client
consent can establish the account-owned game channel first. A later browser login reuses that
channel to watch the same player. External identity providers require application-owned channel
provisioning and grants.

The MCP mount follows the configured resource path, including any public prefix. The proxy must preserve this path. Use an exact HTTPS public resource URL, such as `https://mcpgame.paulox.dev/mcp` in the game. A client authorization request and token exchange must include that exact `resource`, even when connecting to a channel-specific path. With an external IdP, register exact callback URLs and public MCP client IDs there, with Authorization Code and PKCE S256. With the embedded server, pre-register clients or explicitly enable DCR so hosts can register their callbacks. HTTPS localhost exceptions require the separate development flag; keep that flag disabled in production.

The bundled runners disable HTTP access logs in OAuth mode. An `OAuthLogFilter` also redacts query strings from Uvicorn's WebSocket error logger, which otherwise records provider credentials and tickets. If you run a different server or configure additional loggers, redact credentials there as well. The supplied nginx example logs `$uri` without query strings and forwards discovery, Authorization and WebSocket upgrades without buffering MCP SSE.

Official MCP client integration tests pass locally. Real ChatGPT and Claude OAuth handshakes have not been run, so compatibility with their deployed client registration flows is not claimed. No public game deployment was modified.

References: [MCP authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization), [RFC 9728](https://www.rfc-editor.org/rfc/rfc9728), [RFC 8707](https://www.rfc-editor.org/rfc/rfc8707), [RFC 9068](https://www.rfc-editor.org/rfc/rfc9068), [RFC 9700](https://www.rfc-editor.org/rfc/rfc9700).

Channel grants persist the consented scope set as well as issuer/subject/client ownership and expiry.
A token may use fewer scopes than the grant. A token claiming additional scopes is denied until an
explicit new grant is approved. Updating a grant replaces its scope set, so removed rights cannot be
recovered using an older broader token. This final SQLite schema requires a fresh grant database for
the new feature. No historical-schema migration or permissive compatibility path is provided.

## Strategy contracts

| Abstract contract | Shipped implementation | Gateway replacement |
|---|---|---|
| `McpClientAccessController` | `TokenMcpAccessController`, OAuth and hybrid controllers | `mcp_access_controller_class`, `mcp_access_controller=` |
| `AccessTokenVerifier` | JWT, introspection or embedded AS | `access_token_verifier=` in resource-server mode |
| `ChannelAccessPolicy` | `DenyUnlessGranted` | `channel_access=` |
| `ChannelGrantStore` | `MemoryChannelGrantStore`, `SqliteChannelGrantStore` | Inject into the channel policy |
| `McpSessionBindingStore` | `MemoryMcpSessionBindingStore` | `session_binding_store_class`, `session_bindings=` |
| `OAuthMetadataPublisher` | `ProtectedResourceMetadataPublisher` | `oauth_metadata_class`, `oauth_metadata=` |
| `OAuthRateLimitPolicy` | `WindowOAuthRateLimitPolicy` | `oauth_rate_limit_class`, `oauth_rate_limit=` |
| `ToolAccessPolicy` | `RequiredScopesToolAccess` | `tool_access_policy_class`, `tool_access_policy=` |
| `AuthorizationServer` | `EmbeddedAuthorizationServer` | `authorization_server=` in embedded mode |
| `IdentityAuthenticator` | `SqlitePasswordIdentity` | Inject into the embedded AS |
| `ConsentPolicy` | Application-owned policy such as game consent | Implement explicit `approve`, read-only `validate` and target `binding` |
| `OAuthStateStore` | `SqliteOAuthStateStore` | Inject into the embedded AS |
| `ClientMetadataResolver` | `HttpsClientMetadataResolver` | Inject into the client registry |

Strategies are constructed once and hold no mutable per-request current-user field. An injected
metadata publisher requires OAuth enabled. Its path, URL and challenges must describe the configured
canonical resource. A custom consent validator must never create, broaden or renew grants.
The SQLite adapters convert filesystem/database availability faults to typed failures, which deny
MCP before the manager and return neutral HTTP 503 on authorization-server endpoints.

Consent pages show the escaped client name, exact client ID, target resource, requested scopes and
registered callback. Client-provided names alone are not identity evidence. With an issuer path
prefix, both first login and already-authenticated authorization redirect to the single canonical
prefixed consent endpoint. The prefix regression follows that actual redirect.


The packaged authorization pages use a monochrome dark layout: near-black background and inputs,
subtle gray borders, white primary actions, visible focus and compact credential hints. They remain
script-free, responsive and shared across sign-in, consent, neutral errors and expired transactions.
The username/password identity strategy remains unchanged. Visual references do not add unimplemented
social login, email identity, password recovery or magic-link actions.
