# Deployment

## Publishing to PyPI

The package is published to PyPI as `mcp-gtw` (the import package stays `mcp_gtw`). Pushing a
version tag runs [`release.yml`](../.github/workflows/release.yml): it checks the tag matches the
`pyproject.toml` version, runs lint and the coverage gate, builds, and publishes with
`uv publish --trusted-publishing always`.

```bash
make version v=X.Y.Z   # bumps pyproject.toml
git tag vX.Y.Z
git push origin vX.Y.Z
```

Publishing is token-less via [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/) —
**no secret** is stored. The workflow grants `id-token: write` and GitHub's OIDC identity
authenticates the upload. PyPI supports a **pending publisher**, so register it before the first
release (pypi.org → Account → Publishing → Add a pending publisher): PyPI project `mcp-gtw`, owner
`mcp-gtw`, repository `mcp-gtw`, workflow `release.yml`. The first tag then creates the project.

## Docker

The [`Dockerfile`](../Dockerfile) builds an image that serves the bare gateway through the bundled
runner (`python -m mcp_gtw.main`), which reads `GatewaySettings` and applies the transport frame
limit (`ws_max_size` = `GATEWAY_MAXIMUM_WEBSOCKET_MESSAGE_BYTES`) and `GATEWAY_MAXIMUM_CONCURRENT_CONNECTIONS`.
Prefer this entrypoint over launching bare `uvicorn`, which would not apply those limits.

```bash
docker build -t mcp-gtw .
docker run --rm -p 8000:8000 \
  -e GATEWAY_ALLOWED_PROVIDER_ORIGINS="https://app.example.com" \
  -e GATEWAY_ALLOWED_MCP_ORIGINS="" \
  -e GATEWAY_ADMIN_ENABLED="true" \
  -e GATEWAY_ADMIN_KEY="$(openssl rand -hex 16)" \
  mcp-gtw
```

Every `GATEWAY_*` setting from [configuration](configuration.md) is passed the same way.

### Docker Compose on a server

A ready [`docker-compose.yml`](../docker-compose.yml) is included. On the production host:

```bash
cp .env.example .env        # then edit .env with real origins, admin key, limits
docker compose up -d --build
docker compose logs -f
```

How it works on the server:

- **Config lives in `.env` on the host, not in the image.** Compose reads `.env` at `up` time and
  injects each `GATEWAY_*` value as a container environment variable, which `GatewaySettings` picks
  up. The file never enters the image (it is also in `.dockerignore`), so the same image is reusable
  and secrets like `GATEWAY_ADMIN_KEY` stay on the server. The `env_file` is marked `required: false`
  so Compose still starts if you configure via `environment:` or a secrets manager instead.
- **Image source.** `build: .` builds on the server from the checkout. For a cleaner pipeline, have CI
  publish an image and swap `build: .` for `image: <registry>/mcp-gtw:<tag>`, then
  `docker compose pull && docker compose up -d`.
- **Health.** The service has a `/health` healthcheck, so Compose reports readiness and
  `restart: unless-stopped` brings it back after a crash or reboot.
- **TLS.** The gateway speaks plain HTTP on `8000`. In production, bind it to loopback
  (`127.0.0.1:8000:8000`) and terminate TLS at a reverse proxy in front (see below) so `/mcp` and the
  `/provider` WebSocket are served over HTTPS/WSS.

An application built on the library ships its own image with its own entrypoint — the same pattern,
pointing `CMD` (and the compose `image`/`build`) at your `create_app()` module.

### Configuration, not `.env`, in the image

The image never contains a `.env`. `GatewaySettings` reads `GATEWAY_*` from the **environment**, so
config stays out of the build (the same image runs anywhere, only the variables change) and secrets
are never baked in. A `.env` is a local-development convenience only, and `.dockerignore` keeps it
out of the build context. To feed config at runtime, pick one:

- `-e GATEWAY_...=...` per variable, or `--env-file .env` — Docker reads the file **on the host** and
  injects the values as environment variables. This does not put the file inside the container.
- Mount one read-only if you prefer a file inside: `-v "$(pwd)/.env:/srv/.env:ro"`. The working
  directory is `/srv`, and `GatewaySettings` loads a `.env` found there.

## Reverse proxy

A complete, ready [`nginx.conf`](../nginx.conf) is included, written for an origin **behind
Cloudflare**: Cloudflare terminates TLS, so nginx serves plain HTTP, forwards Cloudflare's
`X-Forwarded-Proto`, serves your app's static frontend directly, and proxies only the dynamic
endpoints to the app. It is generic — a site, a game, any app — so adjust `server_name`, the `app`
upstream, the static `root`, and your own routes, and lock the origin to Cloudflare's IP ranges at
your firewall.

The essentials it encodes: HTTP for `/mcp` with `proxy_buffering off` (SSE) and a long read timeout,
WebSocket upgrades for `/provider` (and any WebSocket your app adds), and the `Authorization` and
`Origin` headers preserved so the gateway can authenticate and origin-check.

Use HTTPS and WSS in production so tokens and payloads are encrypted.

## Scaling

State (the WebSocket, tools, pending calls and MCP sessions) lives in memory, so a single process is
authoritative for its channels. The design scales to many thousands of channels in one process
because a single MCP server and session manager serve them all, routed by token.

To run multiple workers you need channel-affinity: either sticky sessions at the load balancer so a
provider and its MCP client land on the same worker, or a broker (Redis Streams, NATS) that relays
the `request` / `result` frames to the worker that owns the WebSocket. Do not run several Uvicorn workers
without one of these — a provider on worker A and a client on worker B would not find each other.

## Configuration in production

- Restrict `GATEWAY_ALLOWED_PROVIDER_ORIGINS` and `GATEWAY_CORS_ALLOW_ORIGINS` to your real origins.
- If the admin dashboard is enabled, set a strong `GATEWAY_ADMIN_KEY`.
- Tune the [limits](configuration.md): timeout, pending calls, channels and the offline grace.
- Front the service with TLS and keep `/mcp` and the WebSocket paths uncached.

See [security](security.md) for the full hardening checklist.
