from __future__ import annotations

from mcpgtw.channel import Channel
from mcpgtw.config import GatewaySettings
from mcpgtw.listeners import GatewayListener


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
