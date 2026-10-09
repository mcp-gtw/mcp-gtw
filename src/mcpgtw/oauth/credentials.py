from __future__ import annotations

import re

from starlette.requests import Request

from mcpgtw.oauth.access_error import McpAccessError

_BEARER = re.compile(r"(?i:Bearer) ([A-Za-z0-9._~+/-]+=*)\Z")


def bearer_credential(request: Request, maximum_bytes: int) -> str:
    headers = request.headers.getlist("authorization")

    if not headers:
        raise McpAccessError("missing_token")

    if len(headers) != 1 or len(headers[0].encode()) > maximum_bytes + 7:
        raise McpAccessError("invalid_token")

    match = _BEARER.fullmatch(headers[0])

    if match is None:
        raise McpAccessError("invalid_token")

    return match.group(1)
