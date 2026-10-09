from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class McpAccessContext:
    channel_id: str
    credential_kind: str
    principal_id: str
    client_id: str | None
    scopes: frozenset[str]
    issuer: str | None
    expires_at: int | None
