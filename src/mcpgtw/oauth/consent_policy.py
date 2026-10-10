from __future__ import annotations

from abc import ABC, abstractmethod


class ConsentPolicy(ABC):
    @abstractmethod
    async def approve(
        self, subject: str, client_id: str, resource: str, scopes: frozenset[str]
    ) -> bool: ...

    @abstractmethod
    async def validate(
        self, subject: str, client_id: str, resource: str, scopes: frozenset[str]
    ) -> bool: ...

    async def binding(self, subject: str, client_id: str, resource: str) -> str | None:
        return resource
