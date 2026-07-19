from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Iterable

from mcp_gtw.channel import Channel
from mcp_gtw.config import GatewaySettings
from mcp_gtw.errors import ChannelCapacityError
from mcp_gtw.helpers.security import generate_token
from mcp_gtw.listeners import GatewayListener

logger = logging.getLogger(__name__)


class ChannelRegistry:
    """Owns every channel and resolves the private and public tokens to channels."""

    def __init__(
        self,
        settings: GatewaySettings,
        listeners: Iterable[GatewayListener] = (),
        channel_class: type[Channel] = Channel,
    ) -> None:
        self.settings = settings
        self._listeners = list(listeners)
        self._channel_class = channel_class
        self._channels: dict[str, Channel] = {}
        self._by_mcp_token: dict[str, str] = {}
        self._by_provider_token: dict[str, str] = {}
        self._expiry: dict[str, float] = {}
        self._created: dict[str, float] = {}
        self._lock = asyncio.Lock()

    @property
    def channel_count(self) -> int:
        return len(self._channels)

    @property
    def connected_provider_count(self) -> int:
        return sum(1 for channel in self._channels.values() if channel.provider_connected)

    def get(self, channel_id: str) -> Channel | None:
        return self._channels.get(channel_id)

    def resolve_mcp_token(self, token: str | None) -> Channel | None:
        return self._resolve(self._by_mcp_token, token)

    def resolve_provider_token(self, token: str | None) -> Channel | None:
        return self._resolve(self._by_provider_token, token)

    def _resolve(self, index: dict[str, str], token: str | None) -> Channel | None:
        if token is None:
            return None

        channel_id = index.get(token)

        if channel_id is None:
            return None

        return self._channels.get(channel_id)

    def admin_channels(self) -> list[dict]:
        now = time.monotonic()
        channels = [
            {
                **channel.snapshot(),
                "ageSeconds": round(now - self._created[channel_id], 1),
                "reclaimInSeconds": self._reclaim_in(channel_id, channel, now),
            }
            for channel_id, channel in self._channels.items()
        ]
        return sorted(channels, key=lambda entry: not entry["providerConnected"])

    def _reclaim_in(self, channel_id: str, channel: Channel, now: float) -> float | None:
        expiry = self._expiry[channel_id]

        if channel.provider_connected or math.isinf(expiry):
            return None

        return round(max(0.0, expiry - now), 1)

    async def create_channel(
        self,
        *,
        channel_id: str | None = None,
        metadata: dict | None = None,
        ttl_seconds: float | None = None,
    ) -> Channel:
        async with self._lock:
            if len(self._channels) >= self.settings.maximum_channels:
                raise ChannelCapacityError("The registry reached its maximum number of channels")

            channel_id = channel_id or generate_token(12)

            if channel_id in self._channels:
                raise ChannelCapacityError(f"Channel already exists: {channel_id}")

            channel = self._channel_class(
                channel_id=channel_id,
                mcp_token=self._unique_token(self._by_mcp_token),
                provider_token=self._unique_token(self._by_provider_token),
                settings=self.settings,
                metadata=metadata or {},
            )

            now = time.monotonic()
            self._channels[channel_id] = channel
            self._by_mcp_token[channel.mcp_token] = channel_id
            self._by_provider_token[channel.provider_token] = channel_id
            self._created[channel_id] = now
            grace = self.settings.offline_ttl_seconds if ttl_seconds is None else ttl_seconds
            self._expiry[channel_id] = now + grace

        logger.info("Channel created: %s", channel_id)
        await self._dispatch("on_channel_created", channel)
        return channel

    async def remove_channel(self, channel_id: str, *, require_offline: bool = False) -> bool:
        async with self._lock:
            channel = self._channels.get(channel_id)

            if channel is None:
                return False

            if require_offline and channel.provider_connected:
                return False

            del self._channels[channel_id]
            self._by_mcp_token.pop(channel.mcp_token, None)
            self._by_provider_token.pop(channel.provider_token, None)
            self._expiry.pop(channel_id, None)
            self._created.pop(channel_id, None)

        await channel.close()
        await self._dispatch("on_channel_removed", channel)
        logger.info("Channel removed: %s", channel_id)
        return True

    async def purge_expired(self) -> int:
        now = time.monotonic()
        expired = [
            channel_id
            for channel_id, channel in self._channels.items()
            if not channel.provider_connected and self._expiry[channel_id] <= now
        ]
        removed = 0

        for channel_id in expired:
            if await self.remove_channel(channel_id, require_offline=True):
                removed += 1

        return removed

    async def provider_connected(self, channel: Channel) -> None:
        if channel.channel_id in self._channels:
            self._expiry[channel.channel_id] = float("inf")

        await self._dispatch("on_provider_connected", channel)

    async def provider_disconnected(self, channel: Channel) -> None:
        if channel.channel_id in self._channels and not channel.provider_connected:
            self._expiry[channel.channel_id] = time.monotonic() + self.settings.offline_ttl_seconds

        await self._dispatch("on_provider_disconnected", channel)

    async def close_all(self) -> None:
        for channel_id in list(self._channels):
            await self.remove_channel(channel_id)

    def _unique_token(self, index: dict[str, str]) -> str:
        while True:
            token = generate_token()

            if token not in index:
                return token

    async def _dispatch(self, event: str, channel: Channel) -> None:
        for listener in self._listeners:
            try:
                await getattr(listener, event)(channel)
            except Exception:
                logger.exception("Listener %s failed on %s", type(listener).__name__, event)
