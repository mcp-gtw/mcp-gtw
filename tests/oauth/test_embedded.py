from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import random
import time
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from starlette.requests import Request

from mcpgtw.config import GatewaySettings
from mcpgtw.errors import GatewayConfigurationError, OAuthRateLimitError
from mcpgtw.gateway import Gateway
from mcpgtw.oauth.authorization_server import EmbeddedAuthorizationServer
from mcpgtw.oauth.channel_access import DenyUnlessGranted
from mcpgtw.oauth.channel_grants import MemoryChannelGrantStore
from mcpgtw.oauth.client_registry import OAuthClientRegistry
from mcpgtw.oauth.consent_policy import ConsentPolicy
from mcpgtw.oauth.identity import IdentityAuthenticator, SqlitePasswordIdentity
from mcpgtw.oauth.rate_limit import WindowOAuthRateLimitPolicy
from mcpgtw.oauth.signing_key import OAuthSigningKey
from mcpgtw.oauth.state_store import SqliteOAuthStateStore

ISSUER = "https://game.example"
RESOURCE = ISSUER + "/mcp"
REDIRECT = "https://host.example/callback"
VERIFIER = "a" * 64
CHALLENGE = (
    base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()
)


class Identity(IdentityAuthenticator):
    async def authenticate(self, username, password, register=False):
        return "alice" if username == "alice" and password == "correct-password" else None


class Consent(ConsentPolicy):
    async def approve(self, subject, client_id, resource, scopes):
        return subject == "alice"

    async def validate(self, subject, client_id, resource, scopes):
        return subject == "alice"


def settings(**kwargs):
    return GatewaySettings(
        **{
            "oauth_mode": "embedded",
            "oauth_resource_url": RESOURCE,
            "oauth_authorization_servers": [ISSUER],
            "oauth_jwks_url": ISSUER + "/oauth/jwks",
            "oauth_embedded_dcr_enabled": True,
            **kwargs,
        }
    )


@pytest.fixture
async def service(tmp_path):
    config = settings()
    store = SqliteOAuthStateStore(str(tmp_path / "state.db"))
    registry = OAuthClientRegistry(store, ISSUER, RESOURCE, True)
    registry.add(
        "host",
        {
            "redirect_uris": [REDIRECT],
            "token_endpoint_auth_method": "none",
            "response_types": ["code"],
            "grant_types": ["authorization_code", "refresh_token"],
        },
    )
    registry.add(
        "browser",
        {
            "redirect_uris": [REDIRECT],
            "token_endpoint_auth_method": "client_secret_basic",
            "response_types": ["code"],
            "grant_types": ["authorization_code"],
        },
        "browser-secret",
        browser=True,
    )
    server = EmbeddedAuthorizationServer(
        config, Identity(), Consent(), store, OAuthSigningKey(str(tmp_path / "key.pem")), registry
    )
    app = FastAPI()
    server.register_routes(app)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ISSUER) as client:
        yield server, client


async def code_for(client, client_id="host", action="allow", **changes):
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "scope": "mcp:access",
        "resource": RESOURCE,
        "state": "state",
        "code_challenge": CHALLENGE,
        "code_challenge_method": "S256",
        **changes,
    }

    if client_id == "browser":
        params.update(scope="openid", nonce="nonce")
        params.pop("resource")

    response = await client.get("/oauth/authorize", params=params)
    assert response.status_code == 303

    if response.headers["location"].endswith("/login"):
        assert (await client.get("/oauth/login")).status_code == 200
        response = await client.post(
            "/oauth/login",
            data={
                "username": "alice",
                "password": "correct-password",
                "csrf": client.cookies["oauth_csrf"],
                "action": "login",
            },
            headers={"origin": ISSUER},
        )
        assert response.status_code == 303

    assert (await client.get("/oauth/consent")).status_code == 200
    response = await client.post(
        "/oauth/consent",
        data={"csrf": client.cookies["oauth_csrf"], "action": action},
        headers={"origin": ISSUER},
    )
    assert response.status_code == 303
    result = parse_qs(urlsplit(response.headers["location"]).query)
    assert result["iss"] == [ISSUER]
    assert result["state"] == ["state"]
    return result


def exchange(authorization_code, **changes):
    return {
        "client_id": "host",
        "grant_type": "authorization_code",
        "code": authorization_code,
        "code_verifier": VERIFIER,
        "redirect_uri": REDIRECT,
        "resource": RESOURCE,
        **changes,
    }


async def test_complete_pkce_refresh_reuse_logout_and_discovery(service):
    server, client = service
    discovery = (await client.get("/.well-known/openid-configuration")).json()
    assert discovery["issuer"] == ISSUER
    assert discovery["code_challenge_methods_supported"] == ["S256"]
    assert discovery["registration_endpoint"] == ISSUER + "/oauth/register"
    assert (await client.get("/oauth/jwks")).json()["keys"][0]["kid"] == server.key.kid
    code = (await code_for(client))["code"][0]
    response = await client.post("/oauth/token", data=exchange(code))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    token = response.json()
    assert (await server.verify(token["access_token"], RESOURCE)).subject == "alice"
    assert await server.verify(token["access_token"], "https://other.example/mcp") is None
    assert (await client.post("/oauth/token", data=exchange(code))).status_code == 400
    refresh = {
        "client_id": "host",
        "grant_type": "refresh_token",
        "refresh_token": token["refresh_token"],
        "resource": RESOURCE,
    }
    rotated = await client.post("/oauth/token", data=refresh)
    assert rotated.status_code == 200
    assert rotated.json()["refresh_token"] != token["refresh_token"]
    assert (await client.post("/oauth/token", data=refresh)).status_code == 400
    assert await server.verify(rotated.json()["access_token"], RESOURCE) is None
    assert (
        await client.post(
            "/oauth/token", data={**refresh, "refresh_token": rotated.json()["refresh_token"]}
        )
    ).status_code == 400
    code = (await code_for(client))["code"][0]
    token = (await client.post("/oauth/token", data=exchange(code))).json()
    await server.store.revoke_subject("bob")
    assert await server.verify(token["access_token"], RESOURCE) is not None
    await server.store.revoke_subject("alice")
    assert await server.verify(token["access_token"], RESOURCE) is None
    assert await server.store.get("login", client.cookies["oauth_login"]) is None


async def test_oidc_confidential_client_and_nonce(service):
    server, client = service
    code = (await code_for(client, "browser"))["code"][0]
    data = exchange(code, client_id="browser", resource="")
    assert (await client.post("/oauth/token", data=data)).status_code == 401
    assert (
        await client.post("/oauth/token", data=data, auth=("browser", "wrong"))
    ).status_code == 401
    token = (
        await client.post("/oauth/token", data=data, auth=("browser", "browser-secret"))
    ).json()
    claims = server.key.decode(token["id_token"], ISSUER, "browser", "JWT")
    assert claims["nonce"] == "nonce"
    assert "refresh_token" not in token
    assert await server.verify(token["id_token"], RESOURCE) is None
    assert await server.verify(token["access_token"], RESOURCE) is None


@pytest.mark.parametrize(
    "changes,error",
    [
        ({"client_id": "unknown"}, "unauthorized_client"),
        ({"redirect_uri": "https://evil.example"}, "unauthorized_client"),
        ({"response_type": "token"}, "invalid_request"),
        ({"code_challenge_method": "plain"}, "invalid_request"),
        ({"code_challenge": "short"}, "invalid_request"),
        ({"state": "a" * 2049}, "invalid_request"),
        ({"scope": "admin"}, "invalid_scope"),
        ({"scope": ""}, "invalid_scope"),
        ({"resource": "https://evil.example/mcp"}, "invalid_target"),
        (
            {"client_id": "browser", "scope": "openid", "resource": "", "nonce": ""},
            "invalid_request",
        ),
    ],
)
async def test_hostile_authorization(service, changes, error):
    _, client = service
    params = {
        "client_id": "host",
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "scope": "mcp:access",
        "resource": RESOURCE,
        "code_challenge": CHALLENGE,
        "code_challenge_method": "S256",
        **changes,
    }
    response = await client.get("/oauth/authorize", params=params)
    assert response.json()["error"] == error
    assert "location" not in response.headers


@pytest.mark.parametrize(
    "changes",
    [
        {"code_verifier": "wrong"},
        {"code_verifier": "b" * 64},
        {"redirect_uri": "https://evil.example"},
        {"resource": "https://evil.example/mcp"},
        {"scope": "admin"},
        {"client_id": "browser"},
        {"grant_type": "password"},
        {"code": "unknown"},
    ],
)
async def test_hostile_token_exchange(service, changes):
    _, client = service
    code = (await code_for(client))["code"][0]
    response = await client.post("/oauth/token", data=exchange(code, **changes))
    assert response.status_code in (400, 401)
    assert "access_token" not in response.json()


async def test_concurrent_code_and_refresh_are_single_use(service):
    server, client = service
    code = (await code_for(client))["code"][0]
    responses = await asyncio.gather(
        *(client.post("/oauth/token", data=exchange(code)) for _ in range(2))
    )
    assert sorted(r.status_code for r in responses) == [200, 400]
    token = next(r.json() for r in responses if r.status_code == 200)
    data = {
        "client_id": "host",
        "grant_type": "refresh_token",
        "resource": RESOURCE,
        "refresh_token": token["refresh_token"],
    }
    responses = await asyncio.gather(*(client.post("/oauth/token", data=data) for _ in range(2)))
    assert sorted(r.status_code for r in responses) == [200, 400]
    assert await server.verify(token["access_token"], RESOURCE) is None


async def test_login_consent_csrf_and_denial(service):
    server, client = service
    assert (await client.get("/oauth/login")).status_code == 400
    assert (await client.get("/oauth/consent")).status_code == 403
    assert (await code_for(client, action="deny"))["error"] == ["access_denied"]
    server.consent.approve = AsyncMock(return_value=False)
    assert (await code_for(client))["error"] == ["access_denied"]
    await client.get(
        "/oauth/authorize",
        params={
            "client_id": "host",
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "scope": "mcp:access",
            "resource": RESOURCE,
            "code_challenge": CHALLENGE,
            "code_challenge_method": "S256",
        },
    )
    response = await client.get("/oauth/consent")
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert (
        await client.post(
            "/oauth/consent", data={"csrf": "bad", "action": "allow"}, headers={"origin": ISSUER}
        )
    ).status_code == 403
    assert (
        await client.post(
            "/oauth/consent", content=b"x", headers={"content-type": "application/json"}
        )
    ).status_code == 400
    server.clients.pre_registered.pop("host")
    assert (await client.get("/oauth/consent")).status_code == 400


async def test_login_failures_rotation_and_rate_limits(service):
    server, client = service
    await client.get(
        "/oauth/authorize",
        params={
            "client_id": "host",
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "scope": "mcp:access",
            "resource": RESOURCE,
            "code_challenge": CHALLENGE,
            "code_challenge_method": "S256",
        },
    )
    await client.get("/oauth/login")
    form = {
        "username": "alice",
        "password": "wrong",
        "action": "login",
        "csrf": client.cookies["oauth_csrf"],
    }
    assert (
        await client.post("/oauth/login", data=form, headers={"origin": ISSUER})
    ).status_code == 403
    assert (
        await client.post("/oauth/login", data=form, headers={"origin": "https://evil.example"})
    ).status_code == 403
    assert (
        await client.post(
            "/oauth/login", content=b"bad", headers={"content-type": "application/json"}
        )
    ).status_code == 400
    client.cookies.set("oauth_login", "old", domain="game.example", path="/oauth")
    await server.store.put("login", {"subject": "alice"}, 120, "old")
    response = await client.post(
        "/oauth/login",
        data={**form, "password": "correct-password", "action": "register"},
        headers={"origin": ISSUER},
    )
    assert response.status_code == 303
    assert await server.store.get("login", "old") is None
    await client.get("/oauth/login")
    form.update(password="correct-password", csrf=client.cookies["oauth_csrf"])
    server.limits.browser = WindowOAuthRateLimitPolicy(1, 60)
    assert (
        await client.post("/oauth/login", data=form, headers={"origin": ISSUER})
    ).status_code == 303
    assert (
        await client.post("/oauth/login", data=form, headers={"origin": ISSUER})
    ).status_code == 429
    assert (await client.get("/oauth/authorize")).status_code == 429
    assert (await client.post("/oauth/consent", data=form)).status_code == 429


async def test_dcr_and_revocation(service):
    server, client = service
    registration = await client.post(
        "/oauth/register", json={"redirect_uris": [REDIRECT], "client_name": "Host"}
    )
    assert registration.status_code == 201
    registered = registration.json()
    assert (await server.clients.get(registered["client_id"]))["name"] == "Host"
    code = (await code_for(client, client_id=registered["client_id"]))["code"][0]
    token = (
        await client.post("/oauth/token", data=exchange(code, client_id=registered["client_id"]))
    ).json()
    assert (
        await client.post(
            "/oauth/revoke",
            data={"client_id": registered["client_id"], "token": token["refresh_token"]},
        )
    ).status_code == 200
    assert await server.verify(token["access_token"], RESOURCE) is None
    assert (
        await client.post("/oauth/revoke", data={"client_id": "host", "token": "garbage"})
    ).status_code == 200
    assert (
        await client.post("/oauth/revoke", data={"client_id": "missing", "token": "garbage"})
    ).status_code == 401
    assert (await client.post("/oauth/revoke", content=b"x")).status_code == 400
    code = (await code_for(client))["code"][0]
    token = (await client.post("/oauth/token", data=exchange(code))).json()
    await client.post("/oauth/revoke", data={"client_id": "host", "token": token["access_token"]})
    assert await server.verify(token["access_token"], RESOURCE) is None
    assert (await client.post("/oauth/register", json=[])).status_code == 400
    assert (
        await client.post(
            "/oauth/register",
            json={"redirect_uris": [REDIRECT], "token_endpoint_auth_method": "client_secret_post"},
        )
    ).status_code == 400
    assert (await client.post("/oauth/register", content=b"x")).status_code == 400
    server.limits.registration = WindowOAuthRateLimitPolicy(1, 60)
    await client.post("/oauth/register", json={"redirect_uris": [REDIRECT]})
    assert (
        await client.post("/oauth/register", json={"redirect_uris": [REDIRECT]})
    ).status_code == 429


async def test_real_password_accounts_and_persistence(tmp_path):
    path = str(tmp_path / "users.db")
    identity = SqlitePasswordIdentity(path, True, 1)
    assert await identity.authenticate("a", "short", True) is None
    assert await identity.authenticate("alice", "correct-password") is None
    subject = await identity.authenticate("Alice", "correct-password", True)
    assert subject
    assert await identity.authenticate("alice", "correct-password") == subject
    assert await identity.authenticate("alice", "wrong-password") is None
    assert await identity.authenticate("alice", "correct-password", True) is None
    assert await identity.authenticate("bob", "correct-password", True) is None
    assert await SqlitePasswordIdentity(path).authenticate("alice", "correct-password") == subject
    assert (
        await SqlitePasswordIdentity(str(tmp_path / "disabled.db")).authenticate(
            "alice", "correct-password", True
        )
        is None
    )
    assert os.stat(path).st_mode & 0o077 == 0


@pytest.mark.parametrize(
    "constructor,args",
    [
        (SqliteOAuthStateStore, ("",)),
        (SqliteOAuthStateStore, (":memory:",)),
        (SqliteOAuthStateStore, ("file", 0)),
        (SqlitePasswordIdentity, ("",)),
        (SqlitePasswordIdentity, (":memory:",)),
        (SqlitePasswordIdentity, ("file", False, 0)),
    ],
)
def test_invalid_durable_stores(constructor, args):
    with pytest.raises(ValueError):
        constructor(*args)


async def test_store_capacity_expiry_restart_and_binding(tmp_path):
    store = SqliteOAuthStateStore(str(tmp_path / "state.db"), 1)
    key = await store.put("code", {"subject": "alice"}, 120)
    assert await store.get("code", key) == {"subject": "alice"}
    await store.put("code", {"subject": "bob"}, 120, key)
    assert (await SqliteOAuthStateStore(store.path).get("code", key))["subject"] == "bob"
    with pytest.raises(ValueError):
        await store.put("code", {}, 120)
    assert (await store.get("code", key, True))["subject"] == "bob"
    assert await store.get("code", key, True) is None
    key = await store.put("code", {}, -1)
    assert await store.get("code", key) is None
    assert await store.rotate("missing", "client", RESOURCE, set()) is None


async def test_limits_duplicate_fields_cookies_and_clients(service):
    server, client = service
    server.clients.add(
        "client:with space",
        {
            "redirect_uris": [REDIRECT],
            "token_endpoint_auth_method": "client_secret_basic",
            "response_types": ["code"],
            "grant_types": ["authorization_code"],
        },
        "secret:+ space",
    )
    encoded = base64.b64encode(b"client%3Awith+space:secret%3A%2B+space").decode()
    authenticated = await server.authenticate_client(
        Request(
            {
                "type": "http",
                "headers": [(b"authorization", ("Basic " + encoded).encode())],
            }
        ),
        {"client_id": "client:with space"},
    )
    assert authenticated["client_id"] == "client:with space"
    assert (await client.get("/oauth/authorize?client_id=host&client_id=other")).status_code == 400
    assert (await client.get("/oauth/authorize?x=" + "a" * 8200)).status_code == 400
    assert (
        await client.post(
            "/oauth/token",
            content="client_id=host&client_id=other",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
    ).status_code == 400
    assert (
        await client.post(
            "/oauth/token",
            content="x=" + "a" * 17000,
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
    ).status_code == 400
    for headers in [
        [("cookie", "name=a; name=b")],
        [("cookie", "name=a"), ("cookie", "name=b")],
        [("cookie", "name=" + "a" * 8200)],
    ]:
        assert (
            server.cookie(
                Request(
                    {"type": "http", "headers": [(k.encode(), v.encode()) for k, v in headers]}
                ),
                "name",
            )
            == ""
        )
    assert server.address(Request({"type": "http", "headers": []})) == "unknown"
    for header in [
        "Bearer x",
        "Basic !!!",
        "Basic " + base64.b64encode(b"no-colon").decode(),
        "Basic " + base64.b64encode(b"host:password").decode(),
    ]:
        assert (
            await client.post(
                "/oauth/token", data={"client_id": "host"}, headers={"authorization": header}
            )
        ).status_code == 401
    header = "Basic " + base64.b64encode(b"browser:browser-secret").decode()
    assert (
        await client.post(
            "/oauth/token", data={"client_id": "host"}, headers={"authorization": header}
        )
    ).status_code == 401
    assert (
        await client.post("/oauth/token", data={"client_id": "host", "client_secret": "secret"})
    ).status_code == 401
    assert (
        await client.post(
            "/oauth/token",
            data={"client_id": "browser", "client_secret": "secret"},
            headers={"authorization": header},
        )
    ).status_code == 401
    assert (
        await client.post(
            "/oauth/token",
            data={"client_id": "browser"},
            headers=[("authorization", header), ("authorization", header)],
        )
    ).status_code == 401
    server.limits.token = WindowOAuthRateLimitPolicy(1, 60)
    await client.post(
        "/oauth/token", data={}, headers={"content-type": "application/x-www-form-urlencoded"}
    )
    assert (
        await client.post(
            "/oauth/token", data={}, headers={"content-type": "application/x-www-form-urlencoded"}
        )
    ).status_code == 429
    assert (await client.post("/oauth/revoke", data={})).status_code == 429


@pytest.mark.parametrize(
    "metadata",
    [
        {"redirect_uris": []},
        {"redirect_uris": [REDIRECT] * 11},
        {"redirect_uris": [REDIRECT], "token_endpoint_auth_method": "client_secret_post"},
        {"redirect_uris": [REDIRECT], "response_types": ["token"]},
        {"redirect_uris": [REDIRECT], "grant_types": ["password"]},
        {"redirect_uris": ["https://host.example/callback#fragment"]},
        {"redirect_uris": ["https://user:pass@host.example/callback"]},
        {"redirect_uris": ["http://evil.example/callback"]},
        {"redirect_uris": [REDIRECT], "client_name": "a" * 129},
    ],
)
async def test_invalid_client_metadata(service, metadata):
    server, _ = service
    with pytest.raises(ValueError):
        server.clients.validate(
            {
                "token_endpoint_auth_method": "none",
                "response_types": ["code"],
                "grant_types": ["authorization_code"],
                **metadata,
            },
            "client",
        )


async def test_disabled_registration_and_collisions(service):
    server, client = service
    server.clients.dcr_enabled = False
    assert (
        "registration_endpoint"
        not in (await client.get("/.well-known/oauth-authorization-server")).json()
    )
    with pytest.raises(ValueError):
        await server.clients.register({"redirect_uris": [REDIRECT]})
    app = FastAPI()
    server.register_routes(app)
    assert "/oauth/register" not in [route.path for route in app.routes]
    with pytest.raises(GatewayConfigurationError):
        server.register_routes(app)
    server.settings.admin_enabled = True
    server.settings.admin_path = "/oauth/token/stats"
    with pytest.raises(GatewayConfigurationError):
        server.register_routes(FastAPI())
    with pytest.raises(ValueError):
        server.clients.add("host", {})


async def test_storage_exhaustion_is_safe(service):
    server, client = service
    put = server.store.put
    server.store.put = AsyncMock(side_effect=ValueError("capacity"))
    params = {
        "client_id": "host",
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "scope": "mcp:access",
        "resource": RESOURCE,
        "code_challenge": CHALLENGE,
        "code_challenge_method": "S256",
    }
    assert (await client.get("/oauth/authorize", params=params)).status_code == 503
    server.store.put = put
    await client.get("/oauth/authorize", params=params)
    await client.get("/oauth/login")
    form = {
        "username": "alice",
        "password": "correct-password",
        "csrf": client.cookies["oauth_csrf"],
        "action": "login",
    }
    server.store.put = AsyncMock(side_effect=ValueError("capacity"))
    assert (
        await client.post("/oauth/login", data=form, headers={"origin": ISSUER})
    ).status_code == 503
    server.store.put = put
    await client.post("/oauth/login", data=form, headers={"origin": ISSUER})
    await client.get("/oauth/consent")
    server.store.put = AsyncMock(side_effect=ValueError("capacity"))
    response = await client.post(
        "/oauth/consent",
        data={"csrf": client.cookies["oauth_csrf"], "action": "allow"},
        headers={"origin": ISSUER},
    )
    assert response.status_code == 503


async def test_missing_transaction_after_consent_and_revoked_grant(service):
    server, client = service
    code = (await code_for(client))["code"][0]
    server.consent.validate = AsyncMock(return_value=False)
    assert (await client.post("/oauth/token", data=exchange(code))).status_code == 400
    server.consent.validate = AsyncMock(return_value=True)
    await code_for(client)
    params = {
        "client_id": "host",
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "scope": "mcp:access",
        "resource": RESOURCE,
        "code_challenge": CHALLENGE,
        "code_challenge_method": "S256",
    }
    await client.get("/oauth/authorize", params=params)
    await client.get("/oauth/consent")
    original = server.store.get

    async def get(kind, key, consume=False):
        return None if kind == "transaction" and consume else await original(kind, key, consume)

    server.store.get = get
    assert (
        await client.post(
            "/oauth/consent",
            data={"csrf": client.cookies["oauth_csrf"], "action": "allow"},
            headers={"origin": ISSUER},
        )
    ).status_code == 400


async def test_refresh_bindings_and_capacity(service):
    server, client = service
    code = (await code_for(client))["code"][0]
    token = (await client.post("/oauth/token", data=exchange(code, scope="mcp:access"))).json()
    refresh = token["refresh_token"]
    assert await server.store.rotate(refresh, "other", RESOURCE, {"mcp:access"}) is None
    assert await server.store.rotate(refresh, "host", "other", {"mcp:access"}) is None
    assert await server.store.rotate(refresh, "host", RESOURCE, {"admin"}) is None
    server.store.maximum_records = 1
    with pytest.raises(ValueError):
        await server.store.rotate(refresh, "host", RESOURCE, {"mcp:access"})
    server.store.maximum_records = 50000
    assert (
        await client.post(
            "/oauth/token",
            data={
                "client_id": "host",
                "grant_type": "refresh_token",
                "resource": RESOURCE,
                "refresh_token": "missing",
            },
        )
    ).status_code == 400
    assert (
        await client.post(
            "/oauth/token",
            data={
                "client_id": "host",
                "grant_type": "refresh_token",
                "resource": RESOURCE,
                "refresh_token": refresh,
                "scope": "mcp:access",
            },
        )
    ).status_code == 200
    assert await server.verify("garbage", RESOURCE) is None
    now = int(time.time())
    for changes in [{"client_id": ""}, {"family": None}, {"family": "missing"}]:
        encoded = server.key.encode(
            {
                "iss": ISSUER,
                "sub": "alice",
                "aud": RESOURCE,
                "client_id": "host",
                "scope": "mcp:access",
                "iat": now,
                "exp": now + 120,
                **changes,
            }
        )
        assert await server.verify(encoded, RESOURCE) is None


def test_signing_key_persistence_permissions_type_and_creation_race(tmp_path, monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    path = tmp_path / "key.pem"
    key = OAuthSigningKey(str(path))
    assert OAuthSigningKey(str(path)).kid == key.kid
    path.chmod(0o644)
    with pytest.raises(ValueError):
        OAuthSigningKey(str(path))
    path.chmod(0o600)
    other = ec.generate_private_key(ec.SECP256R1())
    path.write_bytes(
        other.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    with pytest.raises(ValueError):
        OAuthSigningKey(str(path))
    path.unlink()
    existing = tmp_path / "existing.pem"
    OAuthSigningKey(str(existing))
    real_open = os.open

    def create_race(target, flags, mode):
        path.write_bytes(existing.read_bytes())
        path.chmod(0o600)
        raise FileExistsError

    monkeypatch.setattr(os, "open", create_race)
    assert OAuthSigningKey(str(path)).kid
    monkeypatch.setattr(os, "open", real_open)


@pytest.mark.parametrize(
    "changes",
    [
        {"oauth_jwt_allowed_algorithms": ["ES256"]},
        {"oauth_embedded_issuer": "https://other.example"},
        {
            "oauth_embedded_issuer": ISSUER + "/oauth/",
            "oauth_authorization_servers": [ISSUER + "/oauth/"],
            "oauth_jwks_url": ISSUER + "/oauth//oauth/jwks",
        },
        {"oauth_jwks_url": ISSUER + "/other"},
        {"oauth_token_verifier": "custom"},
        {
            "oauth_embedded_access_token_ttl_seconds": 100,
            "oauth_embedded_refresh_token_ttl_seconds": 99,
        },
    ],
)
def test_embedded_config(changes):
    with pytest.raises(ValidationError):
        settings(**changes)


async def test_gateway_requires_matching_embedded_dependencies(service):
    server, _ = service
    policy = DenyUnlessGranted(MemoryChannelGrantStore())
    gateway = Gateway(settings(), authorization_server=server, channel_access=policy)
    assert gateway.create_app()
    for extra in [{"access_token_verifier": AsyncMock()}, {"mcp_access_controller": AsyncMock()}]:
        with pytest.raises(GatewayConfigurationError):
            Gateway(settings(), authorization_server=server, channel_access=policy, **extra)
    with pytest.raises(GatewayConfigurationError):
        Gateway(
            settings(oauth_mode="resource_server"),
            authorization_server=server,
            channel_access=policy,
        )


async def test_cimd_authorization_and_metadata_budget(service):
    server, client = service
    client_id = "https://client.example/metadata.json"
    server.clients.metadata_resolver = AsyncMock()
    server.clients.metadata_resolver.resolve.return_value = {
        "client_id": client_id,
        "redirect_uris": [REDIRECT],
        "client_name": "CIMD client",
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
    }
    metadata = (await client.get("/.well-known/oauth-authorization-server")).json()
    assert metadata["client_id_metadata_document_supported"] is True
    server.settings.oauth_supported_scopes.append("game:write")
    code = await code_for(client, client_id=client_id, scope="mcp:access game:write")
    response = await client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "code": code["code"][0],
            "code_verifier": VERIFIER,
            "resource": RESOURCE,
        },
    )
    assert response.status_code == 200
    assert response.json()["scope"] == "game:write mcp:access"
    assert (await server.verify(response.json()["access_token"], RESOURCE)).client_id == client_id
    server.clients.metadata_resolver.resolve.assert_awaited_once_with(client_id)
    server.limits.metadata = WindowOAuthRateLimitPolicy(1, 60)
    assert (await client.get("/.well-known/oauth-authorization-server")).status_code == 200
    assert (await client.get("/.well-known/openid-configuration")).status_code == 429
    assert (await client.get("/oauth/jwks")).status_code == 429
    config = settings(oauth_embedded_cimd_enabled=False)
    registry = OAuthClientRegistry(server.store, ISSUER, RESOURCE)
    other = EmbeddedAuthorizationServer(
        config, server.identity, server.consent, server.store, server.key, registry
    )
    assert other.clients.metadata_resolver is None
    other.limits.metadata = WindowOAuthRateLimitPolicy(1, 60)
    assert (await other.jwks(Request({"type": "http", "headers": []}))).status_code == 200
    with pytest.raises(OAuthRateLimitError):
        await other.jwks(Request({"type": "http", "headers": []}))


def test_cimd_origin_and_supported_scope_configuration():
    assert settings(
        oauth_embedded_cimd_allowed_origins="https://client.example,https://another.example"
    ).oauth_embedded_cimd_allowed_origins == ["https://client.example", "https://another.example"]
    assert settings(oauth_supported_scopes="mcp:access,game:write").oauth_supported_scopes == [
        "mcp:access",
        "game:write",
    ]
    for options in [
        {"oauth_embedded_cimd_allowed_origins": ["https://client.example/path"]},
        {"oauth_embedded_cimd_allowed_origins": ["http://client.example"]},
        {"oauth_supported_scopes": ["game:write"]},
        {"oauth_supported_scopes": ["mcp:access", "bad scope"]},
    ]:
        with pytest.raises(ValidationError):
            settings(**options)


@pytest.mark.parametrize("nonce", ["", "host-nonce"])
async def test_public_host_openid_scope_and_client_assertion_rejection(service, nonce):
    server, client = service
    code = (await code_for(client, scope="openid mcp:access", nonce=nonce))["code"][0]
    response = await client.post("/oauth/token", data=exchange(code))
    assert response.status_code == 200
    tokens = response.json()
    identity = server.key.decode(tokens["id_token"], ISSUER, "host", "JWT")
    assert identity["sub"] == "alice"
    assert identity.get("nonce", "") == nonce
    assert (await server.verify(tokens["access_token"], RESOURCE)).scopes == frozenset(
        {"openid", "mcp:access"}
    )
    assert await server.verify(tokens["id_token"], RESOURCE) is None
    for field in ["client_assertion", "client_assertion_type"]:
        assert (
            await client.post("/oauth/token", data={"client_id": "host", field: "unsupported"})
        ).status_code == 401


async def test_dcr_standard_informational_metadata_without_fetch(service):
    server, client = service
    response = await client.post(
        "/oauth/register",
        json={
            "redirect_uris": [REDIRECT],
            "client_name": "MCP host",
            "client_uri": "https://client.example/",
            "logo_uri": "http://169.254.169.254/logo",
            "contacts": ["owner@example.com"],
            "scope": "openid mcp:access",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["client_uri"] == "https://client.example/"
    assert data["logo_uri"] == "http://169.254.169.254/logo"
    assert data["contacts"] == ["owner@example.com"]
    assert (await server.clients.get(data["client_id"]))["information"]["logo_uri"] == data[
        "logo_uri"
    ]
    assert (
        await client.post("/oauth/register", json={"redirect_uris": [REDIRECT], "scope": 123})
    ).status_code == 400


async def test_unicode_csrf_and_basic_credentials_fail_closed(service):
    _, client = service
    await client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": "host",
            "redirect_uri": REDIRECT,
            "resource": RESOURCE,
            "scope": "mcp:access",
            "code_challenge": CHALLENGE,
            "code_challenge_method": "S256",
        },
    )
    await client.get("/oauth/login")
    assert (
        await client.post(
            "/oauth/login", data={"csrf": "é", "action": "login"}, headers={"origin": ISSUER}
        )
    ).status_code == 403
    assert (
        await client.post(
            "/oauth/login",
            data={
                "username": "alice",
                "password": "correct-password",
                "csrf": client.cookies["oauth_csrf"],
                "action": "login",
            },
            headers={"origin": ISSUER},
        )
    ).status_code == 303
    await client.get("/oauth/consent")
    assert (
        await client.post(
            "/oauth/consent", data={"csrf": "é", "action": "allow"}, headers={"origin": ISSUER}
        )
    ).status_code == 403
    header = "Basic " + base64.b64encode(b"browser:%ff").decode()
    assert (
        await client.post(
            "/oauth/token", data={"client_id": "browser"}, headers={"authorization": header}
        )
    ).status_code == 401


async def test_sec16_17_19_seeded_malformed_input_fuzz_fails_closed(service):
    server, client = service
    server.clients.metadata_resolver = None
    server.limits.token = WindowOAuthRateLimitPolicy(2000, 60)
    server.limits.registration = WindowOAuthRateLimitPolicy(2000, 60)
    server.limits.browser = WindowOAuthRateLimitPolicy(2000, 60)
    server.identity.authenticate = AsyncMock(return_value=None)
    server.consent.approve = AsyncMock(return_value=False)
    mutations = random.Random(20261009)

    for _ in range(256):
        raw = mutations.randbytes(mutations.randrange(0, 1024))
        credential = base64.urlsafe_b64encode(raw).decode()
        assert await server.verify(credential, RESOURCE) is None
        response = await client.post(
            "/oauth/token",
            content=raw,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert response.status_code in (400, 401)
        response = await client.post(
            "/oauth/register", content=raw, headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 400
        response = await client.get(
            "/oauth/authorize", params={"client_id": "invalid-" + credential}
        )
        assert response.status_code == 400

    server.identity.authenticate.assert_not_called()
    server.consent.approve.assert_not_called()


async def test_sec16_account_budget_prevents_rotating_address_password_work(service):
    server, client = service
    await client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": "host",
            "redirect_uri": REDIRECT,
            "resource": RESOURCE,
            "scope": "mcp:access",
            "code_challenge": CHALLENGE,
            "code_challenge_method": "S256",
        },
    )
    await client.get("/oauth/login")
    server.limits.login = WindowOAuthRateLimitPolicy(1, 60)
    server.identity.authenticate = AsyncMock(return_value=None)
    form = {
        "csrf": client.cookies["oauth_csrf"],
        "username": "ALICE",
        "password": "wrong-password",
        "action": "login",
    }
    assert (
        await client.post("/oauth/login", data=form, headers={"origin": ISSUER})
    ).status_code == 403
    form["username"] = "alice"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=client._transport.app, client=("203.0.113.1", 123)),
        base_url=ISSUER,
        cookies=client.cookies,
    ) as other:
        response = await other.post("/oauth/login", data=form, headers={"origin": ISSUER})

    assert response.status_code == 429
    assert int(response.headers["retry-after"]) >= 1
    server.identity.authenticate.assert_awaited_once()


async def test_sec16_client_and_principal_budgets_do_not_consume_codes(service):
    server, client = service
    codes = [(await code_for(client))["code"][0] for _ in range(2)]
    server.limits.principal = WindowOAuthRateLimitPolicy(1, 60)
    first = await client.post("/oauth/token", data=exchange(codes[0]))
    assert first.status_code == 200
    second = await client.post("/oauth/token", data=exchange(codes[1]))
    assert second.status_code == 429
    assert await server.store.get("code", codes[1]) is not None
    server.limits.principal = WindowOAuthRateLimitPolicy(120)
    server.limits.client = WindowOAuthRateLimitPolicy(1, 60)
    assert (
        await client.post("/oauth/revoke", data={"client_id": "host", "token": "absent"})
    ).status_code == 200
    assert (await client.post("/oauth/token", data=exchange(codes[1]))).status_code == 429
    assert await server.store.get("code", codes[1]) is not None


async def test_limits_injection_and_class_override(service):
    from mcpgtw.oauth.endpoint_limits import OAuthEndpointLimits

    server, _ = service

    class CustomLimits(OAuthEndpointLimits):
        pass

    class CustomServer(EmbeddedAuthorizationServer):
        limits_class = CustomLimits

    other = CustomServer(
        server.settings, server.identity, server.consent, server.store, server.key, server.clients
    )
    assert isinstance(other.limits, CustomLimits)
    injected = CustomLimits(server.settings)
    other = EmbeddedAuthorizationServer(
        server.settings,
        server.identity,
        server.consent,
        server.store,
        server.key,
        server.clients,
        limits=injected,
    )
    assert other.limits is injected


async def test_prefixed_issuer_discovery_cookies_pkce_and_exact_origin(service):
    server, _ = service
    issuer = ISSUER + "/identity"
    config = settings(
        oauth_embedded_issuer=issuer,
        oauth_authorization_servers=[issuer],
        oauth_jwks_url=issuer + "/oauth/jwks",
    )
    other = EmbeddedAuthorizationServer(
        config, server.identity, server.consent, server.store, server.key, server.clients
    )
    app = FastAPI()
    other.register_routes(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ISSUER) as client:
        metadata = (await client.get("/.well-known/oauth-authorization-server/identity")).json()
        assert metadata["issuer"] == issuer
        assert metadata["authorization_endpoint"] == issuer + "/oauth/authorize"
        assert (await client.get("/identity/.well-known/openid-configuration")).json() == metadata
        response = await client.get(
            "/identity/oauth/authorize",
            params={
                "response_type": "code",
                "client_id": "host",
                "redirect_uri": REDIRECT,
                "resource": RESOURCE,
                "scope": "mcp:access",
                "state": "prefixed",
                "code_challenge": CHALLENGE,
                "code_challenge_method": "S256",
            },
        )
        assert response.headers["location"] == issuer + "/oauth/login"
        assert "Path=/identity/oauth" in response.headers["set-cookie"]
        await client.get("/identity/oauth/login")
        response = await client.post(
            "/identity/oauth/login",
            data={
                "username": "alice",
                "password": "correct-password",
                "action": "login",
                "csrf": client.cookies["oauth_csrf"],
            },
            headers={"Origin": ISSUER},
        )
        assert response.status_code == 303
        await client.get("/identity/oauth/consent")
        response = await client.post(
            "/identity/oauth/consent",
            data={"action": "allow", "csrf": client.cookies["oauth_csrf"]},
            headers={"Origin": ISSUER},
        )
        query = parse_qs(urlsplit(response.headers["location"]).query)
        assert query["iss"] == [issuer]
        token = await client.post("/identity/oauth/token", data=exchange(query["code"][0]))
        assert token.status_code == 200
        principal = await other.verify(token.json()["access_token"], RESOURCE)
        assert principal is not None and principal.issuer == issuer
        response = await client.get(
            metadata["authorization_endpoint"],
            params={
                "response_type": "code",
                "client_id": "host",
                "redirect_uri": REDIRECT,
                "resource": RESOURCE,
                "scope": "mcp:access",
                "state": "already-signed-in",
                "code_challenge": CHALLENGE,
                "code_challenge_method": "S256",
            },
        )
        assert response.headers["location"] == issuer + "/oauth/consent"
        consent = await client.get(response.headers["location"])
        assert consent.status_code == 200
        assert "Client ID: host" in consent.text
        assert "Resource: " + RESOURCE in consent.text
        assert (await client.get("/oauth/login")).status_code == 404


async def test_sec16_password_worker_saturation_rejects_without_unbounded_wait(tmp_path):
    import threading

    identity = SqlitePasswordIdentity(str(tmp_path / "identity.db"))
    started = threading.Event()
    release = threading.Event()

    def slow(*args):
        started.set()
        assert release.wait(timeout=5)
        return None

    identity._authenticate = slow
    workers = [
        asyncio.create_task(identity.authenticate("alice", "correct-password")) for _ in range(2)
    ]
    try:
        assert await asyncio.to_thread(started.wait, 2)
        await asyncio.sleep(0)
        with pytest.raises(OAuthRateLimitError):
            await identity.authenticate("alice", "correct-password")
        assert not identity._workers._waiters
    finally:
        release.set()
        await asyncio.gather(*workers)


async def test_confidential_basic_scheme_is_case_insensitive(service):
    _, client = service
    code = (await code_for(client, client_id="browser"))["code"][0]
    basic = base64.b64encode(b"browser:browser-secret").decode()
    response = await client.post(
        "/oauth/token",
        data=exchange(code, client_id="browser", resource=""),
        headers={"Authorization": "basic " + basic},
    )
    assert response.status_code == 200
    assert "id_token" in response.json()


@pytest.mark.parametrize("backend", ["state", "identity", "grant"])
@pytest.mark.parametrize("failure", ["sqlite", "filesystem"])
async def test_sec20_storage_faults_are_typed_fail_closed(tmp_path, monkeypatch, backend, failure):
    import sqlite3

    from mcpgtw.oauth.access_error import McpAccessError
    from mcpgtw.oauth.sqlite_grants import SqliteChannelGrantStore
    from mcpgtw.oauth.verified_principal import VerifiedPrincipal

    def fail(*args, **kwargs):
        raise (
            sqlite3.OperationalError("locked")
            if failure == "sqlite"
            else PermissionError("private")
        )

    monkeypatch.setattr(sqlite3, "connect", fail)
    path = str(tmp_path / "unavailable.db")
    with pytest.raises(McpAccessError) as denied:
        if backend == "state":
            await SqliteOAuthStateStore(path).get("family", "id")
        elif backend == "identity":
            await SqlitePasswordIdentity(path).authenticate("alice", "correct-password")
        else:
            principal = VerifiedPrincipal(
                ISSUER, "alice", "host", frozenset(), int(time.time()) + 10
            )
            await SqliteChannelGrantStore(path).channels(principal)

    assert denied.value.reason == "verifier_unavailable" and denied.value.status_code == 503


async def test_sec20_unavailable_embedded_state_returns_neutral_503(service):
    from mcpgtw.oauth.access_error import McpAccessError

    server, client = service
    server.store.get = AsyncMock(side_effect=McpAccessError("verifier_unavailable"))
    response = await client.get("/oauth/login")
    assert response.status_code == 503
    assert response.json() == {"error": "temporarily_unavailable"}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["retry-after"] == "1"
