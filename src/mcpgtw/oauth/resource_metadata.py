from __future__ import annotations

from abc import ABC, abstractmethod
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse

from mcpgtw.config import GatewaySettings
from mcpgtw.oauth.rate_limit import WindowOAuthRateLimitPolicy


class OAuthMetadataPublisher(ABC):
    path: str
    url: str

    @abstractmethod
    async def metadata(self, request: Request) -> JSONResponse: ...

    @abstractmethod
    def challenge(self, reason: str, required_scopes: frozenset[str] | None = None) -> str: ...


class ProtectedResourceMetadataPublisher(OAuthMetadataPublisher):
    def __init__(self, settings: GatewaySettings) -> None:
        self.settings = settings
        self.rate_limit = WindowOAuthRateLimitPolicy(
            settings.oauth_rate_limit_requests,
            settings.oauth_rate_limit_window_seconds,
            settings.oauth_rate_limit_maximum_keys,
            settings.oauth_rate_limit_backoff_seconds,
            settings.oauth_rate_limit_maximum_backoff_seconds,
        )
        url = urlsplit(settings.oauth_resource_url)
        self.path = "/.well-known/oauth-protected-resource" + url.path
        self.url = f"{url.scheme}://{url.netloc}{self.path}"

    async def metadata(self, request: Request) -> JSONResponse:
        if not self.rate_limit.admit(request.client.host if request.client else "unknown"):
            return JSONResponse(
                {"error": "Request budget exceeded"},
                status_code=429,
                headers={
                    "Cache-Control": "no-store",
                    "Retry-After": str(
                        self.rate_limit.retry_after(
                            request.client.host if request.client else "unknown"
                        )
                    ),
                },
            )

        return JSONResponse(
            {
                "resource": self.settings.oauth_resource_url,
                "authorization_servers": self.settings.oauth_authorization_servers,
                "scopes_supported": self.settings.oauth_supported_scopes,
                "bearer_methods_supported": ["header"],
            },
            headers={"Cache-Control": "no-store"},
        )

    def challenge(self, reason: str, required_scopes: frozenset[str] | None = None) -> str:
        scopes = " ".join(
            self.settings.oauth_required_scopes
            if required_scopes is None
            else sorted(required_scopes)
        )
        header = f'Bearer resource_metadata="{self.url}", scope="{scopes}"'

        if reason in ("invalid_token", "insufficient_scope"):
            header += f', error="{reason}"'

        return header
