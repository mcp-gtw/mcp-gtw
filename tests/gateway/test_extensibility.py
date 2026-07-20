from __future__ import annotations

import json

import pytest
from support import FakeProviderWebSocket

from mcp_gtw.authenticator import Authenticator, TokenAuthenticator
from mcp_gtw.channel import Channel
from mcp_gtw.codec import ProtocolCodec
from mcp_gtw.config import GatewaySettings
from mcp_gtw.errors import ChannelCapacityError
from mcp_gtw.expiry import TtlExpiryPolicy
from mcp_gtw.gateway import Gateway
from mcp_gtw.origin import OriginPolicy
from mcp_gtw.registry import ChannelRegistry
from mcp_gtw.tokens import SecretsTokenProvider


class PrefixTokens(SecretsTokenProvider):
    def __init__(self) -> None:
        self._count = 0

    def generate(self, nbytes: int = 32) -> str:
        self._count += 1
        return f"tok-{self._count}"


class DenyEverything(Authenticator):
    async def authenticate_provider(self, websocket) -> None:
        return None

    async def authenticate_client(self, request) -> None:
        return None


class DenyOrigins(OriginPolicy):
    def allows_origin(self, origin: str) -> bool:
        return False


class RejectingCodec(ProtocolCodec):
    def decode(self, text: str | None) -> dict:
        raise ValueError("codec rejected the frame")


class InstantExpiry(TtlExpiryPolicy):
    def initial_deadline(self, now: float, ttl_seconds: float | None) -> float:
        return now - 1.0


class MyChannel(Channel):
    pass


class MyRegistry(ChannelRegistry):
    pass


def _provider_ws(channel, origin: str = "http://testserver") -> FakeProviderWebSocket:
    return FakeProviderWebSocket(
        query_params={"token": channel.provider_token}, headers={"origin": origin}
    )


async def test_injected_token_provider_is_used(settings: GatewaySettings) -> None:
    gateway = Gateway(settings, tokens=PrefixTokens())
    channel = await gateway.create_channel()
    assert channel.provider_token.startswith("tok-")


def test_class_attribute_swaps_the_token_provider(settings: GatewaySettings) -> None:
    class CustomGateway(Gateway):
        token_provider_class = PrefixTokens

    gateway = CustomGateway(settings)
    assert isinstance(gateway.tokens, PrefixTokens)


async def test_injected_authenticator_denies_provider(settings: GatewaySettings) -> None:
    gateway = Gateway(settings, authenticator=DenyEverything())
    websocket = FakeProviderWebSocket(query_params={"token": "anything"}, headers={})

    await gateway.provider_endpoint(websocket)
    assert websocket.close_code == 1008
    assert websocket.accepted is False


async def test_injected_origin_policy_denies_provider(settings: GatewaySettings) -> None:
    gateway = Gateway(settings, provider_origins=DenyOrigins())
    channel = await gateway.create_channel()
    websocket = _provider_ws(channel, origin="http://anywhere")

    await gateway.provider_endpoint(websocket)
    assert websocket.close_code == 1008
    assert websocket.accepted is False


async def test_origin_is_checked_before_authentication(settings: GatewaySettings) -> None:
    calls: list[str] = []

    class RecordingAuthenticator(Authenticator):
        async def authenticate_provider(self, websocket):
            calls.append("provider")
            return None

        async def authenticate_client(self, request):
            calls.append("client")
            return None

    gateway = Gateway(
        settings, authenticator=RecordingAuthenticator(), provider_origins=DenyOrigins()
    )
    websocket = FakeProviderWebSocket(
        query_params={"token": "x"}, headers={"origin": "http://evil"}
    )

    await gateway.provider_endpoint(websocket)
    assert websocket.close_code == 1008
    assert calls == []


async def test_injected_codec_controls_frame_parsing(settings: GatewaySettings) -> None:
    gateway = Gateway(settings, codec=RejectingCodec())
    channel = await gateway.create_channel()
    websocket = _provider_ws(channel)
    websocket.queue(json.dumps({"type": "ping"}))

    await gateway.provider_endpoint(websocket)
    assert websocket.last("protocol.error")["message"] == "codec rejected the frame"


async def test_injected_expiry_policy_controls_reclamation(settings: GatewaySettings) -> None:
    gateway = Gateway(settings, expiry_policy=InstantExpiry(0.0))
    await gateway.create_channel()
    assert await gateway.registry.purge_expired() == 1


async def test_class_attribute_swaps_registry_and_channel(settings: GatewaySettings) -> None:
    class CustomGateway(Gateway):
        registry_class = MyRegistry
        channel_class = MyChannel

    gateway = CustomGateway(settings)
    channel = await gateway.create_channel()
    assert isinstance(gateway.registry, MyRegistry)
    assert isinstance(channel, MyChannel)


async def test_injected_registry_instance_is_used(settings: GatewaySettings) -> None:
    registry = MyRegistry(settings, channel_class=MyChannel)
    gateway = Gateway(settings, registry=registry)
    channel = await gateway.create_channel()
    assert gateway.registry is registry
    assert isinstance(channel, MyChannel)


async def test_injected_registry_still_fires_gateway_hooks(settings: GatewaySettings) -> None:
    events: list[str] = []

    class RecordingGateway(Gateway):
        async def on_channel_created(self, channel) -> None:
            events.append("created")

        async def on_channel_removed(self, channel) -> None:
            events.append("removed")

    gateway = RecordingGateway(settings, registry=ChannelRegistry(settings))
    channel = await gateway.create_channel()
    await gateway.registry.remove_channel(channel.channel_id)

    assert events == ["created", "removed"]


class DerivingTokens(SecretsTokenProvider):
    def derive(self, subject: str) -> str:
        return f"derived-{subject}"


class DerivingAuthenticator(TokenAuthenticator):
    async def authenticate_provider(self, websocket):
        subject = websocket.query_params.get("subject")

        if subject is None:
            return None

        token = self._registry.tokens.derive(subject)
        existing = self._registry.resolve_provider_token(token)

        if existing is not None:
            return existing

        return await self._registry.create_channel(provider_token=token, mcp_token=f"mcp-{token}")


async def test_authenticator_derives_tokens_via_registry(settings: GatewaySettings) -> None:
    class CustomGateway(Gateway):
        token_provider_class = DerivingTokens
        authenticator_class = DerivingAuthenticator

    gateway = CustomGateway(settings)
    websocket = FakeProviderWebSocket(
        query_params={"subject": "alice"}, headers={"origin": "http://testserver"}
    )

    await gateway.provider_endpoint(websocket)
    assert gateway.registry.resolve_provider_token("derived-alice") is not None


async def test_create_channel_accepts_injected_tokens(settings: GatewaySettings) -> None:
    gateway = Gateway(settings)
    channel = await gateway.create_channel(provider_token="ptok", mcp_token="mtok")

    assert channel.provider_token == "ptok"
    assert channel.mcp_token == "mtok"
    assert gateway.registry.resolve_provider_token("ptok") is channel
    assert gateway.registry.resolve_mcp_token("mtok") is channel


async def test_create_channel_rejects_a_duplicate_injected_token(settings: GatewaySettings) -> None:
    gateway = Gateway(settings)
    await gateway.create_channel(provider_token="dup")

    with pytest.raises(ChannelCapacityError, match="same token"):
        await gateway.create_channel(provider_token="dup")


async def test_create_channel_rejects_identical_tokens(settings: GatewaySettings) -> None:
    gateway = Gateway(settings)

    with pytest.raises(ValueError, match="must be different"):
        await gateway.create_channel(provider_token="same", mcp_token="same")
