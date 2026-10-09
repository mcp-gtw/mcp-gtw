from __future__ import annotations

from urllib.parse import urlsplit

from starlette.responses import JSONResponse

from mcpgtw.config import GatewaySettings


class OAuthMetadataPublisher:
    def __init__(self, settings: GatewaySettings) -> None:
        self.settings = settings
        url = urlsplit(settings.oauth_resource_url)
        self.path = "/.well-known/oauth-protected-resource" + url.path
        self.url = f"{url.scheme}://{url.netloc}{self.path}"

    async def metadata(self) -> JSONResponse:
        return JSONResponse(
            {
                "resource": self.settings.oauth_resource_url,
                "authorization_servers": self.settings.oauth_authorization_servers,
                "scopes_supported": self.settings.oauth_required_scopes,
                "bearer_methods_supported": ["header"],
            },
            headers={"Cache-Control": "no-store"},
        )

    def challenge(self, reason: str) -> str:
        scopes = " ".join(self.settings.oauth_required_scopes)
        header = f'Bearer resource_metadata="{self.url}", scope="{scopes}"'

        if reason in ("invalid_token", "insufficient_scope"):
            header += f', error="{reason}"'

        return header
