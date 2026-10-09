from __future__ import annotations

import httpx

from mcpgtw.config import GatewaySettings
from mcpgtw.oauth.claims import principal_from_claims
from mcpgtw.oauth.http_fetch import bounded_json
from mcpgtw.oauth.token_verifier import AccessTokenVerifier
from mcpgtw.oauth.verified_principal import VerifiedPrincipal


class IntrospectionAccessTokenVerifier(AccessTokenVerifier):
    def __init__(self, settings: GatewaySettings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.client = client or httpx.AsyncClient(
            timeout=settings.oauth_http_timeout_seconds, follow_redirects=False, trust_env=False
        )

    async def verify(self, raw_token: str, expected_resource: str) -> VerifiedPrincipal | None:
        data = await bounded_json(
            self.client,
            "POST",
            self.settings.oauth_introspection_url,
            self.settings.oauth_max_metadata_bytes,
            data={"token": raw_token, "token_type_hint": "access_token"},
            auth=(
                self.settings.oauth_introspection_client_id,
                self.settings.oauth_introspection_client_secret.get_secret_value(),
            ),
        )

        if data.get("active") is not True:
            return None

        return principal_from_claims(
            data, self.settings.oauth_authorization_servers[0], expected_resource
        )
