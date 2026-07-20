from __future__ import annotations

import json

import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from support import FakeProviderWebSocket

from mcp_gtw.config import GatewaySettings
from mcp_gtw.gateway import Gateway


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=True
    )


def _provider_ws(token: str) -> FakeProviderWebSocket:
    return FakeProviderWebSocket(
        query_params={"token": token}, headers={"origin": "http://testserver"}
    )


async def _mcp_ping(client: httpx.AsyncClient, token: str, path: str = "/mcp") -> httpx.Response:
    return await client.post(
        path,
        headers={"Authorization": f"Bearer {token}"},
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
    )


async def test_provider_token_is_rejected_on_mcp(settings: GatewaySettings) -> None:
    gateway = Gateway(settings)
    app = gateway.create_app()
    channel = await gateway.create_channel()

    async with _client(app) as client:
        response = await _mcp_ping(client, channel.provider_token)

    assert response.status_code == 401


async def test_mcp_token_is_rejected_on_provider(settings: GatewaySettings) -> None:
    gateway = Gateway(settings)
    channel = await gateway.create_channel()
    websocket = _provider_ws(channel.mcp_token)

    await gateway.provider_endpoint(websocket)
    assert websocket.close_code == 1008
    assert websocket.accepted is False


async def test_removed_channel_tokens_no_longer_resolve(settings: GatewaySettings) -> None:
    gateway = Gateway(settings)
    app = gateway.create_app()
    channel = await gateway.create_channel()
    provider_token, mcp_token = channel.provider_token, channel.mcp_token
    await gateway.registry.remove_channel(channel.channel_id)

    websocket = _provider_ws(provider_token)
    await gateway.provider_endpoint(websocket)
    assert websocket.close_code == 1008

    async with _client(app) as client:
        assert (await _mcp_ping(client, mcp_token)).status_code == 401


async def test_cross_channel_path_is_rejected(settings: GatewaySettings) -> None:
    gateway = Gateway(settings)
    app = gateway.create_app()
    mine = await gateway.create_channel()
    other = await gateway.create_channel()

    async with _client(app) as client:
        response = await _mcp_ping(client, mine.mcp_token, path=f"/mcp/{other.channel_id}")

    assert response.status_code == 404


async def test_empty_and_whitespace_tokens_are_rejected(settings: GatewaySettings) -> None:
    gateway = Gateway(settings)
    app = gateway.create_app()

    empty_provider = _provider_ws("")
    await gateway.provider_endpoint(empty_provider)
    assert empty_provider.close_code == 1008

    async with _client(app) as client:
        assert (await _mcp_ping(client, "")).status_code == 401
        assert (await _mcp_ping(client, "   ")).status_code == 401


async def test_bearer_scheme_must_be_bearer(settings: GatewaySettings) -> None:
    gateway = Gateway(settings)
    app = gateway.create_app()
    channel = await gateway.create_channel()

    async with _client(app) as client:
        response = await client.post(
            "/mcp",
            headers={"Authorization": f"Basic {channel.mcp_token}"},
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        )

    assert response.status_code == 401


async def test_health_is_unauthenticated_and_robust(settings: GatewaySettings) -> None:
    gateway = Gateway(settings)
    app = gateway.create_app()

    async with _client(app) as client:
        response = await client.get(
            "/health",
            headers={"Authorization": "Bearer " + "x" * 100_000, "Origin": "http://evil"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["channels"] == 0


def _admin(settings: GatewaySettings) -> Gateway:
    return Gateway(settings.model_copy(update={"admin_enabled": True, "admin_key": "s3cret"}))


async def test_admin_rejects_hostile_keys_without_crashing(settings: GatewaySettings) -> None:
    gateway = _admin(settings)
    app = gateway.create_app()

    hostile = [
        "",
        "wrong",
        "x" * 8000,
        "' OR '1'='1",
        "../../etc/passwd",
        "s3cret\x00",
        "café",
        "😀",
        "s3creté",
    ]

    async with _client(app) as client:
        for key in hostile:
            assert (await client.get("/admin/stats", params={"key": key})).status_code == 403
            assert (await client.get("/admin", params={"key": key})).status_code == 403

        assert (await client.get("/admin/stats", params={"key": "s3cret"})).status_code == 200


async def test_mcp_survives_a_giant_authorization_header(settings: GatewaySettings) -> None:
    gateway = Gateway(settings)
    app = gateway.create_app()

    async with _client(app) as client:
        response = await _mcp_ping(client, "x" * 200_000)

    assert response.status_code == 401


async def _drain(gateway: Gateway, websocket: FakeProviderWebSocket) -> None:
    await gateway.provider_endpoint(websocket)


async def test_provider_pump_survives_a_battery_of_hostile_json(
    settings: GatewaySettings, move_tool: dict
) -> None:
    gateway = Gateway(settings)
    channel = await gateway.create_channel()
    websocket = _provider_ws(channel.provider_token)

    websocket.queue(
        "[" * 5000 + "]" * 5000,
        json.dumps({"type": "ping"}) + "trailing garbage",
        '{"type": "notify", "method": "notifications/progress", "params": {"progress": 1e999}}',
        '{"type": "register", "registry": "tools", "items": [{"name": 5}]}',
        "[1, 2, 3]",
        '{"type": "result", "requestId": 12345}',
        "\x00\x01 not json",
        json.dumps({"type": "register", "registry": "tools", "items": [move_tool]}),
    )

    await _drain(gateway, websocket)

    errors = [message for message in websocket.messages if message["type"] == "protocol.error"]
    assert len(errors) >= 6
    assert websocket.last("ack")["count"] == 1
    assert channel.provider_connected is False
