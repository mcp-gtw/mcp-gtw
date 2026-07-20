from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class PendingRequest:
    """A relayed request awaiting the provider's result, with its originating MCP session."""

    future: asyncio.Future[Any]
    session: Any | None = None
    progress_token: str | int | None = None
