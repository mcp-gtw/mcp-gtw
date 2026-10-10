from __future__ import annotations

import asyncio
import json
import math
import re
from abc import ABC, abstractmethod
from urllib.parse import urlsplit

import httpcore

from mcpgtw.oauth.public_network import PublicNetworkBackend


class ClientMetadataResolver(ABC):
    @abstractmethod
    async def resolve(self, client_id: str) -> dict: ...


class HttpsClientMetadataResolver(ClientMetadataResolver):
    def __init__(
        self,
        timeout_seconds: float = 5,
        maximum_bytes: int = 65536,
        allowed_origins: frozenset[str] = frozenset(),
        network: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0 or maximum_bytes < 1:
            raise ValueError("Client metadata limits must be finite and positive")

        self.timeout_seconds = timeout_seconds
        self.maximum_bytes = maximum_bytes
        self.allowed_origins = allowed_origins
        self.network = network or PublicNetworkBackend()
        self.ssl_context = httpcore.default_ssl_context()
        self.slots = asyncio.Semaphore(4)

    async def resolve(self, client_id: str) -> dict:
        url = urlsplit(client_id)

        if (
            len(client_id) > 2048
            or url.scheme != "https"
            or not url.hostname
            or not url.path
            or url.username is not None
            or url.password is not None
            or url.fragment
            or any(ord(c) < 33 or ord(c) > 126 or c in '\\"' for c in client_id)
            or (self.allowed_origins and f"https://{url.netloc}" not in self.allowed_origins)
        ):
            raise ValueError("Invalid client metadata URL")

        async with (
            asyncio.timeout(self.timeout_seconds),
            self.slots,
            httpcore.AsyncConnectionPool(
                network_backend=self.network,
                ssl_context=self.ssl_context,
                max_connections=1,
                max_keepalive_connections=0,
            ) as pool,
            pool.stream(
                "GET",
                client_id,
                headers={"Accept": "application/json", "Accept-Encoding": "identity"},
            ) as response,
        ):
            headers = {key.lower(): value for key, value in response.headers}
            cache_control = headers.get(b"cache-control", b"").lower().decode("ascii")

            if (
                response.status != 200
                or headers.get(b"content-type", b"").split(b";")[0] != b"application/json"
                or headers.get(b"content-encoding", b"identity").lower() != b"identity"
            ):
                raise ValueError("Invalid client metadata response")

            data = bytearray()

            async for chunk in response.aiter_stream():
                if len(chunk) > self.maximum_bytes - len(data):
                    raise ValueError("Client metadata exceeds size limit")

                data.extend(chunk)

        result = json.loads(data)

        if (
            not isinstance(result, dict)
            or result.get("client_id") != client_id
            or "client_secret" in result
            or "client_secret_expires_at" in result
            or "jwks" in result
        ):
            raise ValueError("Invalid client metadata document")

        methods = result.get(
            "token_endpoint_auth_methods_supported",
            [result.get("token_endpoint_auth_method", "none")],
        )

        if not isinstance(methods, list) or "none" not in methods:
            raise ValueError("Client does not support the public PKCE method")

        result["token_endpoint_auth_method"] = "none"
        max_age = re.search(r"(?:^|,)\s*max-age=(\d+)", cache_control)
        result["_cache_ttl_seconds"] = (
            0
            if "no-store" in cache_control or "no-cache" in cache_control
            else min(300, int(max_age[1]))
            if max_age
            else 300
        )
        return result
