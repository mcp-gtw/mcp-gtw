from __future__ import annotations

import asyncio

from support import FakeWebSocket

from mcp_gtw.config import GatewaySettings
from mcp_gtw.gateway import Gateway
from mcp_gtw.registry import ChannelRegistry


async def test_thousands_of_channels_stay_unique_and_resolvable(settings: GatewaySettings) -> None:
    gateway = Gateway(settings.model_copy(update={"admin_enabled": True, "admin_key": "k"}))
    count = 3000
    channels = [await gateway.create_channel() for _ in range(count)]

    assert gateway.registry.channel_count == count
    assert len({channel.channel_id for channel in channels}) == count
    assert len({channel.provider_token for channel in channels}) == count
    assert len({channel.mcp_token for channel in channels}) == count

    for channel in channels[::300]:
        assert gateway.registry.resolve_provider_token(channel.provider_token) is channel
        assert gateway.registry.resolve_mcp_token(channel.mcp_token) is channel

    stats = gateway.admin_stats()
    assert stats["totals"]["channels"] == count == len(stats["channels"])

    await gateway.registry.close_all()
    assert gateway.registry.channel_count == 0


async def test_large_concurrent_churn_leaves_a_consistent_registry(
    settings: GatewaySettings,
) -> None:
    registry = ChannelRegistry(settings)
    created = await asyncio.gather(*[registry.create_channel() for _ in range(1500)])

    assert registry.channel_count == 1500
    assert len({channel.provider_token for channel in created}) == 1500

    await asyncio.gather(*[registry.remove_channel(channel.channel_id) for channel in created])

    assert registry.channel_count == 0
    assert registry.admin_channels() == []


async def test_admin_stats_aggregates_many_providers(
    settings: GatewaySettings, move_tool: dict
) -> None:
    gateway = Gateway(settings.model_copy(update={"admin_enabled": True, "admin_key": "k"}))
    count = 500

    for index in range(count):
        channel = await gateway.create_channel()
        await channel.attach(FakeWebSocket(), provider_id=f"p{index}", provider_name=None)
        await gateway.registry.provider_connected(channel)
        await channel.register("tools", [move_tool])

    stats = gateway.admin_stats()
    assert stats["totals"]["channels"] == count
    assert stats["totals"]["providersConnected"] == count
    assert stats["totals"]["tools"] == count
    assert stats["totals"]["channels"] == len(stats["channels"])
