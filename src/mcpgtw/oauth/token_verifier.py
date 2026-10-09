from __future__ import annotations

from abc import ABC, abstractmethod

from mcpgtw.oauth.verified_principal import VerifiedPrincipal


class AccessTokenVerifier(ABC):
    @abstractmethod
    async def verify(self, raw_token: str, expected_resource: str) -> VerifiedPrincipal | None: ...
