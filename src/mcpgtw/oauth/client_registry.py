from __future__ import annotations

import secrets
from urllib.parse import urlsplit

import httpcore
from mcp.shared.auth import OAuthClientInformationFull

from mcpgtw.oauth.client_metadata import ClientMetadataResolver
from mcpgtw.oauth.rate_limit import OAuthRateLimitPolicy, WindowOAuthRateLimitPolicy
from mcpgtw.oauth.state_store import OAuthStateStore

_INFORMATION_FIELDS = frozenset({"client_uri", "logo_uri", "policy_uri", "tos_uri", "contacts"})


class OAuthClientRegistry:
    def __init__(
        self,
        store: OAuthStateStore,
        issuer: str,
        resource: str,
        dcr_enabled: bool = False,
        metadata_resolver: ClientMetadataResolver | None = None,
        metadata_ttl_seconds: float = 300,
        scopes: frozenset[str] = frozenset({"mcp:access"}),
        metadata_limit: OAuthRateLimitPolicy | None = None,
    ) -> None:
        self.store = store
        self.issuer = issuer
        self.resource = resource
        self.dcr_enabled = dcr_enabled
        self.metadata_resolver = metadata_resolver
        self.metadata_ttl_seconds = metadata_ttl_seconds
        self.metadata_limit = metadata_limit or WindowOAuthRateLimitPolicy(10, 60)
        self.scopes = scopes
        self.pre_registered: dict[str, dict] = {}

    @staticmethod
    def validate(data: dict, client_id: str, secret: str = "", browser: bool = False) -> dict:
        client = OAuthClientInformationFull.model_validate(
            {**data, "client_id": client_id, "client_secret": secret or None}
        )
        redirects = [str(uri) for uri in client.redirect_uris or []]

        if (
            not redirects
            or len(redirects) > 10
            or client.token_endpoint_auth_method not in ("none", "client_secret_basic")
            or client.response_types != ["code"]
            or not set(client.grant_types) <= {"authorization_code", "refresh_token"}
            or "authorization_code" not in client.grant_types
            or bool(secret) != (client.token_endpoint_auth_method == "client_secret_basic")
        ):
            raise ValueError("Invalid client metadata")

        for uri in redirects:
            parsed = urlsplit(uri)

            if (
                len(uri) > 2048
                or parsed.username
                or parsed.password
                or parsed.fragment
                or not parsed.hostname
                or parsed.scheme not in ("https", "http")
                or (
                    parsed.scheme == "http"
                    and parsed.hostname not in ("127.0.0.1", "::1", "localhost")
                )
                or any(ord(c) < 33 or ord(c) > 126 for c in uri)
            ):
                raise ValueError("Invalid redirect URI")

        name = client.client_name or "MCP client"

        if len(name) > 128:
            raise ValueError("Invalid client name")

        return {
            "client_id": client_id,
            "secret": secret,
            "redirect_uris": redirects,
            "method": client.token_endpoint_auth_method,
            "name": name,
            "browser": browser,
            "grant_types": client.grant_types,
            "information": {
                key: value
                for key, value in client.model_dump(mode="json", exclude_none=True).items()
                if key in _INFORMATION_FIELDS
            },
        }

    def add(self, client_id: str, data: dict, secret: str = "", browser: bool = False) -> None:
        if not client_id or len(client_id) > 512 or client_id in self.pre_registered:
            raise ValueError("Invalid client identifier")

        self.pre_registered[client_id] = self.validate(data, client_id, secret, browser)

    async def get(self, client_id: str) -> dict | None:
        client = self.pre_registered.get(client_id) or await self.store.get("client", client_id)

        if (
            client is not None
            or self.metadata_resolver is None
            or not client_id.startswith("https://")
        ):
            return client

        cached = await self.store.get("cimd", client_id)

        if cached is not None:
            return cached

        if not self.metadata_limit.admit(client_id):
            return None

        try:
            data = await self.metadata_resolver.resolve(client_id)
            client = self.validate(data, client_id)
            ttl = min(
                self.metadata_ttl_seconds, data.get("_cache_ttl_seconds", self.metadata_ttl_seconds)
            )

            if ttl > 0:
                await self.store.put("cimd", client, ttl, client_id)
        except (
            ValueError,
            TypeError,
            RecursionError,
            OSError,
            httpcore.ProtocolError,
            httpcore.NetworkError,
            httpcore.TimeoutException,
        ):
            return None

        return client

    async def register(self, data: dict) -> dict:
        if (
            not self.dcr_enabled
            or data.get("token_endpoint_auth_method", "none") != "none"
            or set(data)
            - _INFORMATION_FIELDS
            - {
                "redirect_uris",
                "client_name",
                "token_endpoint_auth_method",
                "grant_types",
                "response_types",
                "scope",
            }
            or not isinstance(data.get("scope", ""), str)
            or not set(data.get("scope", " ".join(sorted(self.scopes))).split()) <= self.scopes
        ):
            raise ValueError("Unsupported client metadata")

        client_id = secrets.token_urlsafe(32)
        client = self.validate(
            {
                "token_endpoint_auth_method": "none",
                "response_types": ["code"],
                "grant_types": ["authorization_code", "refresh_token"],
                **data,
            },
            client_id,
        )
        await self.store.put("client", client, 2592000, client_id)
        return {
            **client["information"],
            "client_id": client_id,
            "client_name": client["name"],
            "redirect_uris": client["redirect_uris"],
            "token_endpoint_auth_method": "none",
            "grant_types": client["grant_types"],
            "response_types": ["code"],
            "scope": data.get("scope", " ".join(sorted(self.scopes))),
        }
