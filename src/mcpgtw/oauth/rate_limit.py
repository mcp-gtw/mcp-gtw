from __future__ import annotations

import math
import time
from abc import ABC, abstractmethod
from heapq import heappop, heappush

from mcpgtw.errors import OAuthRateLimitError
from mcpgtw.oauth.rate_budget import RateBudget


class OAuthRateLimitPolicy(ABC):
    @abstractmethod
    def admit(self, key: str) -> bool: ...

    @abstractmethod
    def retry_after(self, key: str) -> int: ...

    def enforce(self, key: str) -> None:
        if not self.admit(key):
            raise OAuthRateLimitError(self.retry_after(key))


class WindowOAuthRateLimitPolicy(OAuthRateLimitPolicy):
    """Bounded request budgets with progressive backoff and deterministic expiry."""

    def __init__(
        self,
        requests: int = 120,
        window_seconds: float = 1,
        maximum_keys: int = 10000,
        backoff_seconds: float = 1,
        maximum_backoff_seconds: float = 30,
    ) -> None:
        if (
            requests < 1
            or not math.isfinite(window_seconds)
            or window_seconds <= 0
            or maximum_keys < 1
            or not math.isfinite(backoff_seconds)
            or not math.isfinite(maximum_backoff_seconds)
            or not 0 < backoff_seconds <= maximum_backoff_seconds
        ):
            raise ValueError("Rate limits must be finite and positive")

        self.requests = requests
        self.window_seconds = window_seconds
        self.maximum_keys = maximum_keys
        self.backoff_seconds = backoff_seconds
        self.maximum_backoff_seconds = maximum_backoff_seconds
        self._budgets: dict[str, RateBudget] = {}
        self._expiry: list[tuple[float, str]] = []

    def admit(self, key: str) -> bool:
        now = time.monotonic()

        while self._expiry and self._expiry[0][0] <= now:
            _, expired = heappop(self._expiry)
            budget = self._budgets[expired]
            deadline = max(budget.reset_at, budget.blocked_until)

            if deadline > now:
                heappush(self._expiry, (deadline, expired))
            else:
                del self._budgets[expired]

        budget = self._budgets.get(key)

        if budget is None:
            if len(self._budgets) >= self.maximum_keys:
                return False

            budget = RateBudget(now + self.window_seconds)
            self._budgets[key] = budget
            heappush(self._expiry, (budget.reset_at, key))

        if budget.blocked_until > now:
            return False

        if budget.used >= self.requests:
            delay = min(
                self.maximum_backoff_seconds,
                self.backoff_seconds * 2 ** min(budget.denials, 32),
            )
            budget.denials += 1
            budget.blocked_until = now + delay
            return False

        budget.used += 1
        return True

    def retry_after(self, key: str) -> int:
        budget = self._budgets.get(key)
        deadline = max(budget.reset_at, budget.blocked_until) if budget else 0
        return max(1, math.ceil(deadline - time.monotonic()))
