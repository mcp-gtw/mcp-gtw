from __future__ import annotations

import math
from abc import ABC, abstractmethod


class ExpiryPolicy(ABC):
    """Computes when an idle channel should be reclaimed.

    The registry owns the stored deadlines and the connected state. This policy owns the
    time math: what deadline a channel gets when created, connected or disconnected, whether
    a deadline has passed, and how long is left before reclamation.
    """

    @abstractmethod
    def initial_deadline(self, now: float, ttl_seconds: float | None) -> float: ...

    @abstractmethod
    def connected_deadline(self) -> float: ...

    @abstractmethod
    def disconnected_deadline(self, now: float) -> float: ...

    @abstractmethod
    def is_expired(self, deadline: float, now: float) -> bool: ...

    @abstractmethod
    def reclaim_in(self, deadline: float, now: float) -> float | None: ...


class TtlExpiryPolicy(ExpiryPolicy):
    """The default policy: a connected channel never expires, an offline one after a grace TTL."""

    def __init__(self, offline_ttl_seconds: float) -> None:
        self._offline_ttl_seconds = offline_ttl_seconds

    def initial_deadline(self, now: float, ttl_seconds: float | None) -> float:
        grace = self._offline_ttl_seconds if ttl_seconds is None else ttl_seconds
        return now + grace

    def connected_deadline(self) -> float:
        return math.inf

    def disconnected_deadline(self, now: float) -> float:
        return now + self._offline_ttl_seconds

    def is_expired(self, deadline: float, now: float) -> bool:
        return deadline <= now

    def reclaim_in(self, deadline: float, now: float) -> float | None:
        if math.isinf(deadline):
            return None

        return round(max(0.0, deadline - now), 1)
