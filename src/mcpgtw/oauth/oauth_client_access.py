from __future__ import annotations

import time

from starlette.requests import Request

from mcpgtw.config import GatewaySettings
from mcpgtw.oauth.access_context import McpAccessContext
from mcpgtw.oauth.access_decision import AuthorizedMcpRequest
from mcpgtw.oauth.access_error import McpAccessError
from mcpgtw.oauth.channel_access import ChannelAccessPolicy
from mcpgtw.oauth.client_access import McpClientAccessController
from mcpgtw.oauth.credentials import bearer_credential
from mcpgtw.oauth.token_verifier import AccessTokenVerifier
from mcpgtw.registry import ChannelRegistry


def addressed_channel(request: Request) -> str:
    return request.scope["path"].removeprefix(request.scope.get("root_path", "")).strip("/")


class OAuthMcpAccessController(McpClientAccessController):
    def __init__(
        self,
        settings: GatewaySettings,
        registry: ChannelRegistry,
        verifier: AccessTokenVerifier,
        channel_access: ChannelAccessPolicy,
    ) -> None:
        self.settings = settings
        self.registry = registry
        self.verifier = verifier
        self.channel_access = channel_access

    async def authorize(self, request: Request) -> AuthorizedMcpRequest:
        token = bearer_credential(request, self.settings.oauth_max_token_bytes)
        principal = await self.verifier.verify(token, self.settings.oauth_resource_url)

        if (
            principal is None
            or principal.issuer not in self.settings.oauth_authorization_servers
            or principal.expires_at <= time.time()
        ):
            raise McpAccessError("invalid_token")

        if not set(self.settings.oauth_required_scopes) <= principal.scopes:
            raise McpAccessError("insufficient_scope")

        channel_id = await self.channel_access.resolve(principal, addressed_channel(request))
        channel = self.registry.get(channel_id) if channel_id is not None else None

        if channel is None:
            raise McpAccessError("channel_forbidden")

        return AuthorizedMcpRequest(
            channel,
            McpAccessContext(
                channel.channel_id,
                "oauth",
                principal.principal_id,
                principal.client_id,
                principal.scopes,
                principal.issuer,
                principal.expires_at,
            ),
        )
