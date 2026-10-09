from __future__ import annotations

import json
from typing import Any

import httpx

from mcpgtw.oauth.access_error import McpAccessError


async def bounded_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    maximum_bytes: int,
    **kwargs: Any,
) -> dict[str, Any]:
    headers = {**kwargs.pop("headers", {}), "Accept-Encoding": "identity"}

    try:
        async with client.stream(method, url, headers=headers, **kwargs) as response:
            response.raise_for_status()

            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise ValueError("Encoded OAuth metadata is not accepted")

            data = bytearray()

            async for chunk in response.aiter_bytes(chunk_size=8192):
                if len(chunk) > maximum_bytes - len(data):
                    raise ValueError("Response size exceeded")

                data.extend(chunk)

        result = json.loads(data)

        if not isinstance(result, dict):
            raise ValueError("Expected object")

        return result
    except (httpx.HTTPError, ValueError, RecursionError) as exc:
        raise McpAccessError("verifier_unavailable") from exc
