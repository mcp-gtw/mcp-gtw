from __future__ import annotations

from typing import Any, cast

from mcp.server.context import CallNext, HandlerResult, ServerRequestContext


class OAuthToolMetadataMiddleware:
    async def __call__(self, ctx: ServerRequestContext, call_next: CallNext) -> HandlerResult:
        result = await call_next(ctx)

        if (
            ctx.method != "tools/list"
            or ctx.request.scope["gateway_access_context"].credential_kind != "oauth"
        ):
            return result

        listed = cast(dict[str, Any], result)
        return {
            **listed,
            "tools": [
                {**tool, "securitySchemes": tool["_meta"]["securitySchemes"]}
                for tool in listed["tools"]
            ],
        }
