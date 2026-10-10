# Local browser and provider integration

## Cross-origin consent browser gate

Install the isolated pinned browser toolchain with `npm --prefix tests/e2e/browser ci`, then
`tests/e2e/browser/node_modules/.bin/playwright install chromium`. On Linux, use `install --with-deps chromium`
to install the browser system dependencies too. Run `make consent-smoke`. `TEST_CHROME`
can select an installed Chrome executable. Ports 19502 and 19503 must be free.

The runner starts two actual loopback HTTPS servers with temporary TLS certificates. The real
embedded AS, SQLite state, password identity and signing key handle signup, consent and code
exchange. A separate callback receiver on a different host and port handles redirects. The fixture
consent policy explicitly approves authenticated test accounts and has no game-channel behavior.

Chromium must follow approval and denial after one click, preserve issuer/state, reject replay,
and block an altered form destination that is a different registered callback. The blocked form
cannot consume the valid transaction. Registration and code lifetime remain real server behavior.
No HTTP interceptor replaces redirects, and no authorization code is sent to a remote service.
The fixture uses private temporary state and stops both servers on completion. This test runs on
every gateway PR and in the publication workflow. Actual ChatGPT/Claude workspaces remain separate
acceptance checks.

## Provider transport smoke

Keep the `mcp-gtw-provider` checkout beside this repository. Run `make sdk-smoke` with supported Node 22/24/26 and the Python development environment installed. Port 19480 must be free; the runner refuses to reuse an occupied port.

This starts a loopback-only gateway with an explicitly injected test verifier and grants, two real JavaScript providers and the official Python MCP client. Each provider registers and executes an identity tool. It verifies OAuth and static credentials separately, denies access across channels, and rejects the internal MCP token of the OAuth-owned channel. Provider source and types are unchanged. Cryptographic JWT verification is covered by the gateway tests and the game HTTPS browser smoke; this fixture specifically tests public/private transport integration.

Secrets are generated at runtime and written only to a private temporary file. Subprocesses are stopped and temporary credentials/logs removed. No remote application is contacted and no npm package is published.

To verify the packaged SDK, extract its npm tarball into a temporary directory and run
`uv run python tests/e2e/sdk_run.py --provider-module /absolute/path/package/src/index.js`.
The providers then load that exact artifact module. The summary records `installedArtifact=true`.
