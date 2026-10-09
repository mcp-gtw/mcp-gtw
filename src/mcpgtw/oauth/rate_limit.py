from __future__ import annotations

import math
import time


class OAuthRateLimitPolicy:
    """Bounded fixed-window budgets keyed by the trusted ASGI client address."""

    def __init__(
        self, requests: int = 120, window_seconds: float = 1, maximum_keys: int = 10000
    ) -> None:
        if (
            requests < 1
            or not math.isfinite(window_seconds)
            or window_seconds <= 0
            or maximum_keys < 1
        ):
            raise ValueError("Rate limits must be finite and positive")

        self.requests = requests
        self.window_seconds = window_seconds
        self.maximum_keys = maximum_keys
        self._budgets: dict[str, tuple[float, int]] = {}

    def admit(self, key: str) -> bool:
        now = time.monotonic()

        while self._budgets:
            oldest = next(iter(self._budgets))

            if self._budgets[oldest][0] > now:
                break

            self._budgets.pop(oldest)

        deadline, used = self._budgets.get(key, (now + self.window_seconds, 0))

        if used >= self.requests or (
            key not in self._budgets and len(self._budgets) >= self.maximum_keys
        ):
            return False

        self._budgets[key] = (deadline, used + 1)
        return True
