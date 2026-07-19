from __future__ import annotations

from mcp_gtw.channel import Channel
from mcp_gtw.config import GatewaySettings
from mcp_gtw.listeners import GatewayListener


async def test_default_hooks_are_noops(settings: GatewaySettings) -> None:
    listener = GatewayListener()
    channel = Channel(
        channel_id="c",
        mcp_token="m",
        provider_token="b",
        settings=settings,
    )

    assert await listener.on_channel_created(channel) is None
    assert await listener.on_channel_removed(channel) is None
    assert await listener.on_provider_connected(channel) is None
    assert await listener.on_provider_disconnected(channel) is None
