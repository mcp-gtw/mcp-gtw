from __future__ import annotations

import pytest

from mcp_gtw.authenticator import Authenticator, TokenAuthenticator, extract_bearer_token
from mcp_gtw.config import GatewaySettings
from mcp_gtw.registry import ChannelRegistry
from mcp_gtw.tokens import SecretsTokenProvider


class FakeConnection:
    def __init__(self, query_params: dict[str, str], headers: dict[str, str]) -> None:
        self.query_params = query_params
        self.headers = headers


def test_extract_bearer_token() -> None:
    assert extract_bearer_token("Bearer abc") == "abc"
    assert extract_bearer_token("Bearer   ") is None
    assert extract_bearer_token("Basic abc") is None
    assert extract_bearer_token(None) is None


async def test_token_authenticator_resolves_provider(settings: GatewaySettings) -> None:
    tokens = SecretsTokenProvider()
    registry = ChannelRegistry(settings, tokens=tokens)
    channel = await registry.create_channel()
    authenticator = TokenAuthenticator(registry)

    known = FakeConnection({"token": channel.provider_token}, {})
    unknown = FakeConnection({"token": "nope"}, {})

    assert await authenticator.authenticate_provider(known) is channel
    assert await authenticator.authenticate_provider(unknown) is None


async def test_token_authenticator_resolves_client(settings: GatewaySettings) -> None:
    tokens = SecretsTokenProvider()
    registry = ChannelRegistry(settings, tokens=tokens)
    channel = await registry.create_channel()
    authenticator = TokenAuthenticator(registry)

    valid = FakeConnection({}, {"authorization": f"Bearer {channel.mcp_token}"})
    missing = FakeConnection({}, {})

    assert await authenticator.authenticate_client(valid) is channel
    assert await authenticator.authenticate_client(missing) is None


def test_authenticator_is_abstract() -> None:
    with pytest.raises(TypeError):
        Authenticator()  # type: ignore[abstract]
