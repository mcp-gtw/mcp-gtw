from __future__ import annotations

from abc import ABC, abstractmethod

from mcpgtw.channel import Channel
from mcpgtw.listeners import GatewayListener
from mcpgtw.oauth.channel_grants import ChannelGrantStore
from mcpgtw.oauth.verified_principal import VerifiedPrincipal


class ChannelAccessPolicy(GatewayListener, ABC):
    @abstractmethod
    async def resolve(self, principal: VerifiedPrincipal, addressed: str) -> str | None: ...


class DenyUnlessGranted(ChannelAccessPolicy):
    def __init__(self, grants: ChannelGrantStore) -> None:
        self.grants = grants

    async def resolve(self, principal: VerifiedPrincipal, addressed: str) -> str | None:
        channels = await self.grants.channels(principal)

        if addressed:
            return addressed if addressed in channels else None

        return next(iter(channels)) if len(channels) == 1 else None

    async def on_channel_removed(self, channel: Channel) -> None:
        await self.grants.remove_channel(channel.channel_id)
