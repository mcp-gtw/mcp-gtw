from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChannelGrant:
    scopes: frozenset[str]
    expires_at: int
