from __future__ import annotations

import json
import time
from dataclasses import FrozenInstanceError, replace
from unittest.mock import AsyncMock

import httpx
import httpx2
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import ValidationError
from starlette.requests import Request

from mcpgtw.config import GatewaySettings, validate_oauth_url
from mcpgtw.errors import GatewayConfigurationError
from mcpgtw.gateway import Gateway
from mcpgtw.oauth.access_context import McpAccessContext
from mcpgtw.oauth.access_error import McpAccessError
from mcpgtw.oauth.channel_access import DenyUnlessGranted
from mcpgtw.oauth.channel_grants import MemoryChannelGrantStore
from mcpgtw.oauth.claims import principal_from_claims
from mcpgtw.oauth.credentials import bearer_credential
from mcpgtw.oauth.http_fetch import bounded_json
from mcpgtw.oauth.introspection_verifier import IntrospectionAccessTokenVerifier
from mcpgtw.oauth.jwt_verifier import JwtAccessTokenVerifier
from mcpgtw.oauth.session_binding import MemoryMcpSessionBindingStore
from mcpgtw.oauth.sqlite_grants import SqliteChannelGrantStore
from mcpgtw.oauth.verified_principal import VerifiedPrincipal

RESOURCE = "https://game.example/mcp"
ISSUER = "https://auth.example"


def settings(**kwargs):
    return GatewaySettings(
        **{
            "oauth_mode": "resource_server",
            "oauth_resource_url": RESOURCE,
            "oauth_authorization_servers": [ISSUER],
            "oauth_token_verifier": "custom",
            **kwargs,
        }
    )


def principal(subject="alice", **kwargs):
    return VerifiedPrincipal(
        ISSUER, subject, "host", frozenset({"mcp:access"}), int(time.time()) + 900, **kwargs
    )


def request(headers=(), path="/", method="POST"):
    return Request(
        {
            "type": "http",
            "path": path,
            "root_path": "",
            "method": method,
            "headers": list(headers),
            "query_string": b"",
        }
    )


def claims(**kwargs):
    return {
        "iss": ISSUER,
        "sub": "alice",
        "client_id": "host",
        "aud": RESOURCE,
        "exp": int(time.time()) + 900,
        "iat": int(time.time()),
        "scope": "mcp:access",
        **kwargs,
    }


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "//example.com",
        "https://a#x",
        "https://user:secret@a",
        "https://a?x",
        "https://*.a",
        "https://a/\n",
        'https://a/"',
        "https://a/\\",
        "https://é",
        "https://a:invalid",
        "https://a:65536",
    ],
)
def test_sec18_canonical_url_validation(url):
    with pytest.raises(ValueError):
        validate_oauth_url(url)


def test_config_and_context():
    assert GatewaySettings().oauth_mode == "off"
    assert settings().oauth_mode == "resource_server"
    validate_oauth_url("http://localhost:8000/mcp", True)
    assert settings(oauth_required_scopes="mcp:access,tools:read").oauth_required_scopes == [
        "mcp:access",
        "tools:read",
    ]
    p = principal()
    assert p.principal_id != replace(p, issuer="https://other").principal_id
    assert p.principal_id != replace(p, subject="bob").principal_id
    assert p.principal_id != replace(p, client_id="other").principal_id
    context = McpAccessContext(
        "c", "oauth", p.principal_id, p.client_id, p.scopes, p.issuer, p.expires_at
    )

    with pytest.raises(FrozenInstanceError):
        context.channel_id = "changed"

    for reason in [
        "missing_token",
        "invalid_token",
        "insufficient_scope",
        "channel_forbidden",
        "verifier_unavailable",
        "invalid_session",
    ]:
        assert McpAccessError(reason).status_code in {401, 403, 404, 503}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"oauth_mode": "wrong"},
        {"oauth_resource_url": ""},
        {"oauth_authorization_servers": []},
        {"oauth_resource_url": "https://a/tools"},
        {"oauth_http_timeout_seconds": 0},
        {"oauth_http_timeout_seconds": float("inf")},
        {"oauth_max_token_bytes": ""},
        {"oauth_required_scopes": []},
        {"oauth_required_scopes": ["bad scope"]},
        {"oauth_required_scopes": ['bad"scope']},
        {"oauth_token_verifier": "jwt"},
        {
            "oauth_token_verifier": "jwt",
            "oauth_jwks_url": "https://a/jwks",
            "oauth_jwt_allowed_algorithms": ["HS256"],
        },
        {
            "oauth_token_verifier": "jwt",
            "oauth_jwks_url": "https://a/jwks",
            "oauth_jwt_allowed_algorithms": [],
        },
        {"oauth_token_verifier": "introspection"},
    ],
)
def test_invalid_configuration(kwargs):
    values = settings().model_dump() | kwargs

    with pytest.raises(ValidationError):
        GatewaySettings(**values)


def test_valid_verifier_settings_and_secret():
    settings(oauth_token_verifier="jwt", oauth_jwks_url="https://auth.example/jwks")
    s = settings(
        oauth_token_verifier="introspection",
        oauth_introspection_url="https://auth.example/introspect",
        oauth_introspection_client_id="rs",
        oauth_introspection_client_secret="secret",
    )
    assert "secret" not in repr(s)
    assert s.oauth_introspection_client_secret.get_secret_value() == "secret"


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [(b"authorization", b"Bearer ")],
        [(b"authorization", b"Bearer token"), (b"authorization", b"Bearer second")],
        [(b"authorization", b"Bearer x\r\n")],
        [(b"authorization", b"Basic token")],
        [(b"authorization", b"Bearer " + b"x" * 20)],
    ],
)
def test_sec17_credentials(headers):
    with pytest.raises(McpAccessError):
        bearer_credential(request(headers), 10)


def test_case_insensitive_bearer():
    assert bearer_credential(request([(b"authorization", b"bearer abc")]), 10) == "abc"


@pytest.mark.parametrize(
    "changes",
    [
        {"iss": "wrong"},
        {"aud": "wrong"},
        {"aud": None},
        {"aud": [RESOURCE, 42]},
        {"aud": [RESOURCE, ""]},
        {"azp": "another-client"},
        {"scope": "mcp:access\nadmin"},
        {"scope": "mcp:access café"},
        {"scope": 'mcp:access "admin'},
        {"exp": True},
        {"exp": 0},
        {"sub": None},
        {"sub": ""},
        {"client_id": ""},
        {"client_id": 12},
        {"scope": []},
        {"token_use": "id"},
        {"nbf": "bad"},
        {"iat": int(time.time()) + 500},
    ],
)
def test_sec02_claims(changes):
    assert principal_from_claims(claims(**changes), ISSUER, RESOURCE) is None


def test_valid_claims():
    assert principal_from_claims(claims(aud=[RESOURCE], nbf=int(time.time())), ISSUER, RESOURCE)
    assert principal_from_claims(claims(client_id=None, azp="host"), ISSUER, RESOURCE)


async def test_grants_capacity_revocation_and_ambiguous_channel():
    store = MemoryChannelGrantStore(2)
    p = principal()
    policy = DenyUnlessGranted(store)
    assert await policy.resolve(p, "") is None
    await store.grant(p, "one")
    await store.grant(p, "one")
    assert await policy.resolve(p, "") == "one"
    assert await policy.resolve(p, "other") is None
    assert await policy.resolve(p, "one") == "one"
    await store.grant(p, "two")
    assert await policy.resolve(p, "") is None

    with pytest.raises(ValueError, match="capacity"):
        await store.grant(p, "three")

    await store.revoke(p, "unknown")
    await store.remove_channel("unknown")
    await store.revoke(p, "one")
    await store.remove_channel("two")
    assert not await store.channels(p)

    with pytest.raises(ValueError):
        MemoryChannelGrantStore(0)


def test_sec06_session_binding(monkeypatch):
    context = McpAccessContext("c", "oauth", "alice", "host", frozenset(), ISSUER, 200)
    store = MemoryMcpSessionBindingStore(1, 10)
    assert not store.accepts("unknown", context)
    assert store.bind("one", context)
    assert store.bind("one", context)
    assert not store.bind("two", context)
    assert not store.bind("one", replace(context, principal_id="bob"))
    assert not store.accepts("one", replace(context, client_id="other"))
    assert store.accepts("one", replace(context, expires_at=1000, scopes=frozenset({"new"})))
    store.remove_channel("other")
    assert store.accepts("one", context)
    store.remove_channel("c")
    assert not store.accepts("one", context)
    assert store.bind("one", context)
    monkeypatch.setattr("mcpgtw.oauth.session_binding.time.monotonic", lambda: float("inf"))
    assert not store.accepts("one", context)
    assert store.bind("two", context)
    store.remove("one")
    store.clear()

    with pytest.raises(ValueError):
        MemoryMcpSessionBindingStore(0)

    with pytest.raises(ValueError):
        MemoryMcpSessionBindingStore(idle_seconds=0)


async def test_sec04_discovery_hybrid_and_authorization():
    grants = MemoryChannelGrantStore()
    verifier = AsyncMock()
    verifier.verify.return_value = principal()
    gateway = Gateway(
        settings(oauth_allow_static_mcp_tokens=True),
        access_token_verifier=verifier,
        channel_access=DenyUnlessGranted(grants),
        static_channel_eligible=lambda channel: channel.metadata.get("auth") == "token",
    )
    token_channel = await gateway.create_channel(metadata={"auth": "token"})
    oauth_channel = await gateway.create_channel()
    await grants.grant(principal(), oauth_channel.channel_id)

    async def handle(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    gateway.manager.handle_request = AsyncMock(side_effect=handle)
    app = gateway.create_app()

    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app), base_url="https://game.example", follow_redirects=True
    ) as client:
        for path in [
            "/.well-known/oauth-protected-resource/mcp",
            "/.well-known/oauth-protected-resource",
        ]:
            response = await client.get(path, headers={"Host": "evil.example"})
            assert response.json()["resource"] == RESOURCE

        response = await client.post("/mcp")
        assert response.status_code == 401
        assert (
            RESOURCE.replace("/mcp", "/.well-known/oauth-protected-resource/mcp")
            in response.headers["www-authenticate"]
        )
        assert (
            await client.post("/mcp", headers={"Authorization": "Bearer oauth-token"})
        ).status_code == 200
        assert (
            await client.post(
                "/mcp", headers={"Authorization": "Bearer " + token_channel.mcp_token}
            )
        ).status_code == 200
        assert (
            await client.post(
                "/mcp", headers={"Authorization": "Bearer " + oauth_channel.mcp_token}
            )
        ).status_code == 401
        assert (
            await client.post(
                "/mcp/" + token_channel.channel_id, headers={"Authorization": "Bearer oauth-token"}
            )
        ).status_code == 404
        verifier.verify.return_value = replace(principal(), scopes=frozenset())
        assert (
            await client.post("/mcp", headers={"Authorization": "Bearer oauth-token"})
        ).status_code == 403
        verifier.verify.return_value = replace(principal(), expires_at=0)
        assert (
            await client.post("/mcp", headers={"Authorization": "Bearer oauth-token"})
        ).status_code == 401
        verifier.verify.return_value = None
        assert (
            await client.post("/mcp", headers={"Authorization": "Bearer oauth-token"})
        ).status_code == 401
        verifier.verify.side_effect = McpAccessError("verifier_unavailable")
        assert (
            await client.post("/mcp", headers={"Authorization": "Bearer oauth-token"})
        ).status_code == 503


def test_missing_dependencies_and_embedded_blocked():
    with pytest.raises(GatewayConfigurationError, match="channel access"):
        Gateway(settings())

    with pytest.raises(GatewayConfigurationError, match="injected"):
        Gateway(settings(), channel_access=DenyUnlessGranted(MemoryChannelGrantStore()))

    with pytest.raises(GatewayConfigurationError, match="injected authorization"):
        Gateway(
            settings(
                oauth_mode="embedded",
                oauth_token_verifier="jwt",
                oauth_jwks_url="https://auth.example/oauth/jwks",
            )
        )

    with pytest.raises(GatewayConfigurationError, match="eligibility"):
        Gateway(
            settings(oauth_allow_static_mcp_tokens=True),
            access_token_verifier=AsyncMock(),
            channel_access=DenyUnlessGranted(MemoryChannelGrantStore()),
        )

    policy = DenyUnlessGranted(MemoryChannelGrantStore())
    Gateway(
        settings(oauth_token_verifier="jwt", oauth_jwks_url="https://a/jwks"), channel_access=policy
    )
    Gateway(
        settings(
            oauth_token_verifier="introspection",
            oauth_introspection_url="https://a/check",
            oauth_introspection_client_id="rs",
            oauth_introspection_client_secret="secret",
        ),
        channel_access=policy,
    )
    gateway = Gateway(
        settings(
            admin_enabled=True, admin_key="key", admin_path="/.well-known/oauth-protected-resource"
        ),
        mcp_access_controller=AsyncMock(),
    )

    with pytest.raises(GatewayConfigurationError, match="collides"):
        gateway.create_app()


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500),
        httpx.Response(200, json=[]),
        httpx.Response(200, content=b"x" * 100),
        httpx.Response(200, content=b"{"),
        httpx.Response(200, headers={"content-encoding": "gzip"}, stream=httpx.ByteStream(b"")),
    ],
)
async def test_sec19_bounded_json_errors(response):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: response)) as client:
        with pytest.raises(McpAccessError, match="unavailable"):
            await bounded_json(client, "GET", "https://auth.example/data", 10)


async def test_introspection():
    s = settings(
        oauth_token_verifier="introspection",
        oauth_introspection_url=ISSUER + "/check",
        oauth_introspection_client_id="rs",
        oauth_introspection_client_secret="secret",
    )
    outputs = [{"active": False}, claims(active=True), claims(active=True, aud="other")]

    def handler(req):
        assert req.headers["authorization"].startswith("Basic ")
        assert b"token=opaque" in req.content
        return httpx.Response(200, json=outputs.pop(0))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        verifier = IntrospectionAccessTokenVerifier(s, client)
        assert await verifier.verify("opaque", RESOURCE) is None
        assert await verifier.verify("opaque", RESOURCE) is not None
        assert await verifier.verify("opaque", RESOURCE) is None


async def test_sec01_jwt_signatures_rollover_and_header_attacks(monkeypatch):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key())) | {
        "kid": "one",
        "alg": "RS256",
    }
    s = settings(oauth_token_verifier="jwt", oauth_jwks_url=ISSUER + "/keys")
    payloads = [{"keys": [public]}]

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payloads[0]))
    ) as client:
        verifier = JwtAccessTokenVerifier(s, client)

        def token(**headers):
            return jwt.encode(
                claims(),
                private,
                algorithm="RS256",
                headers={"kid": "one", "typ": "at+jwt", **headers},
            )

        valid = token()
        assert await verifier.verify(valid, RESOURCE)
        assert await verifier.verify(valid, RESOURCE)
        assert await verifier.verify(token(kid="unknown"), RESOURCE) is None
        assert await verifier.verify(token(typ="JWT"), RESOURCE) is None
        assert await verifier.verify(token(jku="http://localhost"), RESOURCE) is None
        assert await verifier.verify(token(x5u="http://localhost"), RESOURCE) is None
        assert await verifier.verify(token(crit=["unexpected"]), RESOURCE) is None
        assert await verifier.verify(token(crit=[]), RESOURCE) is None
        assert await verifier.verify("invalid", RESOURCE) is None
        nested = b'{"a":' + b"[" * 1100 + b"0" + b"]" * 1100 + b"}"
        nested_token = jwt.utils.base64url_encode(nested).decode() + ".e30.c2ln"
        assert len(nested_token) < s.oauth_max_token_bytes
        assert await verifier.verify(nested_token, RESOURCE) is None
        assert await verifier.verify(valid, "https://wrong") is None
        assert (
            await verifier.verify(
                jwt.encode(
                    claims(), "secret", algorithm="HS256", headers={"kid": "one", "typ": "at+jwt"}
                ),
                RESOURCE,
            )
            is None
        )
        verifier._keys["one"] = jwt.PyJWK.from_dict(public | {"alg": "RS512"})
        assert await verifier.verify(valid, RESOURCE) is None
        verifier._refresh_after = 0
        payloads[0] = {"keys": []}

        with pytest.raises(McpAccessError):
            await verifier._refresh()

        verifier._refresh_after = 0
        payloads[0] = {"keys": ["not-an-object"]}

        with pytest.raises(McpAccessError):
            await verifier._refresh()

        verifier._refresh_after = 0
        payloads[0] = {"keys": [{"kid": "bad", "kty": "wrong"}]}

        with pytest.raises(McpAccessError):
            await verifier._refresh()

        verifier._refresh_after = 0
        payloads[0] = {"keys": [public | {"use": "enc"}]}
        await verifier._refresh()
        assert await verifier.verify(valid, RESOURCE) is None


async def test_durable_grants_and_concurrent_capacity(tmp_path):
    from mcpgtw.oauth.sqlite_grants import SqliteChannelGrantStore

    store = SqliteChannelGrantStore(str(tmp_path / "grants.db"), 1)
    p = principal()
    await store.grant(p, "one")
    await store.grant(p, "one")
    restarted = SqliteChannelGrantStore(store.path)
    assert await restarted.channels(p) == {"one"}

    with pytest.raises(ValueError, match="capacity"):
        await store.grant(p, "two")

    await store.revoke(p, "one")
    await store.grant(p, "two")
    await store.remove_channel("two")
    assert not await store.channels(p)

    for args in [("",), (":memory:",), (store.path, 0)]:
        with pytest.raises(ValueError):
            SqliteChannelGrantStore(*args)


@pytest.mark.parametrize("stateless", [True, False])
async def test_sec06_guard_all_http_methods_and_revocation(stateless):
    verifier = AsyncMock()
    verifier.verify.return_value = principal()
    grants = MemoryChannelGrantStore()
    gateway = Gateway(
        settings(mcp_stateless=stateless),
        access_token_verifier=verifier,
        channel_access=DenyUnlessGranted(grants),
    )
    channel = await gateway.create_channel()
    await grants.grant(principal(), channel.channel_id)
    assert channel._call_target(None) is None
    mode = "normal"

    async def handle(scope, receive, send):
        if mode == "revoke":
            await grants.remove_channel(channel.channel_id)

        if mode == "replace":
            await gateway.registry.remove_channel(channel.channel_id)

        headers = [(b"mcp-session-id", b"s")] if not stateless else []
        await send({"type": "http.response.start", "status": 200, "headers": headers})

        if mode == "midstream":
            await grants.revoke(principal(), channel.channel_id)

        await send({"type": "http.response.body", "body": b"", "more_body": False})

    gateway.manager.handle_request = AsyncMock(side_effect=handle)
    app = gateway.create_app()

    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app), base_url="https://game.example", follow_redirects=True
    ) as client:
        headers = {"Authorization": "Bearer valid"}
        assert (await client.post("/mcp", headers=headers)).status_code == 200
        assert (
            await client.get("/mcp", headers=headers | {"Mcp-Session-Id": "unknown"})
        ).status_code == 404
        assert (
            await client.get(
                "/mcp",
                headers=[
                    ("Authorization", "Bearer valid"),
                    ("Mcp-Session-Id", "s"),
                    ("Mcp-Session-Id", "s"),
                ],
            )
        ).status_code == 404

        if not stateless:
            assert (
                await client.get("/mcp", headers=headers | {"Mcp-Session-Id": "s"})
            ).status_code == 200
            assert (
                await client.delete("/mcp", headers=headers | {"Mcp-Session-Id": "s"})
            ).status_code == 200
            assert not gateway.session_bindings.accepts(
                "s",
                McpAccessContext(
                    channel.channel_id,
                    "oauth",
                    principal().principal_id,
                    "host",
                    principal().scopes,
                    ISSUER,
                    200,
                ),
            )
            gateway.session_bindings.bind(
                "s", McpAccessContext("other", "oauth", "other", "host", frozenset(), ISSUER, None)
            )
            assert (await client.post("/mcp", headers=headers)).status_code == 404
            gateway.session_bindings.clear()

        mode = "midstream"
        assert (await client.post("/mcp", headers=headers)).status_code == 200
        await grants.grant(principal(), channel.channel_id)
        mode = "revoke"
        assert (await client.post("/mcp", headers=headers)).status_code == 404
        await grants.grant(principal(), channel.channel_id)
        mode = "replace"
        assert (await client.post("/mcp", headers=headers)).status_code == 404


async def test_guard_identity_changed_and_invalid_session_cleanup():
    from mcpgtw.oauth.access_decision import AuthorizedMcpRequest

    gateway = Gateway(settings(), mcp_access_controller=AsyncMock())
    channel = await gateway.create_channel()
    context = McpAccessContext(
        channel.channel_id, "oauth", "alice", "host", frozenset(), ISSUER, None
    )
    gateway.session_bindings.bind("s", context)
    controller = gateway.mcp_access_controller
    controller.authorize.side_effect = [
        AuthorizedMcpRequest(channel, context),
        AuthorizedMcpRequest(channel, replace(context, principal_id="bob")),
    ]

    async def handle(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})

    gateway.manager.handle_request = handle
    messages = []

    async def send(message):
        messages.append(message)

    scope = request([(b"mcp-session-id", b"s")]).scope
    await gateway.mcp_asgi(scope, AsyncMock(), send)
    assert messages[0]["status"] == 404
    assert not gateway.session_bindings.accepts("s", context)


@pytest.mark.parametrize("stateless", [False, True])
@pytest.mark.parametrize("json_response", [False, True])
async def test_oauth_official_mcp_client_round_trip(stateless, json_response):
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from support import FakeWebSocket

    from mcpgtw.oauth.tool_access import RequiredScopesToolAccess

    class AnsweringProvider(FakeWebSocket):
        async def send_json(self, message):
            await super().send_json(message)

            if message["type"] == "request":
                await channel.handle_provider_message(
                    self,
                    {
                        "type": "result",
                        "requestId": message["requestId"],
                        "result": {"content": [{"type": "text", "text": "authorized"}]},
                    },
                )

    verifier = AsyncMock()
    verifier.verify.return_value = principal()
    grants = MemoryChannelGrantStore()
    gateway = Gateway(
        settings(mcp_stateless=stateless, mcp_json_response=json_response),
        access_token_verifier=verifier,
        channel_access=DenyUnlessGranted(grants),
    )
    app = gateway.create_app()

    async with app.router.lifespan_context(app):
        channel = await gateway.create_channel()
        provider = AnsweringProvider()
        await channel.attach(provider, provider_id="test", provider_name=None)
        await channel.register("tools", [{"name": "hello", "inputSchema": {"type": "object"}}])
        await grants.grant(principal(), channel.channel_id)

        async with (
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app),
                base_url=RESOURCE,
                headers={"Authorization": "Bearer access-token"},
                follow_redirects=True,
            ) as client,
            streamable_http_client(RESOURCE, http_client=client) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            tools = (await session.list_tools()).tools
            assert tools[0].name == "hello"
            assert tools[0].meta["securitySchemes"] == [
                {"type": "oauth2", "scopes": ["mcp:access"]}
            ]
            gateway.tool_access_policy = RequiredScopesToolAccess(
                frozenset({"mcp:access", "game:write"})
            )
            denied = await session.call_tool("hello", {})
            assert denied.is_error
            assert 'error="insufficient_scope"' in denied.meta["mcp/www_authenticate"][0]
            assert 'scope="game:write mcp:access"' in denied.meta["mcp/www_authenticate"][0]
            assert not any(message.get("type") == "request" for message in provider.messages)
            verifier.verify.return_value = replace(
                principal(), scopes=frozenset({"mcp:access", "game:write"})
            )
            await grants.grant(verifier.verify.return_value, channel.channel_id)
            assert (await session.call_tool("hello", {})).content[0].text == "authorized"


def test_rate_limit_capacity_expiry_and_config(monkeypatch):
    from mcpgtw.oauth.rate_limit import WindowOAuthRateLimitPolicy

    policy = WindowOAuthRateLimitPolicy(1, 1, 1)
    assert policy.admit("one")
    assert not policy.admit("one")
    assert not policy.admit("two")
    monkeypatch.setattr("mcpgtw.oauth.rate_limit.time.monotonic", lambda: float("inf"))
    assert policy.admit("two")

    for args in [(0,), (1, 0), (1, 1, 0)]:
        with pytest.raises(ValueError):
            WindowOAuthRateLimitPolicy(*args)


async def test_http_budget_and_injected_policy():
    from mcpgtw.oauth.rate_limit import WindowOAuthRateLimitPolicy

    gateway = Gateway(
        settings(),
        mcp_access_controller=AsyncMock(side_effect=McpAccessError("invalid_token")),
        oauth_rate_limit=WindowOAuthRateLimitPolicy(1),
    )
    gateway.mcp_access_controller.authorize.side_effect = McpAccessError("invalid_token")
    app = gateway.create_app()

    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app), base_url=RESOURCE, follow_redirects=True
    ) as client:
        assert (await client.post(RESOURCE)).status_code == 401
        response = await client.post(RESOURCE)
        assert response.status_code == 429
        assert response.headers["retry-after"] == "1"


@pytest.mark.parametrize("window", [float("inf"), float("nan")])
def test_rate_limit_nonfinite_window(window):
    from mcpgtw.oauth.rate_limit import WindowOAuthRateLimitPolicy

    with pytest.raises(ValueError):
        WindowOAuthRateLimitPolicy(window_seconds=window)


def test_uvicorn_websocket_queries_are_redacted():
    import logging

    from mcpgtw.oauth.log_filter import OAuthLogFilter

    record = logging.LogRecord(
        "uvicorn.error",
        logging.INFO,
        "",
        0,
        '%s - "WebSocket %s" [accepted]',
        ("127.0.0.1", "/app/stream?ticket=private&token=secret"),
        None,
    )
    assert OAuthLogFilter().filter(record)
    assert record.getMessage() == '127.0.0.1 - "WebSocket /app/stream?[redacted]" [accepted]'
    assert record.args == ()


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
async def test_canonical_endpoint_challenge_without_redirect(method):
    verifier = AsyncMock()
    grants = MemoryChannelGrantStore()
    gateway = Gateway(
        settings(), access_token_verifier=verifier, channel_access=DenyUnlessGranted(grants)
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(gateway.create_app()),
        base_url=RESOURCE.removesuffix("/mcp"),
        follow_redirects=False,
    ) as client:
        response = await client.request(method, "/mcp")
        assert response.status_code == 401
        assert "resource_metadata" in response.headers["www-authenticate"]
        assert not verifier.verify.called


async def test_owned_verifier_http_client_closes_on_shutdown():
    gateway = Gateway(
        settings(oauth_token_verifier="jwt", oauth_jwks_url="https://a/jwks"),
        channel_access=DenyUnlessGranted(MemoryChannelGrantStore()),
    )
    client = gateway._oauth_http_client
    assert not client.is_closed

    async with gateway.lifespan(gateway.create_app()):
        assert not client.is_closed

    assert client.is_closed


async def test_durable_grant_expiry_private_file_and_reconsent(tmp_path, monkeypatch):
    from mcpgtw.oauth.sqlite_grants import SqliteChannelGrantStore

    store = SqliteChannelGrantStore(str(tmp_path / "grants.db"), 1)
    p = principal()
    await store.grant(p, "old-generation")
    assert (tmp_path / "grants.db").stat().st_mode & 0o077 == 0
    assert await store.channels(p) == {"old-generation"}
    renewed = replace(p, expires_at=p.expires_at + 300)
    await store.grant(renewed, "old-generation")
    monkeypatch.setattr("mcpgtw.oauth.sqlite_grants.time.time", lambda: p.expires_at + 1)
    assert await store.channels(renewed) == {"old-generation"}
    monkeypatch.setattr("mcpgtw.oauth.sqlite_grants.time.time", lambda: renewed.expires_at + 1)
    assert not await store.channels(renewed)
    await store.grant(replace(p, expires_at=renewed.expires_at + 500), "new-generation")
    assert await store.channels(p) == {"new-generation"}


def test_invalid_configuration_does_not_render_client_secret():
    secret = "do-not-render-introspection-credential"

    with pytest.raises(ValidationError) as error:
        settings(oauth_introspection_client_secret=secret, oauth_required_scopes=["bad scope"])

    assert secret not in str(error.value)


@pytest.mark.parametrize("injected", [False, True])
async def test_tool_auth_metadata_and_revocation_after_admission(injected):
    from types import SimpleNamespace

    from mcp.types import CallToolRequestParams, PaginatedRequestParams
    from support import FakeWebSocket

    from mcpgtw.oauth.tool_access import RequiredScopesToolAccess

    class ToolPolicy(RequiredScopesToolAccess):
        def required_scopes(self, channel, tool_name):
            return super().required_scopes(channel, tool_name) | {"game:write"}

    class CustomGateway(Gateway):
        tool_access_policy_class = ToolPolicy

    policy = ToolPolicy(frozenset({"mcp:access"}))
    gateway = CustomGateway(
        settings(),
        mcp_access_controller=AsyncMock(),
        **({"tool_access_policy": policy} if injected else {}),
    )
    assert isinstance(gateway.tool_access_policy, ToolPolicy)

    if injected:
        assert gateway.tool_access_policy is policy
    channel = await gateway.create_channel()
    await channel.attach(FakeWebSocket(), provider_id="test", provider_name=None)
    await channel.register("tools", [{"name": "hello", "inputSchema": {"type": "object"}}])
    context = McpAccessContext(
        channel.channel_id,
        "oauth",
        "alice",
        "host",
        frozenset({"mcp:access"}),
        ISSUER,
        int(time.time()) + 900,
    )
    scope = request().scope | {
        "gateway_channel_id": channel.channel_id,
        "gateway_access_context": context,
    }
    ctx = SimpleNamespace(request=Request(scope), session=AsyncMock(), meta=None)
    listed = await gateway.server._request_handlers["tools/list"].handler(
        ctx, PaginatedRequestParams()
    )
    assert listed.model_dump(by_alias=True)["tools"][0]["securitySchemes"] == [
        {"type": "oauth2", "scopes": ["game:write", "mcp:access"]}
    ]
    assert channel.list_tools()[0].meta is None
    gateway.mcp_access_controller.authorize.side_effect = McpAccessError("invalid_token")
    result = await gateway.server._request_handlers["tools/call"].handler(
        ctx, CallToolRequestParams(name="hello", arguments={})
    )
    assert result.is_error
    assert 'error="invalid_token"' in result.meta["mcp/www_authenticate"][0]
    assert "error_description=" in result.meta["mcp/www_authenticate"][0]
    publisher = gateway.oauth_metadata
    publisher.rate_limit.requests = 1
    assert (await publisher.metadata(request())).status_code == 200
    assert (await publisher.metadata(request())).status_code == 429


async def test_revoked_credential_keeps_http_challenge_before_response_start():
    from mcpgtw.oauth.access_decision import AuthorizedMcpRequest

    controller = AsyncMock()
    gateway = Gateway(settings(mcp_stateless=True), mcp_access_controller=controller)
    channel = await gateway.create_channel()
    access = McpAccessContext(
        channel.channel_id,
        "oauth",
        "alice",
        "host",
        frozenset({"mcp:access"}),
        ISSUER,
        int(time.time()) + 900,
    )
    controller.authorize.side_effect = [
        AuthorizedMcpRequest(channel, access),
        McpAccessError("invalid_token"),
    ]

    async def manager(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})

    gateway.manager.handle_request = manager
    sent = AsyncMock()
    await gateway.mcp_asgi(request().scope, AsyncMock(), sent)
    response = sent.await_args_list[0].args[0]
    assert response["status"] == 401
    headers = dict(response["headers"])
    assert b'resource_metadata="https://game.example/.well-known/' in headers[b"www-authenticate"]
    assert b'error="invalid_token"' in headers[b"www-authenticate"]
    assert headers[b"cache-control"] == b"no-store"


@pytest.mark.parametrize("changed", ["channel", "principal"])
async def test_sec22_tool_reauthorization_cannot_change_its_channel_or_owner(changed, monkeypatch):
    from types import SimpleNamespace

    from mcp.types import CallToolRequestParams

    from mcpgtw.oauth.access_decision import AuthorizedMcpRequest

    controller = AsyncMock()
    gateway = Gateway(settings(), mcp_access_controller=controller)
    channel = await gateway.create_channel()
    other = await gateway.create_channel()
    access = McpAccessContext(
        channel.channel_id, "oauth", "alice", "host", frozenset({"mcp:access"}), ISSUER, None
    )
    current_channel = other if changed == "channel" else channel
    current = replace(
        access,
        channel_id=current_channel.channel_id,
        principal_id="bob" if changed == "principal" else access.principal_id,
    )
    controller.authorize.return_value = AuthorizedMcpRequest(current_channel, current)
    dispatch = AsyncMock()
    monkeypatch.setattr(type(channel), "execute_tool", dispatch)
    scope = request().scope | {
        "gateway_channel_id": channel.channel_id,
        "gateway_access_context": access,
    }
    ctx = SimpleNamespace(request=Request(scope), session=AsyncMock(), meta=None)
    result = await gateway.server._request_handlers["tools/call"].handler(
        ctx, CallToolRequestParams(name="hello", arguments={})
    )
    assert result.is_error
    assert 'error="invalid_token"' in result.meta["mcp/www_authenticate"][0]
    dispatch.assert_not_called()


async def test_sec22_stream_reauthorization_cannot_switch_to_a_new_granted_channel():
    from mcpgtw.oauth.access_decision import AuthorizedMcpRequest

    controller = AsyncMock()
    gateway = Gateway(settings(mcp_stateless=True), mcp_access_controller=controller)
    channel = await gateway.create_channel()
    other = await gateway.create_channel()
    access = McpAccessContext(
        channel.channel_id, "oauth", "alice", "host", frozenset({"mcp:access"}), ISSUER, None
    )
    controller.authorize.side_effect = [
        AuthorizedMcpRequest(channel, access),
        AuthorizedMcpRequest(other, replace(access, channel_id=other.channel_id)),
    ]

    async def manager(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})

    gateway.manager.handle_request = manager
    sent = AsyncMock()
    await gateway.mcp_asgi(request().scope, AsyncMock(), sent)
    assert sent.await_args_list[0].args[0]["status"] == 404


@pytest.mark.parametrize("durable", [False, True])
async def test_sec04_grants_scope_reduction_reconsent_and_client_isolation(tmp_path, durable):
    store = (
        SqliteChannelGrantStore(str(tmp_path / "grants.db"))
        if durable
        else MemoryChannelGrantStore()
    )
    owner = principal()
    broader = replace(owner, scopes=owner.scopes | {"game:write"})
    reader = replace(owner, client_id="reader")
    policy = DenyUnlessGranted(store)
    await store.grant(owner, "game")
    assert await policy.resolve(owner, "game") == "game"
    assert await policy.resolve(broader, "game") is None
    assert await policy.resolve(reader, "game") is None
    await store.grant(broader, "game")
    await store.grant(reader, "game")
    assert await policy.resolve(broader, "game") == "game"
    assert await policy.resolve(replace(reader, scopes=broader.scopes), "game") is None
    await store.grant(owner, "game")
    assert await policy.resolve(broader, "game") is None
    assert await policy.resolve(replace(owner, scopes=frozenset()), "game") == "game"

    if durable:
        restarted = SqliteChannelGrantStore(store.path)
        assert await restarted.channels(broader) == frozenset()
        assert await restarted.channels(reader) == frozenset({"game"})


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
@pytest.mark.parametrize(
    "changes",
    [
        {"channel_id": "another-channel"},
        {"principal_id": "another-user"},
        {"client_id": "another-client"},
        {"issuer": "https://another-issuer.example"},
        {"credential_kind": "token"},
    ],
)
async def test_sec06_stolen_session_all_identity_fields_denied_before_manager(method, changes):
    from mcpgtw.oauth.access_decision import AuthorizedMcpRequest

    controller = AsyncMock()
    gateway = Gateway(settings(mcp_stateless=False), mcp_access_controller=controller)
    channel = await gateway.create_channel()
    original = McpAccessContext(
        channel.channel_id, "oauth", "alice", "host", frozenset({"mcp:access"}), ISSUER, None
    )
    gateway.session_bindings.bind("stolen", original)
    controller.authorize.return_value = AuthorizedMcpRequest(channel, replace(original, **changes))
    gateway.manager.handle_request = AsyncMock()
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(gateway.create_app()), base_url=RESOURCE
    ) as client:
        response = await client.request(method, RESOURCE, headers={"Mcp-Session-Id": "stolen"})

    assert response.status_code == 404
    gateway.manager.handle_request.assert_not_awaited()
    assert not channel._pending


@pytest.mark.parametrize("exhausted", ["client", "principal"])
async def test_sec16_verified_client_principal_limits_block_before_manager(exhausted):
    from mcpgtw.oauth.access_decision import AuthorizedMcpRequest
    from mcpgtw.oauth.rate_limit import WindowOAuthRateLimitPolicy

    controller = AsyncMock()
    budget = WindowOAuthRateLimitPolicy(1, 60)
    gateway = Gateway(settings(), mcp_access_controller=controller, oauth_rate_limit=budget)
    channel = await gateway.create_channel()
    context = McpAccessContext(
        channel.channel_id, "oauth", "alice", "host", frozenset(), ISSUER, None
    )
    controller.authorize.return_value = AuthorizedMcpRequest(channel, context)
    key = "client:" + ISSUER + "\0host" if exhausted == "client" else "principal:alice"
    assert budget.admit(key)
    gateway.manager.handle_request = AsyncMock()
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(gateway.create_app()), base_url=RESOURCE
    ) as client:
        response = await client.post(RESOURCE)

    assert response.status_code == 429
    assert int(response.headers["retry-after"]) >= 1
    gateway.manager.handle_request.assert_not_awaited()


async def test_sec18_prefixed_public_resource_mount_metadata_and_admin_collision():
    resource = "https://game.example/public/mcp"
    verifier = AsyncMock()
    verifier.verify.return_value = principal()
    grants = MemoryChannelGrantStore()
    config = settings(oauth_resource_url=resource)
    gateway = Gateway(
        config, access_token_verifier=verifier, channel_access=DenyUnlessGranted(grants)
    )
    channel = await gateway.create_channel()
    await grants.grant(principal(), channel.channel_id)

    async def handle(scope, receive, send):
        assert scope["root_path"] == "/public/mcp"
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"authorized"})

    gateway.manager.handle_request = AsyncMock(side_effect=handle)
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(gateway.create_app())) as client:
        response = await client.post(resource)
        assert response.status_code == 401
        assert (
            "/.well-known/oauth-protected-resource/public/mcp"
            in response.headers["www-authenticate"]
        )
        metadata = await client.get(
            "https://game.example/.well-known/oauth-protected-resource/public/mcp"
        )
        assert metadata.json()["resource"] == resource
        assert (
            await client.post(resource, headers={"Authorization": "Bearer valid"})
        ).content == b"authorized"
        assert (
            await client.post(
                resource + "/" + channel.channel_id, headers={"Authorization": "Bearer valid"}
            )
        ).status_code == 200
        assert (await client.post("https://game.example/mcp")).status_code == 404

    for path in ["/public/mcp", "/public/mcp/stats"]:
        with pytest.raises(GatewayConfigurationError):
            Gateway(
                settings(
                    oauth_resource_url=resource,
                    admin_enabled=True,
                    admin_key="secret",
                    admin_path=path,
                ),
                access_token_verifier=verifier,
                channel_access=DenyUnlessGranted(grants),
            )


async def test_memory_grant_deadlines_cannot_be_renewed_by_tokens_and_release_capacity(monkeypatch):
    now = time.time()
    monkeypatch.setattr("mcpgtw.oauth.channel_grants.time.time", lambda: now)
    store = MemoryChannelGrantStore(1)
    owner = replace(principal(), expires_at=int(now) + 10)
    renewed = replace(owner, expires_at=int(now) + 1000)
    await store.grant(owner, "old")
    assert await store.channels(renewed) == frozenset({"old"})
    now += 11
    assert not await store.channels(renewed)
    await store.grant(renewed, "new")
    assert await store.channels(renewed) == frozenset({"new"})
    assert store._count == 1
    assert "old" not in store._grants[owner.principal_id]


def test_session_binding_strategy_class_and_instance_injection():
    class CustomBindings(MemoryMcpSessionBindingStore):
        pass

    class CustomGateway(Gateway):
        session_binding_store_class = CustomBindings

    assert isinstance(CustomGateway().session_bindings, CustomBindings)
    bindings = CustomBindings()
    assert Gateway(session_bindings=bindings).session_bindings is bindings


def test_metadata_and_rate_strategy_class_and_instance_injection():
    from mcpgtw.oauth.rate_limit import WindowOAuthRateLimitPolicy
    from mcpgtw.oauth.resource_metadata import ProtectedResourceMetadataPublisher

    class CustomMetadata(ProtectedResourceMetadataPublisher):
        pass

    class CustomRateLimit(WindowOAuthRateLimitPolicy):
        pass

    class CustomGateway(Gateway):
        oauth_metadata_class = CustomMetadata
        oauth_rate_limit_class = CustomRateLimit

    gateway = CustomGateway(settings(), mcp_access_controller=AsyncMock())
    assert isinstance(gateway.oauth_metadata, CustomMetadata)
    assert isinstance(gateway.oauth_rate_limit, CustomRateLimit)
    metadata = CustomMetadata(settings())
    gateway = Gateway(settings(), mcp_access_controller=AsyncMock(), oauth_metadata=metadata)
    assert gateway.oauth_metadata is metadata
    with pytest.raises(GatewayConfigurationError):
        Gateway(oauth_metadata=metadata)
