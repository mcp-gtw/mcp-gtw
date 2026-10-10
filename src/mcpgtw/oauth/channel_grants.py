from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod

from mcpgtw.oauth.channel_grant import ChannelGrant
from mcpgtw.oauth.verified_principal import VerifiedPrincipal


class ChannelGrantStore(ABC):
    @abstractmethod
    async def channels(self, principal: VerifiedPrincipal) -> frozenset[str]: ...

    @abstractmethod
    async def grant(self, principal: VerifiedPrincipal, channel_id: str) -> None: ...

    @abstractmethod
    async def revoke(self, principal: VerifiedPrincipal, channel_id: str) -> None: ...

    @abstractmethod
    async def remove_channel(self, channel_id: str) -> None: ...


class MemoryChannelGrantStore(ChannelGrantStore):
    """Bounded process-local grants for development and tests only."""

    def __init__(self, maximum_grants: int = 10000) -> None:
        if maximum_grants < 1:
            raise ValueError("maximum_grants must be positive")

        self.maximum_grants = maximum_grants
        self._grants: dict[str, dict[str, ChannelGrant]] = {}
        self._count = 0
        self._lock = asyncio.Lock()

    async def channels(self, principal: VerifiedPrincipal) -> frozenset[str]:
        async with self._lock:
            return frozenset(
                channel
                for channel, grant in self._grants.get(principal.principal_id, {}).items()
                if principal.scopes <= grant.scopes and grant.expires_at > time.time()
            )

    async def grant(self, principal: VerifiedPrincipal, channel_id: str) -> None:
        async with self._lock:
            if self._count >= self.maximum_grants:
                self._purge()

            channels = self._grants.get(principal.principal_id, {})

            if channel_id not in channels:
                if self._count >= self.maximum_grants:
                    raise ValueError("Grant capacity exceeded")

                self._count += 1

            channels[channel_id] = ChannelGrant(principal.scopes, principal.expires_at)
            self._grants[principal.principal_id] = channels

    def _purge(self) -> None:
        now = time.time()
        self._grants = {
            owner: {channel: grant for channel, grant in channels.items() if grant.expires_at > now}
            for owner, channels in self._grants.items()
        }
        self._grants = {owner: channels for owner, channels in self._grants.items() if channels}
        self._count = sum(map(len, self._grants.values()))

    async def revoke(self, principal: VerifiedPrincipal, channel_id: str) -> None:
        async with self._lock:
            channels = self._grants.get(principal.principal_id, {})

            if channel_id in channels:
                del channels[channel_id]
                self._count -= 1

                if not channels:
                    self._grants.pop(principal.principal_id)

    async def remove_channel(self, channel_id: str) -> None:
        async with self._lock:
            for channels in self._grants.values():
                if channel_id in channels:
                    del channels[channel_id]
                    self._count -= 1

            self._grants = {owner: channels for owner, channels in self._grants.items() if channels}
