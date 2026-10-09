from __future__ import annotations

import hashlib
import hmac
import secrets
from abc import ABC, abstractmethod

from starlette.requests import Request

from mcpgtw.authenticator import Authenticator
from mcpgtw.oauth.access_context import McpAccessContext
from mcpgtw.oauth.access_decision import AuthorizedMcpRequest
from mcpgtw.oauth.access_error import McpAccessError


class McpClientAccessController(ABC):
    @abstractmethod
    async def authorize(self, request: Request) -> AuthorizedMcpRequest: ...


class TokenMcpAccessController(McpClientAccessController):
    def __init__(self, authenticator: Authenticator) -> None:
        self.authenticator = authenticator
        self._key = secrets.token_bytes(32)

    async def authorize(self, request: Request) -> AuthorizedMcpRequest:
        channel = await self.authenticator.authenticate_client(request)

        if channel is None:
            raise McpAccessError("invalid_token")

        identity = channel.channel_id + "\0" + request.headers.get("authorization", "")
        principal_id = hmac.new(self._key, identity.encode(), hashlib.sha256).hexdigest()
        context = McpAccessContext(
            channel.channel_id, "token", principal_id, None, frozenset(), None, None
        )
        return AuthorizedMcpRequest(channel, context)
