from __future__ import annotations

import asyncio
import contextlib
import json
import re
from typing import Any

import httpx
import mcp.types as mcp_types
import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from httpx import ASGITransport
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.requests import Request
from support import FakeWebSocket

from mcp_gtw.config import GatewaySettings
from mcp_gtw.errors import GatewayConfigurationError, GatewayError
from mcp_gtw.gateway import SCOPE_CHANNEL_KEY, Gateway


class FakeBrowserWebSocket(FakeWebSocket):
    def __init__(self, query_params: dict[str, str], headers: dict[str, str]) -> None:
        super().__init__()
        self.query_params = query_params
        self.headers = headers
        self.accepted = False
        self._incoming: list[dict[str, Any]] = []

    def queue(self, *messages: str) -> None:
        for message in messages:
            self._incoming.append({"type": "websocket.receive", "text": message})

    def queue_bytes(self, payload: bytes) -> None:
        self._incoming.append({"type": "websocket.receive", "bytes": payload})

    async def accept(self) -> None:
        self.accepted = True

    async def receive(self) -> dict[str, Any]:
        if self._incoming:
            return self._incoming.pop(0)

        return {"type": "websocket.disconnect", "code": 1005}


class SignallingWebSocket(FakeWebSocket):
    def __init__(self) -> None:
        super().__init__()
        self.tool_called = asyncio.Event()

    async def send_json(self, data: Any) -> None:
        await super().send_json(data)

        if data.get("type") == "request":
            self.tool_called.set()


def asgi_client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=True,
    )


def test_gateway_uses_defaults() -> None:
    gateway = Gateway()
    app = gateway.create_app()
    assert isinstance(app, FastAPI)
    assert app.state.gateway is gateway


def test_channel_for_scope(settings: GatewaySettings) -> None:
    gateway = Gateway(settings)

    channel = asyncio.run(gateway.create_channel(channel_id="c1"))
    assert gateway.channel_for_scope({SCOPE_CHANNEL_KEY: "c1"}) is channel

    with pytest.raises(GatewayError, match="no longer available"):
        gateway.channel_for_scope({SCOPE_CHANNEL_KEY: "gone"})


async def test_home_health_logo_and_cors() -> None:
    gateway = Gateway(GatewaySettings(app_name="Test Gateway"))
    app = gateway.create_app()

    async with asgi_client(app) as client:
        home = await client.get("/")
        assert home.status_code == 200
        assert "Test Gateway" in home.text
        assert ">T<" in home.text  # favicon initial letter
        assert "text/html" in home.headers["content-type"]

        logo = await client.get("/logo.svg")
        assert logo.status_code == 200
        assert logo.headers["content-type"] == "image/svg+xml"

        health = await client.get("/health", headers={"Origin": "http://example.com"})
        assert health.json()["status"] == "ok"
        assert health.headers["access-control-allow-origin"] == "*"


class AutoConnectGateway(Gateway):
    async def home(self, request: Request) -> HTMLResponse:
        channel = await self.create_channel()
        url = f"ws://{request.url.netloc}/provider?token={channel.provider_token}"
        return HTMLResponse(f'<script>window.PROVIDER_URL = "{url}";</script>')


async def test_home_can_mint_a_channel_for_auto_connect() -> None:
    gateway = AutoConnectGateway()
    app = gateway.create_app()

    async with asgi_client(app) as client:
        page = await client.get("/")

    assert page.status_code == 200
    token = re.search(r"token=([^\"&]+)", page.text).group(1)
    assert gateway.registry.resolve_provider_token(token) is not None
    assert gateway.registry.channel_count == 1


async def test_mcp_path_mismatch_returns_404(settings: GatewaySettings) -> None:
    gateway = Gateway(settings)
    app = gateway.create_app()

    async with asgi_client(app) as client:
        channel = await gateway.create_channel()
        response = await client.post(
            "/mcp/some-other-service",
            headers={"Authorization": f"Bearer {channel.mcp_token}"},
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        )
        assert response.status_code == 404


async def test_admin_dashboard_with_key(settings: GatewaySettings, move_tool: dict) -> None:
    gateway = Gateway(settings.model_copy(update={"admin_enabled": True, "admin_key": "k3y"}))
    app = gateway.create_app()

    idle = await gateway.create_channel()
    channel = await gateway.create_channel(metadata={"name": "svc"})
    provider = FakeWebSocket()
    await channel.attach(provider, provider_id="p1", provider_name="service-a")
    await channel.register("tools", [move_tool])

    async with asgi_client(app) as client:
        assert (await client.get("/admin")).status_code == 403
        assert (await client.get("/admin?key=wrong")).status_code == 403

        page = await client.get("/admin?key=k3y")
        assert page.status_code == 200
        assert "text/html" in page.headers["content-type"]

        stats = (await client.get("/admin/stats?key=k3y")).json()
        assert stats["totals"] == {
            "channels": 2,
            "providersConnected": 1,
            "tools": 1,
            "pendingCalls": 0,
        }

        # the online channel is created last but must sort first
        online, offline = stats["channels"]
        assert online["channelId"] == channel.channel_id
        assert online["providerName"] == "service-a"
        assert online["tools"] == ["move"]
        assert online["providerConnected"] is True
        assert online["reclaimInSeconds"] is None

        assert offline["channelId"] == idle.channel_id
        assert offline["providerConnected"] is False
        assert isinstance(offline["reclaimInSeconds"], float)


async def test_admin_enabled_without_key_is_rejected(settings: GatewaySettings) -> None:
    with pytest.raises(GatewayConfigurationError, match="GATEWAY_ADMIN_KEY"):
        Gateway(settings.model_copy(update={"admin_enabled": True}))


async def test_admin_disabled_has_no_routes(settings: GatewaySettings) -> None:
    gateway = Gateway(settings)
    app = gateway.create_app()

    async with asgi_client(app) as client:
        assert (await client.get("/admin")).status_code == 404
        assert (await client.get("/admin/stats")).status_code == 404


async def test_mcp_requires_token(settings: GatewaySettings) -> None:
    gateway = Gateway(settings)
    app = gateway.create_app()

    async with asgi_client(app) as client:
        response = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert response.status_code == 401
        assert response.headers["www-authenticate"].startswith("Bearer")


async def test_mcp_rejects_disallowed_origin() -> None:
    gateway = Gateway(GatewaySettings(allowed_mcp_origins=["http://good"]))
    app = gateway.create_app()

    async with asgi_client(app) as client:
        channel = await gateway.create_channel()
        response = await client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {channel.mcp_token}", "Origin": "http://evil"},
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        )
        assert response.status_code == 403


async def test_mcp_rejects_websocket(settings: GatewaySettings) -> None:
    gateway = Gateway(settings)
    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await gateway.mcp_asgi({"type": "websocket"}, None, send)
    assert sent == [{"type": "websocket.close", "code": 1008}]


async def test_full_tool_round_trip(settings: GatewaySettings, move_tool: dict) -> None:
    gateway = Gateway(settings.model_copy(update={"tool_call_timeout_seconds": 2}))
    app = gateway.create_app()

    async with app.router.lifespan_context(app):
        channel = await gateway.create_channel()
        provider = SignallingWebSocket()
        await channel.attach(provider, provider_id="p", provider_name="demo")
        await channel.register("tools", [move_tool])

        async def respond() -> None:
            await provider.tool_called.wait()
            call = provider.last("request")
            channel.handle_result(
                {"type": "result", "requestId": call["requestId"], "result": {"ok": True}}
            )

        http_client = httpx.AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {channel.mcp_token}"},
            follow_redirects=True,
        )
        service_url = f"http://testserver/mcp/{channel.channel_id}"

        async with (
            streamable_http_client(service_url, http_client=http_client) as (r, w, _),
            ClientSession(r, w) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            assert [tool.name for tool in tools.tools] == ["move"]

            responder = asyncio.create_task(respond())
            result = await session.call_tool("move", {"direction": "right"})
            await responder

        assert result.isError is False
        assert result.structuredContent == {"ok": True}


async def _auto_respond(channel, provider, replies: dict[str, Any]) -> None:
    seen: set[str] = set()

    while True:
        for message in list(provider.messages):
            if message.get("type") == "request" and message["requestId"] not in seen:
                seen.add(message["requestId"])
                channel.handle_result(
                    {
                        "type": "result",
                        "requestId": message["requestId"],
                        "result": replies.get(message["method"], {}),
                    }
                )

        await asyncio.sleep(0)


async def test_full_resource_round_trip(settings: GatewaySettings) -> None:
    from pydantic import AnyUrl

    gateway = Gateway(settings.model_copy(update={"tool_call_timeout_seconds": 2}))
    app = gateway.create_app()

    async with app.router.lifespan_context(app):
        channel = await gateway.create_channel()
        provider = FakeWebSocket()
        await channel.attach(provider, provider_id="p", provider_name="demo")
        await channel.register("resources", [{"uri": "mem://a", "name": "a"}])
        await channel.register("resourceTemplates", [{"uriTemplate": "mem://{id}", "name": "t"}])
        await channel.register("prompts", [{"name": "greet", "description": "hi"}])

        auto = asyncio.create_task(
            _auto_respond(
                channel,
                provider,
                {
                    "resources/read": {"contents": [{"uri": "mem://a", "text": "hi"}]},
                    "prompts/get": {
                        "messages": [{"role": "user", "content": {"type": "text", "text": "hey"}}]
                    },
                    "completion/complete": {"values": ["one", "two"], "total": 2},
                },
            )
        )
        http_client = httpx.AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {channel.mcp_token}"},
            follow_redirects=True,
        )
        service_url = f"http://testserver/mcp/{channel.channel_id}"

        async with (
            streamable_http_client(service_url, http_client=http_client) as (r, w, _),
            ClientSession(r, w) as session,
        ):
            await session.initialize()
            resources = await session.list_resources()
            assert [str(res.uri) for res in resources.resources] == ["mem://a"]

            templates = await session.list_resource_templates()
            assert [t.uriTemplate for t in templates.resourceTemplates] == ["mem://{id}"]

            read = await session.read_resource(AnyUrl("mem://a"))
            assert read.contents[0].text == "hi"

            await session.subscribe_resource(AnyUrl("mem://a"))
            await session.unsubscribe_resource(AnyUrl("mem://a"))

            prompts = await session.list_prompts()
            assert [p.name for p in prompts.prompts] == ["greet"]

            got = await session.get_prompt("greet", {"name": "x"})
            assert got.messages[0].content.text == "hey"

            completion = await session.complete(
                mcp_types.PromptReference(type="ref/prompt", name="greet"),
                {"name": "x", "value": "h"},
            )
            assert completion.completion.values == ["one", "two"]

            await session.set_logging_level("info")

        auto.cancel()

        with contextlib.suppress(asyncio.CancelledError):
            await auto


async def test_reaper_removes_expired_channels(settings: GatewaySettings) -> None:
    gateway = Gateway(settings.model_copy(update={"reaper_interval_seconds": 0.01}))
    app = gateway.create_app()

    expired = await gateway.create_channel(ttl_seconds=-1)

    async with app.router.lifespan_context(app):
        await asyncio.sleep(0.05)
        assert gateway.registry.get(expired.channel_id) is None


async def test_provider_endpoint_full_flow(settings: GatewaySettings, move_tool: dict) -> None:
    events: list[str] = []

    class RecordingGateway(Gateway):
        async def on_provider_connected(self, channel) -> None:
            events.append("connected")

        async def on_provider_disconnected(self, channel) -> None:
            events.append("disconnected")

    gateway = RecordingGateway(settings)
    channel = await gateway.create_channel()

    websocket = FakeBrowserWebSocket(
        query_params={"token": channel.provider_token, "providerName": "demo"},
        headers={"origin": "http://testserver"},
    )
    websocket.queue(
        json.dumps({"type": "register", "registry": "tools", "items": [move_tool]}),
        json.dumps({"type": "ping"}),
        "{ not json",
        "123",
        json.dumps({"type": "register", "registry": "tools", "items": "not-an-array"}),
        json.dumps({"type": "unsupported"}),
    )

    await gateway.provider_endpoint(websocket)

    assert websocket.accepted is True
    assert websocket.last("hello.ack")["channelId"] == channel.channel_id
    assert websocket.last("ack")["count"] == 1
    assert websocket.last("pong")["type"] == "pong"
    assert sum(1 for m in websocket.messages if m["type"] == "protocol.error") == 4
    assert events == ["connected", "disconnected"]
    assert channel.provider_connected is False


async def test_provider_endpoint_stops_when_channel_goes_offline(settings: GatewaySettings) -> None:
    gateway = Gateway(settings)
    channel = await gateway.create_channel()

    class OfflineOnReceive(FakeBrowserWebSocket):
        async def receive(self) -> dict[str, Any]:
            channel._websocket = None
            return {"type": "websocket.receive", "text": json.dumps({"type": "ping"})}

    websocket = OfflineOnReceive(
        query_params={"token": channel.provider_token},
        headers={"origin": "http://testserver"},
    )

    await gateway.provider_endpoint(websocket)
    assert channel.provider_connected is False


async def test_provider_endpoint_survives_channel_error_before_pump(
    settings: GatewaySettings,
) -> None:
    gateway = Gateway(settings)
    channel = await gateway.create_channel()

    class FailingHello(FakeBrowserWebSocket):
        async def send_json(self, data: Any) -> None:
            raise RuntimeError("send failed")

    websocket = FailingHello(
        query_params={"token": channel.provider_token},
        headers={"origin": "http://testserver"},
    )
    await gateway.provider_endpoint(websocket)
    assert channel.provider_connected is False


async def test_provider_endpoint_rejects_binary_frames(settings: GatewaySettings) -> None:
    gateway = Gateway(settings)
    channel = await gateway.create_channel()
    websocket = FakeBrowserWebSocket(
        query_params={"token": channel.provider_token},
        headers={"origin": "http://testserver"},
    )
    websocket.queue_bytes(b"\x00\x01\x02")

    await gateway.provider_endpoint(websocket)
    assert websocket.last("protocol.error")["message"] == "Only text messages are supported"


async def test_reaper_survives_purge_errors(settings: GatewaySettings) -> None:
    gateway = Gateway(settings.model_copy(update={"reaper_interval_seconds": 0.01}))
    calls = {"count": 0}

    async def flaky_purge() -> int:
        calls["count"] += 1

        if calls["count"] == 1:
            raise RuntimeError("transient failure")

        return 0

    gateway.registry.purge_expired = flaky_purge
    app = gateway.create_app()

    async with app.router.lifespan_context(app):
        await asyncio.sleep(0.05)

    assert calls["count"] >= 2


async def test_provider_endpoint_rejects_invalid_token(settings: GatewaySettings) -> None:
    gateway = Gateway(settings)
    websocket = FakeBrowserWebSocket(query_params={"token": "bad"}, headers={})
    await gateway.provider_endpoint(websocket)
    assert websocket.closed is True
    assert websocket.close_code == 1008
    assert websocket.accepted is False


async def test_provider_endpoint_rejects_invalid_origin(settings: GatewaySettings) -> None:
    gateway = Gateway(settings)
    channel = await gateway.create_channel()
    websocket = FakeBrowserWebSocket(
        query_params={"token": channel.provider_token},
        headers={"origin": "http://evil"},
    )
    await gateway.provider_endpoint(websocket)
    assert websocket.close_code == 1008
    assert websocket.accepted is False


async def test_provider_endpoint_closes_oversized_message() -> None:
    settings = GatewaySettings(
        allowed_provider_origins=["http://testserver"],
        maximum_websocket_message_bytes=32,
    )
    gateway = Gateway(settings)
    channel = await gateway.create_channel()
    websocket = FakeBrowserWebSocket(
        query_params={"token": channel.provider_token},
        headers={"origin": "http://testserver"},
    )
    websocket.queue("x" * 128)

    await gateway.provider_endpoint(websocket)
    assert websocket.close_code == 1009


async def test_provider_endpoint_skips_disconnect_when_replaced(settings: GatewaySettings) -> None:
    events: list[str] = []

    class RecordingGateway(Gateway):
        async def on_provider_disconnected(self, channel) -> None:
            events.append("disconnected")

    gateway = RecordingGateway(settings)
    channel = await gateway.create_channel()
    replacement = FakeWebSocket()

    class ReplacingWebSocket(FakeBrowserWebSocket):
        async def receive(self) -> dict[str, Any]:
            # a new provider takes over the channel before this one is cleaned up
            await channel.attach(replacement, provider_id="p2", provider_name=None)
            return {"type": "websocket.disconnect", "code": 1005}

    websocket = ReplacingWebSocket(
        query_params={"token": channel.provider_token},
        headers={"origin": "http://testserver"},
    )

    await gateway.provider_endpoint(websocket)
    assert events == []
