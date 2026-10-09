# JavaScript provider integration

Keep the `mcp-gtw-provider` checkout beside this repository. Run `make sdk-smoke` with supported Node 22/24/26 and the Python development environment installed. Port 19480 must be free; the runner refuses to reuse an occupied port.

This starts a loopback-only gateway with an explicitly injected test verifier and grants, two real JavaScript providers and the official Python MCP client. Each provider registers and executes an identity tool. It verifies OAuth and static credentials separately, denies access across channels, and rejects the internal MCP token of the OAuth-owned channel. Provider source and types are unchanged. Cryptographic JWT verification is covered by the gateway tests and the game HTTPS browser smoke; this fixture specifically tests public/private transport integration.

Secrets are generated at runtime and written only to a private temporary file. Subprocesses are stopped and temporary credentials/logs removed. No remote application is contacted and no npm package is published.
