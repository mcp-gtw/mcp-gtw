from __future__ import annotations

from typing import Any, Protocol


class JsonWebSocket(Protocol):
    """The minimal websocket surface a channel needs to talk to a provider."""

    async def send_json(self, data: Any) -> None: ...

    async def close(self, code: int = 1000, reason: str | None = None) -> None: ...
