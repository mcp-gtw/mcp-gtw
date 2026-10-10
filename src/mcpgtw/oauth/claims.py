from __future__ import annotations

import time
from typing import Any

from mcpgtw.oauth.verified_principal import VerifiedPrincipal


def principal_from_claims(
    claims: dict[str, Any],
    issuer: str,
    resource: str,
    skew: int = 0,
) -> VerifiedPrincipal | None:
    audience = claims.get("aud")
    audiences = [audience] if isinstance(audience, str) else audience
    expiry = claims.get("exp")
    subject = claims.get("sub")
    client = claims.get("client_id") or claims.get("azp")
    scope = claims.get("scope", "")

    if (
        claims.get("iss") != issuer
        or not isinstance(audiences, list)
        or any(not isinstance(value, str) or not value for value in audiences)
        or resource not in audiences
        or type(expiry) is not int
        or expiry <= time.time() - skew
        or not isinstance(subject, str)
        or not subject
        or not isinstance(client, str)
        or not client
        or not isinstance(scope, str)
        or any(ord(c) < 32 or ord(c) > 126 or c in '"\\' for c in scope)
        or (claims.get("client_id") and claims.get("azp") and client != claims["azp"])
        or claims.get("token_use", "access") != "access"
    ):
        return None

    for name in ("nbf", "iat"):
        value = claims.get(name)

        if value is not None and (type(value) is not int or value > time.time() + skew):
            return None

    return VerifiedPrincipal(issuer, subject, client, frozenset(scope.split()), expiry)
