from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import ClassVar

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport
from starlette.requests import Request
from support import FakeProviderWebSocket

from mcp_gtw.authenticator import TokenAuthenticator
from mcp_gtw.config import GatewaySettings
from mcp_gtw.errors import ChannelCapacityError
from mcp_gtw.gateway import Gateway

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_SAMPLE_UUID = "550e8400-e29b-41d4-a716-446655440000"


class OwnTokenAuthenticator(TokenAuthenticator):
    """Objective 3: the client brings its own UUID token, validated by format and upserted."""

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


def _provider_ws(token: str) -> FakeProviderWebSocket:
    return FakeProviderWebSocket(
        query_params={"token": token}, headers={"origin": "http://testserver"}
    )


async def test_own_token_creates_a_channel_from_a_valid_uuid(settings: GatewaySettings) -> None:
    gateway = OwnTokenGateway(settings)

    await gateway.provider_endpoint(_provider_ws(_SAMPLE_UUID))

    channel = gateway.registry.resolve_provider_token(_SAMPLE_UUID)
    assert channel is not None
    assert gateway.registry.resolve_mcp_token(f"mcp-{_SAMPLE_UUID}") is channel


async def test_own_token_reuses_the_same_channel(settings: GatewaySettings) -> None:
    gateway = OwnTokenGateway(settings)

    await gateway.provider_endpoint(_provider_ws(_SAMPLE_UUID))
    first_id = gateway.registry.resolve_provider_token(_SAMPLE_UUID).channel_id

    await gateway.provider_endpoint(_provider_ws(_SAMPLE_UUID))
    assert gateway.registry.resolve_provider_token(_SAMPLE_UUID).channel_id == first_id
    assert gateway.registry.channel_count == 1


async def test_own_token_rejects_a_malformed_token(settings: GatewaySettings) -> None:
    gateway = OwnTokenGateway(settings)
    websocket = _provider_ws("token-paulo")

    await gateway.provider_endpoint(websocket)
    assert websocket.close_code == 1008
    assert websocket.accepted is False


async def test_own_token_denies_when_registry_is_full(settings: GatewaySettings) -> None:
    gateway = OwnTokenGateway(settings.model_copy(update={"maximum_channels": 1}))
    await gateway.create_channel()
    websocket = _provider_ws(_SAMPLE_UUID)

    await gateway.provider_endpoint(websocket)
    assert websocket.close_code == 1008


class LoginGateway(Gateway):
    """Objective 2: username/password authentication that mints a channel on success."""

    users: ClassVar[dict[str, str]] = {"alice": "s3cret"}

    def register_routes(self, app: FastAPI) -> None:
        super().register_routes(app)
        app.add_api_route("/login", self.login, methods=["POST"])

    async def login(self, request: Request) -> dict:
        try:
            body = await request.json()
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid request body") from None

        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="Invalid request body")

        expected = self.users.get(body.get("username"))

        if expected is None or not self.tokens.equals(body.get("password"), expected):
            raise HTTPException(status_code=401, detail="Invalid credentials")

        channel = await self.create_channel(metadata={"user": body["username"]})
        return {
            "channelId": channel.channel_id,
            "providerToken": channel.provider_token,
            "mcpToken": channel.mcp_token,
        }


def _asgi_client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def test_login_rejects_bad_credentials(settings: GatewaySettings) -> None:
    gateway = LoginGateway(settings)
    app = gateway.create_app()

    async with _asgi_client(app) as client:
        wrong = await client.post("/login", json={"username": "alice", "password": "nope"})
        unknown = await client.post("/login", json={"username": "ghost", "password": "x"})

    assert wrong.status_code == 401
    assert unknown.status_code == 401
    assert gateway.registry.channel_count == 0


async def test_login_mints_a_usable_channel(settings: GatewaySettings) -> None:
    gateway = LoginGateway(settings)
    app = gateway.create_app()

    async with _asgi_client(app) as client:
        response = await client.post("/login", json={"username": "alice", "password": "s3cret"})

    assert response.status_code == 200
    data = response.json()
    assert gateway.registry.resolve_provider_token(data["providerToken"]) is not None
    assert gateway.registry.resolve_mcp_token(data["mcpToken"]) is not None


@pytest.mark.parametrize(
    "token",
    [
        "",
        "short",
        "550e8400-e29b-41d4-a716",
        "'; DROP TABLE channels;--",
        " 550e8400-e29b-41d4-a716-446655440000",
        "550e8400-e29b-41d4-a716-44665544000g",
        "550e8400e29b41d4a716446655440000",
        "../../etc/passwd",
    ],
)
async def test_own_token_rejects_malformed_tokens(settings: GatewaySettings, token: str) -> None:
    gateway = OwnTokenGateway(settings)
    websocket = _provider_ws(token)

    await gateway.provider_endpoint(websocket)
    assert websocket.close_code == 1008
    assert websocket.accepted is False
    assert gateway.registry.channel_count == 0


async def test_own_token_distinct_uuids_are_distinct_channels(settings: GatewaySettings) -> None:
    gateway = OwnTokenGateway(settings)
    first = "550e8400-e29b-41d4-a716-000000000001"
    second = "550e8400-e29b-41d4-a716-000000000002"

    await gateway.provider_endpoint(_provider_ws(first))
    await gateway.provider_endpoint(_provider_ws(second))

    assert gateway.registry.channel_count == 2
    assert gateway.registry.resolve_provider_token(
        first
    ) is not gateway.registry.resolve_provider_token(second)


async def test_own_token_channel_id_does_not_leak_the_token(settings: GatewaySettings) -> None:
    gateway = OwnTokenGateway(settings)
    await gateway.provider_endpoint(_provider_ws(_SAMPLE_UUID))

    channel = gateway.registry.resolve_provider_token(_SAMPLE_UUID)
    assert channel.channel_id != _SAMPLE_UUID
    assert _SAMPLE_UUID not in channel.channel_id


async def test_own_token_concurrent_upsert_converges(settings: GatewaySettings) -> None:
    gateway = OwnTokenGateway(settings)
    authenticator = gateway.authenticator

    first, second = await asyncio.gather(
        authenticator.authenticate_provider(_provider_ws(_SAMPLE_UUID)),
        authenticator.authenticate_provider(_provider_ws(_SAMPLE_UUID)),
    )

    assert first is second
    assert gateway.registry.channel_count == 1


async def test_own_token_upsert_flood_is_capped(settings: GatewaySettings) -> None:
    gateway = OwnTokenGateway(settings.model_copy(update={"maximum_channels": 3}))

    for index in range(3):
        websocket = _provider_ws(f"550e8400-e29b-41d4-a716-{index:012x}")
        await gateway.provider_endpoint(websocket)
        assert websocket.accepted is True

    assert gateway.registry.channel_count == 3

    overflow = _provider_ws("550e8400-e29b-41d4-a716-ffffffffffff")
    await gateway.provider_endpoint(overflow)

    assert overflow.close_code == 1008
    assert gateway.registry.channel_count == 3


async def test_login_rejects_missing_fields(settings: GatewaySettings) -> None:
    gateway = LoginGateway(settings)
    app = gateway.create_app()

    async with _asgi_client(app) as client:
        assert (await client.post("/login", json={"username": "alice"})).status_code == 401
        assert (await client.post("/login", json={"password": "s3cret"})).status_code == 401
        assert (await client.post("/login", json={})).status_code == 401

    assert gateway.registry.channel_count == 0


async def test_login_rejects_malformed_body(settings: GatewaySettings) -> None:
    gateway = LoginGateway(settings)
    app = gateway.create_app()

    async with _asgi_client(app) as client:
        not_json = await client.post(
            "/login", content="not json", headers={"content-type": "application/json"}
        )
        not_object = await client.post("/login", json=["alice", "s3cret"])

    assert not_json.status_code == 400
    assert not_object.status_code == 400
    assert gateway.registry.channel_count == 0


async def test_login_does_not_enumerate_users(settings: GatewaySettings) -> None:
    gateway = LoginGateway(settings)
    app = gateway.create_app()

    async with _asgi_client(app) as client:
        wrong_password = await client.post("/login", json={"username": "alice", "password": "x"})
        unknown_user = await client.post("/login", json={"username": "ghost", "password": "x"})

    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json() == unknown_user.json()


async def test_login_mints_distinct_channels_each_time(settings: GatewaySettings) -> None:
    gateway = LoginGateway(settings)
    app = gateway.create_app()

    async with _asgi_client(app) as client:
        first = (
            await client.post("/login", json={"username": "alice", "password": "s3cret"})
        ).json()
        second = (
            await client.post("/login", json={"username": "alice", "password": "s3cret"})
        ).json()

    assert first["channelId"] != second["channelId"]
    assert first["providerToken"] != second["providerToken"]
    assert first["mcpToken"] != second["mcpToken"]
    assert gateway.registry.channel_count == 2


async def test_login_channel_authenticates_on_both_sides(settings: GatewaySettings) -> None:
    gateway = LoginGateway(settings)
    app = gateway.create_app()

    async with _asgi_client(app) as client:
        data = (
            await client.post("/login", json={"username": "alice", "password": "s3cret"})
        ).json()

    websocket = FakeProviderWebSocket(
        query_params={"token": data["providerToken"]}, headers={"origin": "http://testserver"}
    )
    await gateway.provider_endpoint(websocket)

    assert websocket.accepted is True
    assert gateway.registry.resolve_mcp_token(data["mcpToken"]) is not None
