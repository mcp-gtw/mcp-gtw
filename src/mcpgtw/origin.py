from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable


class OriginPolicy(ABC):
    """Decides whether a request or websocket ``Origin`` header is allowed.

    A missing origin is always allowed because non-browser clients omit it.
    Swap the implementation for regex matching, per-tenant rules or anything else.
    """

    def allows(self, origin: str | None) -> bool:
        if origin is None:
            return True

        return self.allows_origin(origin)

    @abstractmethod
    def allows_origin(self, origin: str) -> bool: ...


class ListOriginPolicy(OriginPolicy):
    """The default policy: allow origins in a fixed set, or every origin when it holds ``*``."""

    def __init__(self, allowed_origins: Iterable[str]) -> None:
        self._allowed = frozenset(allowed_origins)
        self._wildcard = "*" in self._allowed

    def allows_origin(self, origin: str) -> bool:
        return self._wildcard or origin in self._allowed
