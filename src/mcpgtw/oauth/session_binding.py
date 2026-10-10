from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import replace

from mcpgtw.oauth.access_context import McpAccessContext


class McpSessionBindingStore(ABC):
    @abstractmethod
    def bind(self, session_id: str, context: McpAccessContext) -> bool: ...

    @abstractmethod
    def accepts(self, session_id: str, context: McpAccessContext) -> bool: ...

    @abstractmethod
    def remove(self, session_id: str) -> None: ...

    @abstractmethod
    def remove_channel(self, channel_id: str) -> None: ...

    @abstractmethod
    def clear(self) -> None: ...


class MemoryMcpSessionBindingStore(McpSessionBindingStore):
    """Process-local bindings for a process-local MCP session manager."""

    def __init__(self, maximum_sessions: int = 10000, idle_seconds: float = 900) -> None:
        if maximum_sessions < 1 or idle_seconds <= 0:
            raise ValueError("Session limits must be positive")

        self.maximum_sessions = maximum_sessions
        self.idle_seconds = idle_seconds
        self._bindings: dict[str, tuple[McpAccessContext, float]] = {}

    @staticmethod
    def _identity(context: McpAccessContext) -> McpAccessContext:
        return replace(context, scopes=frozenset(), expires_at=None)

    def bind(self, session_id: str, context: McpAccessContext) -> bool:
        self._purge()
        identity = self._identity(context)
        existing = self._bindings.get(session_id)

        if existing is not None and existing[0] != identity:
            return False

        if existing is None and len(self._bindings) >= self.maximum_sessions:
            return False

        self._bindings.pop(session_id, None)
        self._bindings[session_id] = (identity, time.monotonic() + self.idle_seconds)
        return True

    def accepts(self, session_id: str, context: McpAccessContext) -> bool:
        existing = self._bindings.get(session_id)

        if (
            existing is None
            or existing[1] <= time.monotonic()
            or existing[0] != self._identity(context)
        ):
            return False

        self._bindings.pop(session_id)
        self._bindings[session_id] = (existing[0], time.monotonic() + self.idle_seconds)
        return True

    def _purge(self) -> None:
        now = time.monotonic()

        while self._bindings:
            key = next(iter(self._bindings))

            if self._bindings[key][1] > now:
                break

            self._bindings.pop(key)

    def remove(self, session_id: str) -> None:
        self._bindings.pop(session_id, None)

    def remove_channel(self, channel_id: str) -> None:
        self._bindings = {
            key: value for key, value in self._bindings.items() if value[0].channel_id != channel_id
        }

    def clear(self) -> None:
        self._bindings.clear()
