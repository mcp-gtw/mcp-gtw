from __future__ import annotations

import asyncio

from support import FakeWebSocket

from mcp_gtw.config import GatewaySettings
from mcp_gtw.errors import ChannelCapacityError
from mcp_gtw.gateway import Gateway
from mcp_gtw.registry import ChannelRegistry


async def test_concurrent_creation_stays_unique(settings: GatewaySettings) -> None:
    registry = ChannelRegistry(settings)

    channels = await asyncio.gather(*[registry.create_channel() for _ in range(50)])

    assert registry.channel_count == 50
    assert len({channel.channel_id for channel in channels}) == 50
    assert len({channel.provider_token for channel in channels}) == 50
    assert len({channel.mcp_token for channel in channels}) == 50


async def test_concurrent_create_and_remove_churn(settings: GatewaySettings) -> None:
    registry = ChannelRegistry(settings)
    created = await asyncio.gather(*[registry.create_channel() for _ in range(20)])

    await asyncio.gather(*[registry.remove_channel(channel.channel_id) for channel in created])
    assert registry.channel_count == 0


async def test_reaper_races_creation_without_corruption(settings: GatewaySettings) -> None:
    registry = ChannelRegistry(settings)
    await asyncio.gather(*[registry.create_channel(ttl_seconds=-1) for _ in range(10)])

    async def keep_creating() -> list:
        return await asyncio.gather(*[registry.create_channel(ttl_seconds=100) for _ in range(10)])

    purged, _ = await asyncio.gather(registry.purge_expired(), keep_creating())

    assert purged == 10
    assert registry.channel_count == 10


async def test_upsert_race_converges_to_one_channel(settings: GatewaySettings) -> None:
    registry = ChannelRegistry(settings)

    async def upsert():
        existing = registry.resolve_provider_token("shared")

        if existing is not None:
            return existing

        try:
            return await registry.create_channel(
                channel_id="shared-id", provider_token="shared", mcp_token="shared-mcp"
            )
        except ChannelCapacityError:
            return registry.resolve_provider_token("shared")

    first, second = await asyncio.gather(upsert(), upsert())

    assert first is second
    assert registry.channel_count == 1


async def test_pending_call_flood_is_capped(settings: GatewaySettings, move_tool: dict) -> None:
    registry = ChannelRegistry(settings)
    channel = await registry.create_channel()
    await channel.attach(FakeWebSocket(), provider_id="p", provider_name=None)
    await channel.register("tools", [move_tool])

    cap = settings.maximum_pending_calls_per_channel
    results = await asyncio.gather(
        *[
            channel.execute_tool(name="move", arguments={"direction": "left"})
            for _ in range(cap + 2)
        ]
    )

    assert all(result.isError for result in results)
    assert any("Too many pending calls" in result.content[0].text for result in results)


async def test_admin_stats_survives_channel_churn(settings: GatewaySettings) -> None:
    gateway = Gateway(settings.model_copy(update={"admin_enabled": True, "admin_key": "k"}))

    for _ in range(10):
        await gateway.create_channel()

    async def churn() -> None:
        for _ in range(30):
            channel = await gateway.create_channel()
            await gateway.registry.remove_channel(channel.channel_id)
            await asyncio.sleep(0)

    async def read_admin() -> int:
        reads = 0

        for _ in range(60):
            stats = gateway.admin_stats()
            assert stats["totals"]["channels"] == len(stats["channels"])
            reads += 1
            await asyncio.sleep(0)

        return reads

    _, reads = await asyncio.gather(churn(), read_admin())
    assert reads == 60
