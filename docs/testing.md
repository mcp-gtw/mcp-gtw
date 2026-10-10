# Testing

The library is covered by a unit, integration, security and stress suite behind a **100% coverage
gate** (branch coverage included). The suite runs in a few seconds.

## Running

```bash
make test          # quiet run
make coverage      # run behind the 100% gate, prints missing lines, writes coverage.xml
```

The gate is configured in [`pyproject.toml`](../pyproject.toml):

```toml
[tool.coverage.run]
branch = true
source = ["mcpgtw"]

[tool.coverage.report]
fail_under = 100
```

## Layout

```text
tests/
├── support.py            # shared FakeWebSocket / FakeProviderWebSocket helpers
├── conftest.py           # settings, tool and websocket fixtures
├── test_entrypoints.py   # the uvicorn entrypoint
└── gateway/
    ├── test_config.py         test_protocol.py       test_codec.py
    ├── test_tokens.py         test_origin.py         test_expiry.py
    ├── test_authenticator.py  test_listeners.py      test_registry.py
    ├── test_channel.py        test_gateway.py        # the Gateway class end to end
    ├── test_extensibility.py  # every strategy swapped by class attribute and by injection
    ├── test_auth_recipes.py   # the own-token and username/password models, with abuse cases
    ├── test_security.py       # token bypass / confusion / cross-channel attempts
    └── test_concurrency.py    test_stress.py         # races and thousands-of-channels scale
```

## Approach

- **Unit tests** exercise the channel and registry directly, including every edge and failure branch:
  timeouts, cancellation, provider replacement, invalid schemas, pending-call limits and reaping.
- **Strategy tests** cover each swappable default and its abstract contract, and prove that a custom
  `TokenProvider` / `OriginPolicy` / `ExpiryPolicy` / `ProtocolCodec` / `Authenticator` / registry /
  channel takes effect end to end, both by class attribute and by `__init__` injection.
- **Auth-recipe tests** implement the own-token (client-supplied UUID, upserted) and username/password
  models and attack them: malformed tokens, upsert floods, user enumeration, malformed login bodies,
  and token reuse.
- **Security tests** attempt to bypass or confuse the two tokens — a provider token on `/mcp`, an mcp
  token on `/provider`, a removed channel's tokens, empty tokens, a cross-channel path.
- **In-process MCP integration** drives the real MCP client (`streamable_http_client`) against the
  ASGI app through an `ASGITransport`, with a fake provider answering the calls — proving the
  full `list_tools` / `call_tool` path.
- **Provider endpoint tests** feed a fake WebSocket through `Gateway.provider_endpoint`, covering
  registration, ping, protocol errors, oversized text and binary messages and provider replacement.
- **Concurrency and stress tests** run thousands of channels and concurrent create/remove churn,
  asserting unique tokens, a consistent registry, and that `admin_stats` stays correct under load.

## Linting

```bash
make lint          # ruff check + ruff format --check
make format        # apply formatting and safe fixes
```

## OAuth client authorization

Public MCP OAuth is opt-in and requires explicit channel grants. Provider WebSocket credentials remain separate. See [OAuth configuration, extension contracts, transport gates and deployment limits](oauth.md). Embedded OAuth requires an injected durable authorization server; the demo game supplies local account login and consent.

The embedded suite also runs a fixed-seed malformed-input fuzz corpus: 256 credentials and 768 token/registration/authorization requests must fail closed without reaching identity or consent. Endpoint budgets are raised only inside that test so parsing failures are exercised rather than hidden behind rate limiting.

Authorization page tests submit hostile application names, error messages, client names and client
identifiers and require escaped output with the no-store and framing policies intact. Responsive
pages are also checked in real Chrome at 1440x900, 390x844, 320x568 and 844x390. The visual checks
include 12px hints, keyboard focus, touch target height, collapsed consent details, expanded long
values, errors, expired forms and disabled signup. Vertical scrolling on short screens remains
available, and none of the checked pages scroll horizontally.

`make sdk-smoke` runs real JavaScript providers against OAuth and static MCP clients on loopback. Requirements and test-only boundaries: [tests/e2e/README.md](../tests/e2e/README.md).

The OAuth security cases are traceable to these suites:

| Plan cases | Automated coverage |
| --- | --- |
| SEC-01/02/03/19/20 | `test_resource_server.py`: signatures, claims, ID-token rejection, bounded downloads, trusted JWKS rollover and introspection failure |
| SEC-04/05/06 | `test_resource_server.py`, `gateway/test_security.py`: explicit grants, wrong owners/providers and session identity swapping |
| SEC-07/22/23 | `test_resource_server.py`: request/chunk revocation, deletion and four official-client JSON/SSE/stateful/stateless combinations |
| SEC-08/09/10/11/12 | `test_embedded.py`: exact callbacks, CSRF, PKCE/resource/scope bindings, concurrent one-use codes and refresh-family reuse |
| SEC-13/14 | `test_client_metadata.py`: non-public/mapped IPs, DNS pinning and actual peer, blocked redirects, malformed/oversized metadata and bounded cache |
| SEC-15/16 | `test_embedded.py`, `test_client_metadata.py`: registration quotas, finite stores and endpoint request budgets |
| SEC-17/18/27 | `test_resource_server.py`, `test_embedded.py`: ambiguous credentials/cookies/fields, canonical URLs and neutral challenges |
| SEC-21 | `test_resource_server.py`, `gateway/test_gateway.py`: log query redaction and version/admin exposure |
| SEC-24/25 | `gateway/test_extensibility.py`, `gateway/test_channel.py`, `test_resource_server.py`: strategy injection, token regression and correlated reverse calls |
| SEC-26/28 | `test_embedded.py` and demo `tests/e2e/proxy.py`: cookie/consent protections and real Docker/nginx HTTPS discovery |
| HOST-08 | `test_resource_server.py`: serialized tool securitySchemes, missing-scope challenge, no provider execution and successful reauthorization |

The demo's `make embedded-smoke` uses the actual embedded AS and durable accounts in Chrome; `make oauth-smoke` exercises an external test IdP. Both use the official MCP client. Browser and proxy scripts are opt-in local integrations, not evidence of a remote ChatGPT/Claude connection. Real host account/workspace checks require a separately authorized accessible endpoint and are not covered by line/branch coverage. GitHub CI runs only after the maintainer pushes the coordinated branches.

## Coordinated immutable integration

`.github/workflows/oauth-integration.yml` accepts three required full commit SHAs and checks out the
matching gateway, game and JavaScript provider. It runs Python 3.12/3.13/3.14 gates, the frontend/SDK
gates, real Chrome external/embedded login, official MCP clients, Inspector, 1000-channel bounded
load and production Docker/nginx HTTPS smoke. No release or publish step is present. Action SHAs,
uv, npm, Node and Inspector are pinned. Record the three commits shown by the workflow.

The demo unit workflow and Docker builds install the published gateway selected by its PyPI
lockfile. They do not require a sibling checkout or `GATEWAY_INTEGRATION_SHA`. The coordinated
workflow still checks out each repository by full commit SHA, and game integrations exercise the
published gateway from the game lockfile. Record that installed package version alongside the
checkout revisions when reviewing acceptance results.

Registry checks cover all direct/dev/build and locked transitive packages. Babel 7 is constrained by
magicast, es-module-lexer 2 and obug 2 by Vitest, nanoid 3 by postcss, mdn-data 2.27.1 by css-tree, and
why-is-node-running 3.2.1 is pinned by Vitest. Their parents are current stable releases. Do not force
unsupported transitive majors through npm overrides. Compatible upgrades are resolved into locks.
Temporary registry/audit results are kept outside the repositories per the maintainer's preference.
