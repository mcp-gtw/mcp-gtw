from __future__ import annotations

from dataclasses import dataclass

from mcpgtw.channel import Channel
from mcpgtw.oauth.access_context import McpAccessContext


@dataclass(frozen=True, slots=True)
class AuthorizedMcpRequest:
    channel: Channel
    context: McpAccessContext
