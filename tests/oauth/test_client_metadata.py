from __future__ import annotations

import asyncio
import json
import socket
import ssl
from unittest.mock import AsyncMock, Mock

import httpcore
import pytest

from mcpgtw.oauth.client_metadata import HttpsClientMetadataResolver
from mcpgtw.oauth.client_registry import OAuthClientRegistry
from mcpgtw.oauth.public_network import PublicNetworkBackend
from mcpgtw.oauth.rate_limit import WindowOAuthRateLimitPolicy
from mcpgtw.oauth.state_store import SqliteOAuthStateStore

URL = "https://client.example/metadata.json"
DATA = {
    "client_id": URL,
    "client_name": "MCP host",
    "redirect_uris": ["https://client.example/callback"],
    "token_endpoint_auth_method": "none",
    "grant_types": ["authorization_code", "refresh_token"],
    "response_types": ["code"],
    "logo_uri": "http://169.254.169.254/logo",
}


def network(data=DATA, status=200, content_type="application/json", encoding="identity", cache=""):
    body = json.dumps(data).encode()
    return httpcore.AsyncMockBackend(
        [
            f"HTTP/1.1 {status} Response\r\nContent-Type: {content_type}\r\n"
            f"Content-Encoding: {encoding}\r\nCache-Control: {cache}\r\n"
            f"Content-Length: {len(body)}\r\n\r\n".encode()
            + body
        ]
    )


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "0.0.0.0",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.0.1",
        "169.254.169.254",
        "100.64.0.1",
        "224.0.0.1",
        "240.0.0.1",
        "::1",
        "fc00::1",
        "fe80::1",
        "ff02::1",
        "::ffff:8.8.8.8",
        "::ffff:127.0.0.1",
    ],
)
def test_sec13_nonpublic_addresses(address):
    with pytest.raises(ValueError):
        PublicNetworkBackend.public_address(address)


async def test_sec13_dns_pin_and_peer_verification(monkeypatch):
    resolve = AsyncMock(return_value=[(socket.AF_INET, 1, 6, "", ("8.8.8.8", 443))])
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    backend = AsyncMock(spec=httpcore.AsyncNetworkBackend)
    stream = Mock(spec=httpcore.AsyncNetworkStream)
    stream.get_extra_info.return_value = ("8.8.8.8", 443)
    stream.aclose = AsyncMock()
    backend.connect_tcp.return_value = stream
    safe = PublicNetworkBackend(backend)
    assert await safe.connect_tcp("client.example", 443, timeout=1) is stream
    backend.connect_tcp.assert_awaited_once_with("8.8.8.8", 443, 1, None, None)
    assert isinstance(PublicNetworkBackend().backend, httpcore.AnyIOBackend)

    for peer in [None, ("1.1.1.1", 443), ("127.0.0.1", 443)]:
        stream.get_extra_info.return_value = peer

        with pytest.raises(ValueError):
            await safe.connect_tcp("client.example", 443)

    assert stream.aclose.await_count == 3
    backend.connect_tcp.reset_mock()

    for answers in [[], [(socket.AF_INET, 1, 6, "", ("127.0.0.1", 443))]]:
        resolve.return_value = answers

        with pytest.raises(ValueError):
            await safe.connect_tcp("client.example", 443)

    backend.connect_tcp.assert_not_awaited()


@pytest.mark.parametrize(
    "url",
    [
        "http://client.example/doc",
        "//client.example/doc",
        "https://client.example",
        "https://user:password@client.example/doc",
        "https://client.example/doc#f",
        "https:///doc",
        "https://client.example/a\n",
        "https://client.example/\\a",
        "https://client.example/" + "a" * 2050,
    ],
)
async def test_sec14_invalid_metadata_urls(url):
    with pytest.raises(ValueError):
        await HttpsClientMetadataResolver().resolve(url)


async def test_sec14_bounded_fetch_no_secondary_urls():
    resolver = HttpsClientMetadataResolver(network=network())
    assert resolver.ssl_context.verify_mode == ssl.CERT_REQUIRED
    assert resolver.ssl_context.check_hostname
    assert await resolver.resolve(URL) == {**DATA, "_cache_ttl_seconds": 300}
    assert isinstance(HttpsClientMetadataResolver().network, PublicNetworkBackend)
    assert await HttpsClientMetadataResolver(
        allowed_origins=frozenset({"https://client.example"}), network=network()
    ).resolve(URL) == {**DATA, "_cache_ttl_seconds": 300}

    with pytest.raises(ValueError):
        await HttpsClientMetadataResolver(
            allowed_origins=frozenset({"https://different.example"})
        ).resolve(URL)

    for backend in [
        network(status=302),
        network(content_type="text/html"),
        network(encoding="gzip"),
    ]:
        with pytest.raises(ValueError):
            await HttpsClientMetadataResolver(network=backend).resolve(URL)

    with pytest.raises(ValueError):
        await HttpsClientMetadataResolver(maximum_bytes=1, network=network()).resolve(URL)

    for data in [
        [],
        {**DATA, "client_id": "https://other.example/doc"},
        {**DATA, "token_endpoint_auth_method": "private_key_jwt"},
        {**DATA, "client_secret": "secret"},
        {**DATA, "client_secret_expires_at": 10},
        {**DATA, "jwks": {}},
        {**DATA, "token_endpoint_auth_methods_supported": "none"},
        {**DATA, "token_endpoint_auth_methods_supported": ["private_key_jwt"]},
    ]:
        with pytest.raises(ValueError):
            await HttpsClientMetadataResolver(network=network(data)).resolve(URL)

    transition = {
        **DATA,
        "token_endpoint_auth_method": "private_key_jwt",
        "token_endpoint_auth_methods_supported": ["none", "private_key_jwt"],
        "jwks_uri": "http://169.254.169.254/keys",
    }
    assert (await HttpsClientMetadataResolver(network=network(transition)).resolve(URL))[
        "token_endpoint_auth_method"
    ] == "none"


@pytest.mark.parametrize(
    "cache,ttl", [("no-store", 0), ("no-cache", 0), ("max-age=30", 30), ("max-age=99999", 300)]
)
async def test_metadata_cache_control(cache, ttl):
    result = await HttpsClientMetadataResolver(network=network(cache=cache)).resolve(URL)
    assert result["_cache_ttl_seconds"] == ttl


@pytest.mark.parametrize("timeout,maximum", [(0, 100), (float("inf"), 100), (1, 0)])
def test_metadata_limits(timeout, maximum):
    with pytest.raises(ValueError):
        HttpsClientMetadataResolver(timeout, maximum)


async def test_metadata_registry_cache_limits_and_failure(tmp_path):
    store = SqliteOAuthStateStore(str(tmp_path / "oauth.db"))
    resolver = AsyncMock()
    resolver.resolve.return_value = DATA
    registry = OAuthClientRegistry(
        store, "https://issuer.example", "https://issuer.example/mcp", metadata_resolver=resolver
    )
    assert (await registry.get(URL))["client_id"] == URL
    assert (await registry.get(URL))["client_id"] == URL
    resolver.resolve.assert_awaited_once_with(URL)
    await store.remove("cimd", URL)
    resolver.resolve.side_effect = httpcore.ConnectError("TLS failed")
    assert await registry.get(URL) is None
    resolver.resolve.side_effect = ValueError("invalid document")
    assert await registry.get(URL) is None
    registry.metadata_limit.requests = 1
    assert await registry.get(URL) is None
    assert await registry.get("unknown") is None
    assert await OAuthClientRegistry(store, "https://issuer.example", "resource").get(URL) is None
    registry.metadata_limit = WindowOAuthRateLimitPolicy(100)
    resolver.resolve.side_effect = None
    resolver.resolve.return_value = {**DATA, "_cache_ttl_seconds": 0}
    assert await registry.get(URL)
    assert await store.get("cimd", URL) is None
