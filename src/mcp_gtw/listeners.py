from __future__ import annotations

from mcp_gtw.channel import Channel


class GatewayListener:
    """Lifecycle hooks that extensions implement to observe channel events.

    Every hook is optional and defaults to a no-op so extensions only override
    the events they care about.
    """

    async def on_channel_created(self, channel: Channel) -> None:
        return None

    async def on_channel_removed(self, channel: Channel) -> None:
        return None

    async def on_provider_connected(self, channel: Channel) -> None:
        return None

    async def on_provider_disconnected(self, channel: Channel) -> None:
        return None
