from __future__ import annotations

import asyncio
import time

import httpx
import jwt

from mcpgtw.config import GatewaySettings
from mcpgtw.oauth.access_error import McpAccessError
from mcpgtw.oauth.claims import principal_from_claims
from mcpgtw.oauth.http_fetch import bounded_json
from mcpgtw.oauth.token_verifier import AccessTokenVerifier
from mcpgtw.oauth.verified_principal import VerifiedPrincipal


class JwtAccessTokenVerifier(AccessTokenVerifier):
    def __init__(self, settings: GatewaySettings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.client = client or httpx.AsyncClient(
            timeout=settings.oauth_http_timeout_seconds, follow_redirects=False, trust_env=False
        )
        self._keys: dict[str, jwt.PyJWK] = {}
        self._deadline = 0.0
        self._refresh_after = 0.0
        self._lock = asyncio.Lock()

    async def _refresh(self) -> None:
        async with self._lock:
            if time.monotonic() < self._refresh_after:
                return

            self._refresh_after = time.monotonic() + 5
            data = await bounded_json(
                self.client,
                "GET",
                self.settings.oauth_jwks_url,
                self.settings.oauth_max_metadata_bytes,
            )
            keys = data.get("keys")

            if not isinstance(keys, list) or not 1 <= len(keys) <= 64:
                raise McpAccessError("verifier_unavailable")

            try:
                parsed = {
                    key["kid"]: jwt.PyJWK.from_dict(key)
                    for key in keys
                    if key.get("use", "sig") == "sig"
                }
            except (AttributeError, KeyError, TypeError, ValueError, jwt.PyJWTError) as exc:
                raise McpAccessError("verifier_unavailable") from exc

            self._keys = parsed
            self._deadline = time.monotonic() + self.settings.oauth_jwks_cache_ttl_seconds

    async def verify(self, raw_token: str, expected_resource: str) -> VerifiedPrincipal | None:
        try:
            header = jwt.get_unverified_header(raw_token)
            algorithm = header.get("alg")
            kid = header.get("kid")

            if (
                algorithm not in self.settings.oauth_jwt_allowed_algorithms
                or not isinstance(kid, str)
                or header.get("typ") not in ("at+jwt", "application/at+jwt")
                or "jku" in header
                or "x5u" in header
                or header.get("crit")
            ):
                return None

            if time.monotonic() >= self._deadline or kid not in self._keys:
                await self._refresh()

            key = self._keys.get(kid)

            if key is None or time.monotonic() >= self._deadline or key.algorithm_name != algorithm:
                return None

            claims = jwt.decode(
                raw_token,
                key.key,
                algorithms=[algorithm],
                audience=expected_resource,
                issuer=self.settings.oauth_authorization_servers[0],
                leeway=self.settings.oauth_clock_skew_seconds,
                options={"require": ["exp", "iss", "aud", "sub", "iat"]},
            )
            return principal_from_claims(
                claims,
                self.settings.oauth_authorization_servers[0],
                expected_resource,
                self.settings.oauth_clock_skew_seconds,
            )
        except (jwt.PyJWTError, ValueError, TypeError, RecursionError):
            return None
