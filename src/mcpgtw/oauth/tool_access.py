from __future__ import annotations

from abc import ABC, abstractmethod

from mcp.types import CallToolResult, TextContent, Tool

from mcpgtw.channel import Channel
from mcpgtw.oauth.resource_metadata import OAuthMetadataPublisher
from mcpgtw.oauth.secured_tool import SecuredTool


class ToolAccessPolicy(ABC):
    @abstractmethod
    def required_scopes(self, channel: Channel, tool_name: str) -> frozenset[str]: ...

    def describe(self, channel: Channel, tool: Tool) -> SecuredTool:
        schemes = [{"type": "oauth2", "scopes": sorted(self.required_scopes(channel, tool.name))}]
        return SecuredTool(
            **{**tool.model_dump(), "meta": {**(tool.meta or {}), "securitySchemes": schemes}},
            securitySchemes=schemes,
        )

    def challenge(
        self, metadata: OAuthMetadataPublisher, reason: str, scopes: frozenset[str]
    ) -> CallToolResult:
        challenge = metadata.challenge(
            "insufficient_scope" if reason == "insufficient_scope" else "invalid_token", scopes
        )
        return CallToolResult(
            content=[TextContent(type="text", text="Authorization required for this tool")],
            is_error=True,
            meta={
                "mcp/www_authenticate": [
                    challenge + ', error_description="Authorization required for this tool"'
                ]
            },
        )


class RequiredScopesToolAccess(ToolAccessPolicy):
    def __init__(self, scopes: frozenset[str]) -> None:
        self.scopes = scopes

    def required_scopes(self, channel: Channel, tool_name: str) -> frozenset[str]:
        return self.scopes
