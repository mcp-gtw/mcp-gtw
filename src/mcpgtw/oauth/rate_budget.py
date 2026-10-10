from dataclasses import dataclass


@dataclass(slots=True)
class RateBudget:
    reset_at: float
    used: int = 0
    denials: int = 0
    blocked_until: float = 0
