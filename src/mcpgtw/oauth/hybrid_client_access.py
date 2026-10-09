from __future__ import annotations

from collections.abc import Callable

from starlette.requests import Request

from mcpgtw.channel import Channel
from mcpgtw.oauth.access_decision import AuthorizedMcpRequest
from mcpgtw.oauth.access_error import McpAccessError
from mcpgtw.oauth.client_access import McpClientAccessController
from mcpgtw.oauth.credentials import bearer_credential
from mcpgtw.registry import ChannelRegistry


class HybridMcpAccessController(McpClientAccessController):
    def __init__(
        self,
        registry: ChannelRegistry,
        token_access: McpClientAccessController,
        oauth_access: McpClientAccessController,
        static_eligible: Callable[[Channel], bool],
        maximum_token_bytes: int = 8192,
    ) -> None:
        self.registry = registry
        self.token_access = token_access
        self.oauth_access = oauth_access
        self.static_eligible = static_eligible
        self.maximum_token_bytes = maximum_token_bytes

    async def authorize(self, request: Request) -> AuthorizedMcpRequest:
        token = bearer_credential(request, self.maximum_token_bytes)
        channel = self.registry.resolve_mcp_token(token)

        if channel is None:
            return await self.oauth_access.authorize(request)

        if not self.static_eligible(channel):
            raise McpAccessError("invalid_token")

        return await self.token_access.authorize(request)
