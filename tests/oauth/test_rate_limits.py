from __future__ import annotations

import pytest
from pydantic import ValidationError

from mcpgtw.config import GatewaySettings
from mcpgtw.errors import OAuthRateLimitError
from mcpgtw.oauth.endpoint_limits import OAuthEndpointLimits
from mcpgtw.oauth.rate_limit import WindowOAuthRateLimitPolicy


def test_progressive_backoff_expiry_and_bounded_heap(monkeypatch):
    now = 0.0
    monkeypatch.setattr("mcpgtw.oauth.rate_limit.time.monotonic", lambda: now)
    policy = WindowOAuthRateLimitPolicy(1, 60, 2, 1, 4)
    policy.enforce("alice")
    assert not policy.admit("alice")
    assert not policy.admit("alice")
    assert policy.retry_after("alice") == 60
    now = 1.0
    assert not policy.admit("alice")
    assert policy._budgets["alice"].blocked_until == 3
    now = 3.0
    assert not policy.admit("alice")
    assert policy._budgets["alice"].blocked_until == 7
    now = 7.0
    assert not policy.admit("alice")
    assert policy._budgets["alice"].blocked_until == 11
    assert policy.admit("bob")
    assert not policy.admit("new-account")
    assert policy.retry_after("new-account") == 1
    assert len(policy._expiry) == len(policy._budgets) == 2

    with pytest.raises(OAuthRateLimitError) as failure:
        policy.enforce("alice")

    assert failure.value.retry_after == 53
    now = 60.0
    assert policy.admit("alice")
    assert len(policy._expiry) == len(policy._budgets) == 2


def test_backoff_extends_window_without_duplicate_heap_entries(monkeypatch):
    now = 0.0
    monkeypatch.setattr("mcpgtw.oauth.rate_limit.time.monotonic", lambda: now)
    policy = WindowOAuthRateLimitPolicy(1, 1, 1, 5, 5)
    assert policy.admit("one")
    assert not policy.admit("one")
    now = 2.0
    assert not policy.admit("one")
    assert not policy.admit("two")
    assert policy.retry_after("one") == 3
    assert policy._expiry == [(5.0, "one")]
    now = 5.0
    assert policy.admit("two")


@pytest.mark.parametrize(
    "args",
    [
        (1, 1, 1, 0),
        (1, 1, 1, float("inf")),
        (1, 1, 1, 1, float("nan")),
        (1, 1, 1, 2, 1),
    ],
)
def test_invalid_backoff(args):
    with pytest.raises(ValueError):
        WindowOAuthRateLimitPolicy(*args)


def test_configured_endpoint_limits_and_policy_class():
    class CustomPolicy(WindowOAuthRateLimitPolicy):
        pass

    class CustomLimits(OAuthEndpointLimits):
        policy_class = CustomPolicy

    config = GatewaySettings(
        oauth_embedded_browser_requests=2,
        oauth_embedded_client_requests=3,
        oauth_embedded_principal_requests=4,
        oauth_embedded_login_attempts=5,
        oauth_rate_limit_maximum_keys=6,
        oauth_rate_limit_backoff_seconds=2,
        oauth_rate_limit_maximum_backoff_seconds=8,
    )
    limits = CustomLimits(config)
    assert isinstance(limits.browser, CustomPolicy)
    assert limits.browser.requests == 2
    assert limits.client.requests == 3
    assert limits.principal.requests == 4
    assert limits.login.requests == 5
    assert limits.login.maximum_keys == 6
    assert limits.login.maximum_backoff_seconds == 8

    with pytest.raises(ValidationError, match="backoff"):
        GatewaySettings(oauth_rate_limit_backoff_seconds=31)


async def test_registry_limiter_recovery_requires_elapsed_backoff(monkeypatch):
    now = 0.0
    monkeypatch.setattr("mcpgtw.oauth.rate_limit.time.monotonic", lambda: now)
    policy = WindowOAuthRateLimitPolicy(1, 1)
    assert policy.admit("client")
    assert not policy.admit("client")
    policy.requests = 2
    assert not policy.admit("client")
    now = 1.0
    assert policy.admit("client")


async def test_metadata_retry_after_without_client_scope():
    from mcpgtw.oauth.resource_metadata import ProtectedResourceMetadataPublisher

    config = GatewaySettings(
        oauth_mode="resource_server",
        oauth_resource_url="https://resource.example/mcp",
        oauth_authorization_servers=["https://issuer.example"],
        oauth_jwks_url="https://issuer.example/jwks",
        oauth_rate_limit_requests=1,
    )
    publisher = ProtectedResourceMetadataPublisher(config)
    scope = {"type": "http", "headers": [], "client": None}
    from starlette.requests import Request

    assert (await publisher.metadata(Request(scope))).status_code == 200
    response = await publisher.metadata(Request(scope))
    assert response.status_code == 429
    assert response.headers["retry-after"] == "1"
    assert response.headers["cache-control"] == "no-store"
