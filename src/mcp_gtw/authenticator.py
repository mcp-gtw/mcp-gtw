from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from mcp_gtw.channel import Channel

if TYPE_CHECKING:
    from fastapi import WebSocket
    from starlette.requests import Request

    from mcp_gtw.registry import ChannelRegistry

_BEARER_PREFIX = "Bearer "


def extract_bearer_token(authorization: str | None) -> str | None:
    if authorization is None or not authorization.startswith(_BEARER_PREFIX):
        return None

    return authorization[len(_BEARER_PREFIX) :].strip() or None


class Authenticator(ABC):
    """Decides which channel an incoming connection is authorized to use, or denies it.

    This is the single seam for every access model. The default resolves a bearer/query token
    to an existing channel. Override it to authenticate by username/password, to validate and
    upsert a client-supplied token, or anything else. Returning ``None`` denies the connection.
    """

    @abstractmethod
    async def authenticate_provider(self, websocket: WebSocket) -> Channel | None: ...

    @abstractmethod
    async def authenticate_client(self, request: Request) -> Channel | None: ...


class TokenAuthenticator(Authenticator):
    """The default authenticator: a connection is admitted when it carries a known token."""

    def __init__(self, registry: ChannelRegistry) -> None:
        self._registry = registry

    async def authenticate_provider(self, websocket: WebSocket) -> Channel | None:
        return self._registry.resolve_provider_token(websocket.query_params.get("token"))

    async def authenticate_client(self, request: Request) -> Channel | None:
        token = extract_bearer_token(request.headers.get("authorization"))
        return self._registry.resolve_mcp_token(token)
