from __future__ import annotations

import hmac
import secrets
from collections.abc import Iterable

_BEARER_PREFIX = "Bearer "


def generate_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def constant_time_equals(received: str | None, expected: str) -> bool:
    return received is not None and hmac.compare_digest(received, expected)


def extract_bearer_token(authorization: str | None) -> str | None:
    if authorization is None or not authorization.startswith(_BEARER_PREFIX):
        return None

    return authorization[len(_BEARER_PREFIX) :].strip() or None


def origin_is_allowed(origin: str | None, allowed_origins: Iterable[str]) -> bool:
    if origin is None:
        return True

    return origin in allowed_origins
