from starlette.requests import Request
from starlette.responses import JSONResponse

from mcpgtw.errors import OAuthRateLimitError
from mcpgtw.oauth.access_error import McpAccessError


async def rate_limit_response(request: Request, error: OAuthRateLimitError) -> JSONResponse:
    return JSONResponse(
        {"error": "temporarily_unavailable"},
        status_code=429,
        headers={"Cache-Control": "no-store", "Retry-After": str(error.retry_after)},
    )


async def unavailable_response(request: Request, error: McpAccessError) -> JSONResponse:
    return JSONResponse(
        {"error": "temporarily_unavailable"},
        status_code=503,
        headers={"Cache-Control": "no-store", "Retry-After": "1"},
    )
