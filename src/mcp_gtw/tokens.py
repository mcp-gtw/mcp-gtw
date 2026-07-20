from __future__ import annotations

import hmac
import secrets
from abc import ABC, abstractmethod


class TokenProvider(ABC):
    """Mints and compares the opaque tokens that authorize channel access.

    Swap the implementation to change how tokens look (length, encoding, derivation)
    or how they are compared. Comparison must be constant time to resist timing attacks.
    """

    @abstractmethod
    def generate(self, nbytes: int = 32) -> str: ...

    @abstractmethod
    def equals(self, received: str | None, expected: str) -> bool: ...


class SecretsTokenProvider(TokenProvider):
    """The default provider: URL-safe tokens from ``secrets`` compared in constant time."""

    def generate(self, nbytes: int = 32) -> str:
        return secrets.token_urlsafe(nbytes)

    def equals(self, received: str | None, expected: str) -> bool:
        if received is None:
            return False

        return hmac.compare_digest(received.encode(), expected.encode())
