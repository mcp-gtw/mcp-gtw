from __future__ import annotations

import pytest

from mcp_gtw.tokens import SecretsTokenProvider, TokenProvider


def test_generate_is_unique_and_urlsafe() -> None:
    tokens = SecretsTokenProvider()
    token = tokens.generate()
    assert token != tokens.generate()
    assert "/" not in token and "+" not in token


def test_generate_respects_length() -> None:
    tokens = SecretsTokenProvider()
    assert len(tokens.generate(4)) < len(tokens.generate(64))


def test_equals_is_constant_time_and_null_safe() -> None:
    tokens = SecretsTokenProvider()
    assert tokens.equals("abc", "abc") is True
    assert tokens.equals("abc", "abd") is False
    assert tokens.equals(None, "abc") is False


def test_equals_handles_non_ascii_without_crashing() -> None:
    tokens = SecretsTokenProvider()
    assert tokens.equals("café", "café") is True
    assert tokens.equals("café", "cafe") is False
    assert tokens.equals("😀", "secret") is False


def test_token_provider_is_abstract() -> None:
    with pytest.raises(TypeError):
        TokenProvider()  # type: ignore[abstract]
