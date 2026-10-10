# OAuth implementation and acceptance checklist

This is the working checklist for the final MCP OAuth implementation plan. It records executable
acceptance requirements, not a promise of universal security. A checked item requires implementation,
a named test or command, and consistent documentation. Unchecked items remain open. External host
checks must identify the actual account, endpoint and client version. Local tests do not prove those
checks. Commit, push, release and deployment are maintainer actions.

## Library architecture and extension contracts

- [x] Keep the distribution `mcp-gtw`, use the `mcpgtw` import package everywhere, and ship web assets
  and `py.typed` in wheels. Check imports, package contents and all three consumers.
- [x] Keep provider authentication separate from public MCP authorization. Accept only private
  provider tokens at `/provider`, and never forward MCP access/refresh tokens to a provider.
- [x] Keep OAuth disabled by default. Token channels work without an issuer, accounts, OAuth routes,
  cookies or OAuth database. Exercise custom authenticators and custom GatewaySettings.
- [x] Implement resource-server mode with injected verifier and channel policy. Provide JWT/JWKS
  and RFC 7662 introspection adapters with bounded, fail-closed HTTP requests.
- [x] Implement embedded mode with durable identity, consent, client registry, state and signing key.
  Refuse activation when required injected dependencies or canonical issuer configuration are absent.
- [x] Implement explicitly enabled hybrid mode with static-channel eligibility. Never accept the
  internally generated static MCP token of an OAuth-owned channel.
- [x] Return typed immutable access contexts and typed authorization failures. Derive opaque principal
  identities from verified issuer, subject, client and credential mode, never email or display name.
- [x] Separate verification, channel grants, session binding, resource metadata, AS, identity,
  consent, client metadata and tool authorization into independent modules and injection seams.
- [x] Complete configurable IP/client/principal/account budgets, progressive backoff and bounded key
  expiry. Verify denial before password work, token consumption, manager admission and provider calls.
- [x] Persist scopes with channel grants. A token with more rights cannot silently expand the
  approved grant. Keep grants isolated by issuer, subject, client and channel generation.
- [x] Bind MCP session IDs to channel, opaque principal, client, issuer and credential kind. Recheck
  authorization for each request and outgoing stream chunk. Permit refreshed expiry/scopes only
  within the same bound identity and the approved grant.
- [x] Keep request handlers async. Run SQLite and bounded password work off the event loop, close all
  connections, and document single-host persistence and process-local routing/session limitations.
- [x] Advertise tool securitySchemes without mutating provider registrations. Recheck current access
  and required scopes immediately before execution, and return a tool authentication challenge on
  denial without reaching the provider.
- [x] Review reverse sampling/elicitation, progress, subscriptions and resource notifications with
  multiple OAuth clients. Prevent an uncorrelated provider call from choosing another client session.
- [x] Compile self-contained input/output schemas with an explicit no-retrieval Registry. Prove
  provider-controlled references cannot fetch private/public HTTP endpoints or files, and preserve
  valid local definitions and root-ID references without IO.

## Authorization-server flows and persistence

- [x] Publish RFC 9728 resource metadata, RFC 8414 AS metadata and OIDC discovery from configured
  canonical URLs. Publish only implemented algorithms, grant types and client authentication methods.
- [x] Support public PKCE clients and confidential browser clients. Require Authorization Code,
  S256, exact redirect URI, `resource` on MCP authorize/token requests and exact access-token audience.
- [x] Keep browser ID tokens separate from MCP access tokens. Verify nonce, issuer and browser audience
  server-side, and never use an OIDC ID token as an MCP bearer credential.
- [x] Consume codes atomically, reject simultaneous reuse, validate PKCE before issuing credentials,
  and bind consent/code/refresh to the account-owned current channel generation.
- [x] Rotate refresh tokens atomically, bound lifetime by the original family, and revoke the family
  on replay. Check family state during access-token verification and logout/channel removal.
- [x] Persist a private RSA signing key with mode 0600 and exclusive first creation. Publish only the
  public JWK, validate `typ=at+jwt`, and reject arbitrary key-header URLs and algorithm confusion.
- [x] Implement real salted password accounts, bounded PBKDF2 workers, generic login errors and
  opt-in registration. Include no default user, password, anonymous OAuth login or test IdP in runtime.
- [x] Render informed consent with client name, exact ID, resource, scopes and callback. Require owner session, exact Origin, CSRF and secure
  cookies, and prevent fixation, account mixing, framing and open redirects.
- [x] Support pre-registration, safe HTTPS CIMD and disabled-by-default DCR. Check public DNS and
  actual connected IP, retain original TLS/SNI hostname, and reject redirects and secondary URL fetches.
- [x] Bound CIMD bytes, time, concurrency and cache. Validate client ID, registered redirects and
  supported public authentication methods without pretending to implement private_key_jwt.
- [x] Bound durable accounts, clients, transactions, code/refresh records and grants, with atomic
  capacity checks and expiry cleanup. Test restart and concurrent consumption.

## Game integration and user-visible behavior

- [x] Implement `legacy`, `oauth` and `dual` with fail-fast cross-validation. In dual, offer separate
  Token/OAuth choices in one running process and isolate both credential namespaces.
- [x] Preserve Token UUID storage, original MCP bearer copy options, gameplay and reconnect behavior.
  OAuth failures do not affect Token sessions and do not automatically switch the user's chosen mode.
- [x] Implement a confidential browser BFF with state, nonce, PKCE, issuer verification, secure cookie
  rotation, authenticated consent and durable fixed session expiry.
- [x] Require cookie-bound, origin-bound, single-use expiring WebSocket tickets for OAuth browser
  streams. Consume tickets atomically and require a new ticket for each reconnect.
- [x] Keep account ownership server-side. Share one active channel between tabs of the same account,
  preserve it during grace, create a fresh generation after teardown/restart and require fresh consent.
- [x] Revoke grants and AS families on logout, account switch and channel removal. Close live browser
  streams, cancel reconnect work and clear frontend player/map/session state.
- [x] Send no MCP bearer, refresh, ID token, provider token or confidential client secret to the OAuth
  frontend. OAuth copy UI contains endpoint-only connection instructions. Token UI intentionally
  retains its bearer copy flow.
- [x] Keep LocalProvider and player authority anchored to the owning Session. OAuth does not introduce
  a second provider protocol or let tool arguments choose another player's session.
- [x] Prove same-owner/different-client grants retain distinct scopes and cannot cross reverse-call
  sessions or notifications. Add a regression for revoked scopes and renewed credentials.
- [x] Supply complete embedded and external example environments, canonical game resource/issuer,
  persistent volume ownership, secure proxy routes and deployment instructions.

## SEC-01 through SEC-28: hostile protocol cases

The referenced suites are `tests/oauth/test_resource_server.py`, `test_embedded.py` and
`test_client_metadata.py`, plus `tests/gateway/test_channel.py`, `test_security.py` and `test_gateway.py`.

- [x] SEC-01: reject none/HS-RS confusion, bad signatures, unknown kid and malicious jku/x5u.
  Evidence: `test_sec01_jwt_signatures_rollover_and_header_attacks`.
- [x] SEC-02: reject expired/future/missing-exp, malformed claims, wrong issuer and audience before
  provider execution. Evidence: `test_sec02_claims`, `test_guard_identity_changed_and_invalid_session_cleanup`.
- [x] SEC-03: reject OIDC ID tokens and upstream tokens at MCP. Evidence: signing-key type tests,
  `test_oidc_confidential_client_and_nonce`, `test_sec02_claims`.
- [x] SEC-04: deny another owner's channel and addressed path without exposing registries or calling
  that provider. Evidence: `test_sec04_discovery_hybrid_and_authorization` and game isolation suite.
- [x] SEC-05: reject provider credentials at MCP and OAuth credentials at provider/browser-ticket
  boundaries. Evidence: gateway security suite and `test_game17_dual_oauth_and_token_isolation`.
- [x] SEC-06: attempt session theft across every bound field and GET/POST/DELETE before manager
  admission. Evidence: `test_sec06_stolen_session_all_identity_fields_denied_before_manager`, all five identity-field permutations across GET/POST/DELETE.
- [x] SEC-07: stop new stream chunks after revoke/expiry and safely handle reconnect/disconnect.
  Evidence: `test_sec06_guard_all_http_methods_and_revocation` and guarded game stream tests.
- [x] SEC-08: code replay and concurrent exchange issue at most one credential set, reject missing,
  plain and wrong PKCE. Evidence: hostile token tests and concurrent code/refresh tests.
- [x] SEC-09: reject wildcard/prefix/fragment/query-modified callbacks and open redirects.
  Evidence: `test_hostile_authorization`, `test_invalid_client_metadata`.
- [x] SEC-10: reject altered/reused state, login fixation, consent CSRF and another account's consent.
  Evidence: login/consent CSRF tests and game callback/account-switch tests.
- [x] SEC-11: reject resource omission/change, scope elevation and issuer mix-up.
  Evidence: hostile authorization/exchange and browser signed-ID-token tests.
- [x] SEC-12: reject refresh theft/reuse, revoke family and prevent privilege enlargement.
  Evidence: refresh binding/capacity and complete refresh/reuse/logout tests.
- [x] SEC-13: block localhost, metadata-service, private/ULA/mapped IPs and DNS rebinding before
  connection. Evidence: `test_sec13_nonpublic_addresses`, DNS pin/actual-peer tests, and
  `test_sec13_provider_schema_references_never_retrieve` for input/output $ref/$dynamicRef, loopback,
  cloud metadata, HTTPS, file URLs and missing anchors. Internal/root-ID references remain valid.
- [x] SEC-14: reject intranet redirects, giant/invalid documents, timeout and mismatched client IDs.
  Evidence: bounded metadata fetch tests, with no logo/JWKS/secondary URL fetching.
- [x] SEC-15: keep DCR opt-in, quotas finite and hostile client metadata rejected.
  Evidence: DCR, disabled registration, store capacity and CIMD cache tests.
- [x] SEC-16: prove IP/client/principal/username budgets resist rotating-IP brute force, enumeration,
  consent flooding and token flooding, with bounded memory and progressive Retry-After.
  Evidence: `test_rate_limits.py`, `test_sec16_account_budget_prevents_rotating_address_password_work`,
  verified client/principal limits, password-worker saturation and code non-consumption tests.
- [x] SEC-17: reject query bearer, ambiguous cookies, duplicate authorization/parameters, CRLF,
  malformed Unicode and wrong content types. Evidence: credentials and fixed-seed malformed-input fuzz.
- [x] SEC-18: ignore spoofed Host/forwarded authority for canonical discovery and redirects, retain
  Origin policy and require configured proxy trust. Evidence: canonical URL and HTTPS proxy smoke.
- [x] SEC-19: bound token/header/JSON/JWKS size, depth, time and concurrency.
  Evidence: bounded download, token parsing, malformed-input fuzz and transport limit suites.
- [x] SEC-20: verify trusted JWKS rollover, issuer mismatch and TLS/network failures fail closed.
  Evidence: JWT rollover, introspection and CIMD peer verification tests.
- [x] SEC-21: keep credentials/account details out of admin, health, errors and access logs.
  Evidence: version/admin tests, websocket query redaction and nginx log assertions.
- [x] SEC-22: deny channel/grant replacement races and stream/dispatch identity changes.
  Evidence: `test_sec22_tool_reauthorization_cannot_change_its_channel_or_owner`, stream reauth test.
- [x] SEC-23: exercise stateful/stateless crossed with JSON/SSE through the official MCP client.
  Evidence: `test_oauth_official_mcp_client_round_trip`, HTTP method guard suite.
- [x] SEC-24: preserve Token, custom Authenticator/Settings and swappable OAuth strategies.
  Evidence: existing gateway extension/security suites and OAuth injection tests.
- [x] SEC-25: explicitly attack reverse sampling/elicitation and resource subscriptions with multiple
  OAuth sessions, assert zero cross-session call/progress/notification fan-out.
  Evidence: `test_sec25_oauth_reverse_calls_cannot_choose_another_session` and
  `test_sec25_oauth_progress_and_resource_events_remain_session_scoped`.
- [x] SEC-26: verify secure HttpOnly cookies, CSRF, CSP/frame-ancestors and visible client consent.
  Evidence: AS CSRF suite, browser smoke, prefixed-issuer first/repeated-login consent and game CSP/origin tests.
- [x] SEC-27: return neutral no-store 401/403/503 with valid non-injectable WWW-Authenticate.
  Evidence: credentials, canonical challenge and unavailable-verifier tests.
- [x] SEC-28: verify actual HTTPS nginx routes for well-known, OAuth, MCP and browser endpoints.
  Evidence: demo `tests/e2e/proxy.py`, canonical public resource/challenge and redacted logs.

## GAME-01 through GAME-24: game isolation cases

Evidence lives in demo `tests/test_oauth_gateway.py`, `tests/test_embedded_oauth.py`,
`client/tests` test suites and `tests/e2e/embedded-browser-smoke.mjs`.

- [x] GAME-01: invalid OAuth/BFF/grants configuration fails startup, Token starts without IdP.
- [x] GAME-02: another account's UUID/ticket/channel never accesses its player or LocalProvider.
- [x] GAME-03: foreign/absent browser Origin, cookies without a valid ticket and CSWSH are denied.
- [x] GAME-04: ticket race/replay/expiry/logout permits only one atomic authorized consumption.
- [x] GAME-05: bad code/state/nonce/PKCE/issuer and fixation never create or overwrite owner sessions.
- [x] GAME-06: account switch revokes the previous channel and clears previous player UI state.
- [x] GAME-07: logout/teardown/reconnect races deny new work, with admitted work policy documented.
- [x] GAME-08: OAuth browser payload/copy UI never exposes bearer/provider/AS secrets.
- [x] GAME-09: spoofed Host/forwarded headers cannot alter the endpoint or callback authority.
- [x] GAME-10: same-account clients have scoped, generation-bound grants and isolated reverse calls.
- [x] GAME-11: malformed/oversized/spamming websocket frames fail safely without harming peers.
- [x] GAME-12: player authority remains the owning Session for login/get_player/move/attack.
- [x] GAME-13: two tabs/two hosts, restart and grace follow the explicit shared-account-channel policy.
- [x] GAME-14: verify CSP/CORS/CSRF, exact redirects, secure cookies and query-redacted proxy logs.
- [x] GAME-15: Token localStorage, original snapshots, MCP token and copy UI keep their contract.
- [x] GAME-16: OAuth bearer cannot replace a browser stream ticket or private provider credential.
- [x] GAME-17: simultaneous Token/OAuth players share one process and retain isolated tools/players.
- [x] GAME-18: unavailable IdP/failed callback does not disable Token or trigger automatic downgrade.
- [x] GAME-19: reject cross-mode credentials and the internal static token of an OAuth channel.
- [x] GAME-20: explicit Token/OAuth/Token switch cancels pending connections and clears old state.
- [x] GAME-21: Token has original four copy options, OAuth has endpoint-only authorization options.
- [x] GAME-22: exercise all game modes and correct/incorrect allow-static configurations.
- [x] GAME-23: cookie plus Token UUID or identical display names never merges ownership namespaces.
- [x] GAME-24: OAuth expiry/refresh/revocation never revokes or upgrades the parallel Token channel.

## Provider SDK cross-repository contract

- [x] SDK-01: JavaScript SDK has zero runtime dependencies and carries no OAuth client flow.
- [x] SDK-02: real SDK WebSocket provider handles Token and OAuth official MCP clients through the
  same private provider protocol. Evidence: core `make sdk-smoke`.
- [x] SDK-03: reject cross-channel/provider/internal-token credential use before provider dispatch.
  Evidence: `tests/e2e/sdk_run.py` assertions and gateway credential tests.
- [x] SDK-04: preserve exported provider API/types, ESM build, declaration output and npm package.
  Evidence: SDK lint/typecheck/39-test coverage/pack gates and real providers loaded from the final
  extracted npm tarball. The SDK is distributed as source ESM with no compilation build step.
- [x] SDK-05: keep optional private-URL reconnect rotation out of scope unless needed by a real
  consumer, and document why the unchanged provider API needs no OAuth-driven release.

## HOST-01 through HOST-12: actual client interoperability

- [x] HOST-01: dual-mode public resource metadata responds 200 with exact configured HTTPS resource.
- [x] HOST-02: anonymous MCP responds 401 with discovery challenge while Token remains usable.
- [x] HOST-03: discovery advertises S256 and implemented CIMD/DCR/pre-registration methods.
- [x] HOST-04: real AS code/PKCE/resource exchange produces the correct audience and single-use code.
- [x] HOST-05: official MCP host initializes/lists tools and runs login/move/get_player on its channel.
- [x] HOST-06: simultaneous Token and OAuth clients play in the same application without crossover.
- [x] HOST-07: wrong issuer/subject/client/channel/path cannot read or change another player.
- [x] HOST-08: serialize securitySchemes and return tool _meta authentication challenge on missing
  scopes. Local Inspector can list and call tools. Real host reauthentication UI remains unverified.
- [x] HOST-09: refreshed token works, replay revokes the family and logout denies subsequent calls.
- [x] HOST-10: actual local HTTPS proxy returns canonical public discovery/redirect URLs, tested
  independently of remote account connectivity.
- [ ] HOST-11: real ChatGPT account/workspace login and tools/list/call on a deployed authorized
  HTTPS endpoint. NOT TESTED, no account or deployed final endpoint is available to this session.
- [ ] HOST-12: record each other real host separately. Inspector 2.10.1 and official Python MCP 2.3.0
  are locally tested. Real Claude/Cursor/Codex account connections are NOT TESTED.

## UPG-01 through UPG-20: dependencies, artifacts and supply chain

No dependency report/inventory files are added to the repos, per the user's explicit instruction.
Temporary registry/audit/scan evidence belongs in `/tmp`. Operational constraints belong in existing
security/deployment/testing documentation.

- [x] UPG-01: verify current stable direct/dev/build package releases in both Python and npm registries
  with source URL/date, and explain each compatibility constraint.
- [x] UPG-02: inspect every Python lock transitive and isolated-build dependency, resolve compatible
  upgrades, and verify locked clean installs across Python 3.12/3.13/3.14.
- [x] UPG-03: inspect game npm transitives, install with npm ci, verify lock integrity and actual build.
- [x] UPG-04: inspect SDK npm transitives, npm ci and pack, and retain zero runtime dependencies.
- [x] UPG-05: adapt and test actual MCP 2.3 SDK auth/transport/session APIs, avoid unpublished snippets.
- [x] UPG-06: exercise upgraded FastAPI/Starlette/Pydantic/HTTP client routes, lifespan, CORS and errors.
- [x] UPG-07: validate current uvicorn/websocket size, origin, disconnect and query-redaction behavior.
- [x] UPG-08: boot actual Phaser game and verify login, map, movement, HUD and both auth choices.
- [x] UPG-09: game frontend build, Vitest/V8/jsdom coverage and lint pass with the selected lock.
- [x] UPG-10: SDK Biome/types/build/pack/Vitest coverage pass with the selected lock.
- [x] UPG-11: repeat all Python lint/100% branch gates on 3.12, 3.13 and 3.14 after final edits.
- [x] UPG-12: repeat Node minimum, active LTS and supported-current clean build/test/pack after edits.
- [x] UPG-13: rebuild Python 3.14 slim/Node images, verify non-root health and HTTPS OAuth/Token smoke.
- [x] UPG-14: pin CI tool/action versions, use minimal permissions and add coordinated immutable
  three-repo SHA inputs with full integration tests. Do not execute publishing steps.
- [x] UPG-15: repeat pip/npm vulnerability audit and document exploitable unresolved findings.
- [x] UPG-16: repeat SAST, secret and container scans, remove unused privileged executables and review
  raw Debian alerts without hiding packages or claiming a zero-CVE base.
- [x] UPG-17: Token baseline regression, UUID/stream/copy/tools and actual gameplay remain working.
- [x] UPG-18: upgraded OAuth discovery/PKCE/identity/gameplay/logout passes actual local AS smoke.
- [x] UPG-19: upgraded dual mode plus real JavaScript provider passes cross-repository isolation smoke.
- [x] UPG-20: rebuild final wheel/sdist/npm tarball/Docker, inspect contents/version and record SHA256
  in temporary evidence. Test installed artifacts, not only editable source.

## Final documentation and review gate

- [x] Update every new/changed GatewaySettings field in .env.example and docs/configuration.md, and
  every game OAuth setting in all applicable env examples and game documentation.
- [x] Keep README, architecture, OAuth, security, extension, deployment and testing guides consistent
  with the final module paths, defaults, routes, denial semantics and persistence model.
- [x] Keep AGENTS.md concise with pointers to OAuth modules, rate/grant invariants, this checklist,
  coordinated CI and commands. Do not duplicate full behavior documentation in the map.
- [x] Review all new code for English names, rare meaningful comments, independent responsibilities,
  empty __init__ modules, no obsolete compatibility paths and no semicolon-separated prose.
- [x] Repeat formatter/lint, all required coverage gates, installed-artifact and local integration
  tests only after the final functional changes, then update the evidence and statuses here.
- [x] Review the complete diff in all three feature branches for unintended generated files, secrets,
  dead code and missing docs. Leave the work uncommitted and unpushed for the maintainer.
- [x] Report concrete results and remaining external checks honestly. Never describe local protocol
  coverage as proof that a real ChatGPT/Claude account connection or remote GitHub CI succeeded.

## Exact game test references

All rows below refer to executed tests. `oauth_gateway` means demo
`tests/test_oauth_gateway.py`, `embedded_game` means `tests/test_embedded_oauth.py`.
The final execution summary below records the last complete gates.

| Requirement | Executable evidence |
|---|---|
| GAME-01 | oauth_gateway::test_game01_invalid_app_config, test_config_modes_and_cross_validation |
| GAME-02 | oauth_gateway::test_game17_dual_oauth_and_token_isolation, test_game03_04_csrf_ticket_replay_and_cookie_binding |
| GAME-03 | oauth_gateway::test_game03_04_csrf_ticket_replay_and_cookie_binding |
| GAME-04 | oauth_gateway::test_browser_store_expiry_capacity_restart_and_atomic_consume, test_game03_04_csrf_ticket_replay_and_cookie_binding |
| GAME-05 | oauth_gateway::test_game05_login_state_callback_and_idp_failures, test_game05_storage_failure_after_verified_callback_does_not_create_owner |
| GAME-06 | oauth_gateway::test_game06_account_switch_and_channel_generation |
| GAME-07 | oauth_gateway::test_guarded_websocket_stops_after_revocation, embedded_game::test_embedded_channel_expiry_and_revocation |
| GAME-08 | oauth_gateway::test_game08_oauth_stream_with_real_websocket_scope, client/tests/auth.test.js |
| GAME-09 | oauth_gateway::test_game17_dual_oauth_and_token_isolation, tests/e2e/proxy.py canonical authority assertions |
| GAME-10 | oauth_gateway::test_game10_same_account_clients_keep_distinct_granted_scopes, embedded_game::test_game10_refresh_cannot_recreate_revoked_consent_or_scopes, core SEC-25 tests |
| GAME-11 | oauth_gateway::test_game11_hostile_websocket_messages |
| GAME-12 | oauth_gateway::test_game17_dual_oauth_and_token_isolation, tests/test_gateway.py::test_provider_serves_gameplay_tools, tests/e2e/gameplay.py |
| GAME-13 | oauth_gateway::test_game06_account_switch_and_channel_generation, test_ticket_cookie_mismatch_missing_channel_and_disconnect_tracking |
| GAME-14 | oauth_gateway::test_game17_dual_oauth_and_token_isolation, test_game03_04_csrf_ticket_replay_and_cookie_binding, tests/e2e/proxy.py |
| GAME-15 | tests/test_gateway.py::test_session_handshake_sends_the_stable_connect_info, client/tests/net.test.js and auth.test.js |
| GAME-16 | oauth_gateway::test_game17_dual_oauth_and_token_isolation, test_game16_browser_endpoint_budgets, test_game16_verified_account_budget_survives_address_rotation |
| GAME-17 | oauth_gateway::test_game17_dual_oauth_and_token_isolation, embedded/external Chrome smokes |
| GAME-18 | oauth_gateway::test_game05_login_state_callback_and_idp_failures, client/tests/auth_selection.test.js |
| GAME-19 | oauth_gateway::test_game17_dual_oauth_and_token_isolation, test_oauth_only_rejects_legacy_socket_and_channel_revoke_branches |
| GAME-20 | client/tests/auth_selection.test.js, embedded/external Chrome smokes |
| GAME-21 | client/tests/auth.test.js, embedded/external Chrome copy UI assertions |
| GAME-22 | oauth_gateway::test_config_modes_and_cross_validation, test_oauth_only_rejects_legacy_socket_and_channel_revoke_branches |
| GAME-23 | oauth_gateway::test_game17_dual_oauth_and_token_isolation, test_game06_account_switch_and_channel_generation |
| GAME-24 | embedded_game::test_embedded_game_real_account_dcr_pkce_refresh_logout_and_token, embedded Chrome refresh/replay/logout assertions |

## Final local execution evidence: 2026-10-09

The tested implementation is the final uncommitted worktree on `feat/oauth-embedded` in all three
repositories. These results do not describe remote GitHub jobs or published packages. Gateway is
prepared as 0.0.7. Game and provider SDK remain 0.0.3. There are 821 tests across the four suites,
with repeated runs for the supported interpreter/runtime matrix.

| Gate | Final result | Temporary execution evidence |
|---|---|---|
| Gateway Python | 522 passed on each of 3.12/3.13/3.14, 100% lines and branches, lint and format passed | `/tmp/oauth-core-{version}-final.log`, coverage XML beside each log |
| Game Python | 218 passed on each of 3.12/3.13/3.14, 100% lines and branches, lint and format passed | `/tmp/oauth-game-{version}-final.log` |
| Game JavaScript | 42 passed, 100% of the configured helpers/net/LoginScene coverage scope, frontend build passed | `/tmp/oauth-game-client-final.log` |
| Provider JavaScript | 39 passed, 100% lines/branches/functions/statements, Biome and declaration checks passed | `/tmp/oauth-sdk-final.log` |
| Node clean-install matrix | SDK 22.12.0/22.23.3/24.21.0/26.11.1 and game 22.22.2/24.21.0/26.11.1 all passed | `/tmp/oauth-node-matrix-final.log` and per-runtime logs |
| Actual embedded AS + Chrome | Accounts, OpenID nonce, consent, DCR, host PKCE, refresh/replay, logout and simultaneous Token passed. Actual position changes asserted for both players. Zero browser errors | `/tmp/oauth-embedded-complete-final.log` |
| MCP Inspector | Pinned 2.10.1 listed the ten game tools against the actual embedded server | Same embedded log |
| External IdP + Chrome | Separate test IdP, official MCP client, both players actually move, logout and Token isolation passed | `/tmp/oauth-external-final.log` |
| Installed Docker wheels + nginx HTTPS | Canonical discovery/challenge, public JWKS, browser/host PKCE, tickets, gameplay and revocation passed. UID 10001, zero capabilities, no SUID/SGID, unused utilities absent, no sensitive queries logged | `/tmp/oauth-proxy-complete-final.log` |
| Installed bare gateway | Health 200, OAuth discovery 404 by default, read-only/no-capability startup passed | `/tmp/oauth-core-image-health-final.json` |
| Installed versions/imports | Gateway 0.0.7 and game 0.0.3 loaded from site-packages. No installer or obsolete import package in runtime | `/tmp/oauth-{core,game}-installed-final.log` |
| Packaged npm provider | Actual providers loaded from the extracted 0.0.3 tarball, Token/OAuth calls passed, wrong-channel/internal-token attempts denied | `/tmp/oauth-sdk-installed-final.log` |
| Bounded load | 1000 channels/principals, channel/grant/session limits and cleanup passed, wrong owner denied. Uses test verifier and memory grants, not a JWT/SQLite/network throughput claim | `/tmp/oauth-load-final.log` |
| Complete registry verification | 182 distinct packages/tools, 296 runtime/dev lock entries plus six isolated-build packages. Baseline versions, roles, hashes and parent constraints recorded. Unsupported transitive major overrides omitted | `/tmp/oauth-complete-registry-check.json` |
| Vulnerability audits | No known Python application/build or npm vulnerabilities found | `/tmp/oauth-{core,game,build}-python-audit.json`, `/tmp/oauth-{game,sdk}-npm-audit.json` |
| SAST | Bandit 1.9.4, no medium/high issues. 16 gateway and two game low-severity enum/default heuristics reviewed | `/tmp/oauth-{core,game}-sast-final.json` |
| Secrets | Gitleaks 8.30.1, zero findings, offline read-only scan | `/tmp/oauth-secrets-final.json` |
| Containers | Trivy 0.75.0, no critical or Python package findings. Each image retains 44 high/58 medium/61 low/3 unknown OS-package findings, eight distinct high CVEs, applicability/hardening reviewed | `/tmp/oauth-final-image-reports/{core,game}.json`, [security review](security.md#container-audit-scope) |
| Workflow syntax | All six workflows passed offline actionlint. Coordinated dispatch requires three immutable SHAs. Demo CI requires GATEWAY_INTEGRATION_SHA | `/tmp/oauth-workflows-final.log` |
| Distribution contents and reproducibility | Wheel/sdist/npm contents and versions checked, required frontend present, missing bundle rejected, repeated Python builds identical, all installed Python module hashes match the final worktree | `/tmp/oauth-final-artifact-evidence.json`, `/tmp/oauth-missing-bundle-final.json` |
| Final diff/configuration review | All three diffs checked, English code and empty package initializers checked, env/config documentation synchronized, generated coverage changes excluded | `/tmp/oauth-final-review.json` |

Python coverage includes the full library and game packages. The frontend unit gate covers helpers, networking and LoginScene, excluding the live-scene
animation helper. The Phaser view layer is checked by the actual browser builds/smokes and has no claim
of 100% unit branch coverage. MCP SDK logging deprecation warnings are upstream warnings, not resource
leaks. The Python matrix also treats ResourceWarning and unraisable exceptions as errors.

This snapshot predates publication. HOST-11/HOST-12 require authorized real host accounts and a
deployed final endpoint and remain NOT TESTED. Current consumers install published registry packages.
The coordinated acceptance workflow uses complete commits to select repository checkouts, without
substituting source for the game's locked PyPI dependency. Do not infer remote host success from
the local 100% coverage gate. See the post-release record below for the published correction.


## Post-release registration correction

- [x] Separate the absolute login/consent deadline (600 seconds by default, bounded at 1800) from
  authorization code expiry (120 seconds, bounded at 300). Keep expired transactions invalid even
  when old cookies are replayed, and never extend their deadline through form refresh or retries.
- [x] Keep login/consent CSRF cookies valid through the authorization window while preserving exact
  Origin, duplicate-cookie and field checks. Verify real registration after a delayed form and
  delayed consent, and retain short authorization-code expiry.
- [x] Declare account registration availability in the identity contract. Hide the registration
  button when disabled and reject forged registration before invoking the identity strategy.
- [x] Render neutral credential errors and expired-login explanations as no-store HTML, without
  echoing credentials or distinguishing unknown accounts, wrong passwords and duplicate signup.
  Display and enforce the shipped username and password requirements in the form.
- [x] Extend the game's BFF state and cookie deadline independently to 600 seconds, configurable
  up to 1800. Reject expired-state replay without creating sessions or calling the identity client.
- [x] Verify 529 gateway and 225 game tests with 100% line/branch coverage on Python 3.12/3.13/3.14.
  The game still installs the published gateway 0.0.7 from PyPI. No unpublished dependency was
  substituted. Logs: `/tmp/oauth-{core,game}-{3.12,3.13,3.14}-registration-fix.log`.
- [x] Verify the corrected AS forms in actual local Chrome over HTTPS with real SQLite accounts:
  disabled registration, username validation, neutral credential error, CSRF rotation and retry,
  successful signup and consent. No browser script errors occurred.
- [x] Publish the corrected gateway before updating the game's gateway minimum/lockfile, then
  repeat the combined game/browser/HTTPS smoke with that published package. Gateway 0.0.8 was
  published through the release workflow before the game changed to `mcp-gtw>=0.0.8`.

### Published 0.0.8 verification

- [x] Merge the registration correction and release PRs after all three supported Python CI checks.
  [Correction PR](https://github.com/mcp-gtw/mcp-gtw/pull/5),
  [release PR](https://github.com/mcp-gtw/mcp-gtw/pull/6),
  [successful publication](https://github.com/mcp-gtw/mcp-gtw/actions/runs/38018886791).
- [x] Verify PyPI wheel and sdist availability, the wheel's SHA-256 and all 54 Python source modules
  against the reviewed release source. Evidence: `/tmp/oauth-008-pypi-evidence.json`.
- [x] Upgrade only the game's gateway dependency to published 0.0.8, retaining registry URLs and
  artifact hashes. Verify 225 game tests, lint, formatting and 100% line/branch coverage on Python
  3.12/3.13/3.14. Logs: `/tmp/oauth-game-{3.12,3.13,3.14}-008-published.log`.
- [x] Repeat real Chrome signup, consent, host PKCE, MCP gameplay, refresh/replay rejection, logout
  and simultaneous Token against the installed published gateway. Inspector 2.10.1 lists the ten
  game tools. Evidence: `/tmp/oauth-008-embedded.log`.
- [x] Build the game independently with Python 3.14 slim and published PyPI dependencies. Verify
  gateway 0.0.8 loads from runtime site-packages. Repeat real embedded authorization, DCR, browser
  and host PKCE, tickets, gameplay, logout and Token through nginx HTTPS, including forged Host
  checks and non-root runtime hardening. Evidence: `/tmp/oauth-008-docker.log`,
  `/tmp/oauth-008-proxy.log`.

Production deployment and real ChatGPT/Claude workspace acceptance remain separate, unexecuted
checks. These release and local integration results do not claim validation of the deployed server.
