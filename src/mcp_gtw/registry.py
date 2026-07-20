from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Iterable

from mcp_gtw.channel import Channel
from mcp_gtw.config import GatewaySettings
from mcp_gtw.errors import ChannelCapacityError
from mcp_gtw.expiry import ExpiryPolicy, TtlExpiryPolicy
from mcp_gtw.listeners import GatewayListener
from mcp_gtw.tokens import SecretsTokenProvider, TokenProvider

logger = logging.getLogger(__name__)


class ChannelRegistry:
    """Owns every channel and resolves the private and public tokens to channels."""

    def __init__(
        self,
        settings: GatewaySettings,
        listeners: Iterable[GatewayListener] = (),
        channel_class: type[Channel] = Channel,
        tokens: TokenProvider | None = None,
        expiry_policy: ExpiryPolicy | None = None,
    ) -> None:
        self.settings = settings
        self._listeners = list(listeners)
        self._channel_class = channel_class
        self._tokens = tokens or SecretsTokenProvider()
        self._expiry_policy = expiry_policy or TtlExpiryPolicy(settings.offline_ttl_seconds)
        self._track_created = settings.admin_enabled
        self._channels: dict[str, Channel] = {}
        self._by_mcp_token: dict[str, str] = {}
        self._by_provider_token: dict[str, str] = {}
        self._expiry: dict[str, float] = {}
        self._created: dict[str, float] = {}
        self._lock = asyncio.Lock()

    def add_listener(self, listener: GatewayListener) -> None:
        self._listeners.append(listener)

    @property
    def tokens(self) -> TokenProvider:
        return self._tokens

    @property
    def channel_count(self) -> int:
        return len(self._channels)

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
                "ageSeconds": self._age_seconds(channel_id, now),
                "reclaimInSeconds": self._reclaim_in(channel_id, channel, now),
            }
            for channel_id, channel in self._channels.items()
        ]
        return sorted(channels, key=lambda entry: not entry["providerConnected"])

    def _age_seconds(self, channel_id: str, now: float) -> float | None:
        created = self._created.get(channel_id)
        return None if created is None else round(now - created, 1)

    def _reclaim_in(self, channel_id: str, channel: Channel, now: float) -> float | None:
        if channel.provider_connected:
            return None

        return self._expiry_policy.reclaim_in(self._expiry[channel_id], now)

    async def create_channel(
        self,
        *,
        channel_id: str | None = None,
        metadata: dict | None = None,
        ttl_seconds: float | None = None,
        provider_token: str | None = None,
        mcp_token: str | None = None,
    ) -> Channel:
        async with self._lock:
            maximum_channels = self.settings.maximum_channels

            if maximum_channels is not None and len(self._channels) >= maximum_channels:
                raise ChannelCapacityError("The registry reached its maximum number of channels")

            channel_id = channel_id or self._tokens.generate(12)

            if channel_id in self._channels:
                raise ChannelCapacityError(f"Channel already exists: {channel_id}")

            resolved_mcp_token = mcp_token or self._unique_token()
            resolved_provider_token = provider_token or self._unique_token()

            if resolved_mcp_token == resolved_provider_token:
                raise ValueError("The mcp token and the provider token must be different")

            if self._token_taken(resolved_mcp_token) or self._token_taken(resolved_provider_token):
                raise ChannelCapacityError("A channel with the same token already exists")

            channel = self._channel_class(
                channel_id=channel_id,
                mcp_token=resolved_mcp_token,
                provider_token=resolved_provider_token,
                settings=self.settings,
                metadata=metadata or {},
            )

            now = time.monotonic()
            self._channels[channel_id] = channel
            self._by_mcp_token[channel.mcp_token] = channel_id
            self._by_provider_token[channel.provider_token] = channel_id
            self._expiry[channel_id] = self._expiry_policy.initial_deadline(now, ttl_seconds)

            if self._track_created:
                self._created[channel_id] = now

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
            if not channel.provider_connected
            and self._expiry_policy.is_expired(self._expiry[channel_id], now)
        ]
        removed = 0

        for channel_id in expired:
            if await self.remove_channel(channel_id, require_offline=True):
                removed += 1

        return removed

    async def provider_connected(self, channel: Channel) -> None:
        if channel.channel_id in self._channels:
            self._expiry[channel.channel_id] = self._expiry_policy.connected_deadline()

        await self._dispatch("on_provider_connected", channel)

    async def provider_disconnected(self, channel: Channel) -> None:
        if channel.channel_id in self._channels and not channel.provider_connected:
            self._expiry[channel.channel_id] = self._expiry_policy.disconnected_deadline(
                time.monotonic()
            )

        await self._dispatch("on_provider_disconnected", channel)

    async def close_all(self) -> None:
        for channel_id in list(self._channels):
            await self.remove_channel(channel_id)

    def _token_taken(self, token: str) -> bool:
        return token in self._by_mcp_token or token in self._by_provider_token

    def _unique_token(self) -> str:
        while True:
            token = self._tokens.generate()

            if not self._token_taken(token):
                return token

    async def _dispatch(self, event: str, channel: Channel) -> None:
        for listener in self._listeners:
            try:
                await getattr(listener, event)(channel)
            except Exception:
                logger.exception("Listener %s failed on %s", type(listener).__name__, event)
