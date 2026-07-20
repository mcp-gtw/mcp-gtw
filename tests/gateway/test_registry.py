from __future__ import annotations

import time

import pytest
from support import FakeWebSocket

from mcp_gtw.config import GatewaySettings
from mcp_gtw.errors import ChannelCapacityError
from mcp_gtw.listeners import GatewayListener
from mcp_gtw.registry import ChannelRegistry
from mcp_gtw.tokens import SecretsTokenProvider


class RecordingListener(GatewayListener):
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    async def on_channel_created(self, channel) -> None:
        self.events.append(("created", channel.channel_id))

    async def on_channel_removed(self, channel) -> None:
        self.events.append(("removed", channel.channel_id))

    async def on_provider_connected(self, channel) -> None:
        self.events.append(("connected", channel.channel_id))

    async def on_provider_disconnected(self, channel) -> None:
        self.events.append(("disconnected", channel.channel_id))


class ExplodingListener(GatewayListener):
    async def on_channel_created(self, channel) -> None:
        raise RuntimeError("listener failure")


async def test_create_resolve_and_remove(settings: GatewaySettings) -> None:
    listener = RecordingListener()
    registry = ChannelRegistry(settings, [listener])

    channel = await registry.create_channel(metadata={"name": "Neo"})
    assert registry.channel_count == 1
    assert registry.get(channel.channel_id) is channel
    assert registry.resolve_mcp_token(channel.mcp_token) is channel
    assert registry.resolve_provider_token(channel.provider_token) is channel
    assert registry.resolve_mcp_token(None) is None
    assert registry.resolve_mcp_token("nope") is None

    assert await registry.remove_channel(channel.channel_id) is True
    assert await registry.remove_channel(channel.channel_id) is False
    assert ("created", channel.channel_id) in listener.events
    assert ("removed", channel.channel_id) in listener.events


async def test_create_channel_with_explicit_id(settings: GatewaySettings) -> None:
    registry = ChannelRegistry(settings)
    channel = await registry.create_channel(channel_id="fixed")
    assert channel.channel_id == "fixed"

    with pytest.raises(ChannelCapacityError, match="already exists"):
        await registry.create_channel(channel_id="fixed")


async def test_create_channel_respects_maximum(settings: GatewaySettings) -> None:
    registry = ChannelRegistry(settings.model_copy(update={"maximum_channels": 0}))

    with pytest.raises(ChannelCapacityError, match="maximum number of channels"):
        await registry.create_channel()


async def test_none_maximum_channels_is_unlimited(settings: GatewaySettings) -> None:
    registry = ChannelRegistry(settings.model_copy(update={"maximum_channels": None}))

    for _ in range(5):
        await registry.create_channel()

    assert registry.channel_count == 5


async def test_full_registry_keeps_existing_channels_intact(settings: GatewaySettings) -> None:
    registry = ChannelRegistry(settings.model_copy(update={"maximum_channels": 2}))
    first = await registry.create_channel()
    second = await registry.create_channel()

    with pytest.raises(ChannelCapacityError, match="maximum number of channels"):
        await registry.create_channel()

    assert registry.channel_count == 2
    assert registry.resolve_provider_token(first.provider_token) is first
    assert registry.resolve_mcp_token(second.mcp_token) is second


async def test_provider_lifecycle_dispatch(settings: GatewaySettings) -> None:
    listener = RecordingListener()
    registry = ChannelRegistry(settings, [listener])
    channel = await registry.create_channel()

    await registry.provider_connected(channel)
    await registry.provider_disconnected(channel)

    assert ("connected", channel.channel_id) in listener.events
    assert ("disconnected", channel.channel_id) in listener.events


async def test_dispatch_survives_listener_error(settings: GatewaySettings) -> None:
    registry = ChannelRegistry(settings, [ExplodingListener()])
    channel = await registry.create_channel()
    assert registry.get(channel.channel_id) is channel


async def test_provider_disconnected_ignores_removed_channel(settings: GatewaySettings) -> None:
    registry = ChannelRegistry(settings)
    channel = await registry.create_channel()
    await registry.remove_channel(channel.channel_id)

    await registry.provider_disconnected(channel)
    assert channel.channel_id not in registry._expiry


async def test_purge_reaps_only_offline_expired_channels(settings: GatewaySettings) -> None:
    registry = ChannelRegistry(settings)
    kept = await registry.create_channel(ttl_seconds=100)
    expired = await registry.create_channel(ttl_seconds=-1)

    assert await registry.purge_expired() == 1
    assert registry.get(expired.channel_id) is None
    assert registry.get(kept.channel_id) is kept
    assert await registry.purge_expired() == 0


async def test_connected_channel_is_never_reaped(settings: GatewaySettings) -> None:
    registry = ChannelRegistry(settings)
    channel = await registry.create_channel(ttl_seconds=-1)
    await channel.attach(FakeWebSocket(), provider_id="p", provider_name=None)

    assert await registry.purge_expired() == 0
    assert registry.get(channel.channel_id) is channel


async def test_creation_time_tracking_is_admin_gated(settings: GatewaySettings) -> None:
    disabled = ChannelRegistry(settings)
    await disabled.create_channel()
    assert disabled._created == {}

    enabled = ChannelRegistry(settings.model_copy(update={"admin_enabled": True}))
    channel = await enabled.create_channel()
    assert list(enabled._created) == [channel.channel_id]


async def test_admin_channels_tolerates_untracked_creation(settings: GatewaySettings) -> None:
    registry = ChannelRegistry(settings)
    await registry.create_channel()

    [entry] = registry.admin_channels()
    assert entry["ageSeconds"] is None


async def test_admin_channels_hides_infinite_reclaim(settings: GatewaySettings) -> None:
    registry = ChannelRegistry(settings.model_copy(update={"admin_enabled": True}))
    channel = await registry.create_channel()
    websocket = FakeWebSocket()
    await channel.attach(websocket, provider_id="p", provider_name=None)
    await registry.provider_connected(channel)
    await channel.detach(websocket)

    [entry] = registry.admin_channels()
    assert entry["providerConnected"] is False
    assert entry["reclaimInSeconds"] is None


async def test_provider_connected_sets_infinite_expiry(settings: GatewaySettings) -> None:
    registry = ChannelRegistry(settings)
    channel = await registry.create_channel()

    await registry.provider_connected(channel)
    assert registry._expiry[channel.channel_id] == float("inf")

    await registry.remove_channel(channel.channel_id)
    await registry.provider_connected(channel)
    assert channel.channel_id not in registry._expiry


async def test_remove_channel_require_offline_skips_connected(settings: GatewaySettings) -> None:
    registry = ChannelRegistry(settings)
    channel = await registry.create_channel(ttl_seconds=-1)
    await channel.attach(FakeWebSocket(), provider_id="p", provider_name=None)

    assert await registry.remove_channel(channel.channel_id, require_offline=True) is False
    assert registry.get(channel.channel_id) is channel


async def test_purge_skips_a_channel_that_reconnects_mid_sweep(settings: GatewaySettings) -> None:
    registry = ChannelRegistry(settings)
    first = await registry.create_channel(ttl_seconds=-1)
    second = await registry.create_channel(ttl_seconds=-1)
    reconnected = FakeWebSocket()

    class Reconnector(GatewayListener):
        async def on_channel_removed(self, channel) -> None:
            if channel.channel_id == first.channel_id:
                await second.attach(reconnected, provider_id="p", provider_name=None)

    registry._listeners.append(Reconnector())

    assert await registry.purge_expired() == 1
    assert registry.get(first.channel_id) is None
    assert registry.get(second.channel_id) is second


async def test_disconnect_starts_offline_grace(settings: GatewaySettings) -> None:
    registry = ChannelRegistry(settings.model_copy(update={"offline_ttl_seconds": 1000.0}))
    channel = await registry.create_channel(ttl_seconds=-1)
    websocket = FakeWebSocket()
    await channel.attach(websocket, provider_id="p", provider_name=None)

    await channel.detach(websocket)
    await registry.provider_disconnected(channel)

    remaining = registry._expiry[channel.channel_id] - time.monotonic()
    assert 900 < remaining <= 1000
    assert await registry.purge_expired() == 0


async def test_provider_disconnected_keeps_infinite_expiry_when_reconnected(
    settings: GatewaySettings,
) -> None:
    registry = ChannelRegistry(settings)
    channel = await registry.create_channel()
    await channel.attach(FakeWebSocket(), provider_id="p", provider_name=None)
    await registry.provider_connected(channel)
    assert registry._expiry[channel.channel_id] == float("inf")

    await registry.provider_disconnected(channel)

    assert registry._expiry[channel.channel_id] == float("inf")


async def test_reconnect_during_detach_notify_is_not_reaped(settings: GatewaySettings) -> None:
    registry = ChannelRegistry(settings)
    channel = await registry.create_channel(ttl_seconds=-1)
    first = FakeWebSocket()
    await channel.attach(first, provider_id="a", provider_name=None)
    await registry.provider_connected(channel)

    second = FakeWebSocket()

    class Reconnector:
        async def send_tool_list_changed(self) -> None:
            await channel.attach(second, provider_id="b", provider_name=None)
            await registry.provider_connected(channel)

    channel.remember_mcp_session(Reconnector())

    if await channel.detach(first):
        await registry.provider_disconnected(channel)

    assert channel.provider_connected is True
    assert registry._expiry[channel.channel_id] == float("inf")
    assert await registry.purge_expired() == 0


async def test_close_all(settings: GatewaySettings) -> None:
    registry = ChannelRegistry(settings)
    await registry.create_channel()
    await registry.create_channel()
    await registry.close_all()
    assert registry.channel_count == 0


class ScriptedTokenProvider(SecretsTokenProvider):
    def __init__(self, values: list[str]) -> None:
        self._values = iter(values)

    def generate(self, nbytes: int = 32) -> str:
        return next(self._values)


def test_unique_token_retries_on_collision(settings: GatewaySettings) -> None:
    registry = ChannelRegistry(settings, tokens=ScriptedTokenProvider(["taken", "fresh"]))
    registry._by_provider_token["taken"] = "chan"
    assert registry._unique_token() == "fresh"


async def test_create_channel_rejects_cross_index_token(settings: GatewaySettings) -> None:
    registry = ChannelRegistry(settings)
    existing = await registry.create_channel()

    with pytest.raises(ChannelCapacityError, match="same token"):
        await registry.create_channel(mcp_token=existing.provider_token)

    with pytest.raises(ChannelCapacityError, match="same token"):
        await registry.create_channel(provider_token=existing.mcp_token)
