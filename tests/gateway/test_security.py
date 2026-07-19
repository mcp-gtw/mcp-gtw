from __future__ import annotations

from mcp_gtw.helpers.security import (
    constant_time_equals,
    extract_bearer_token,
    generate_token,
    origin_is_allowed,
)


def test_generate_token_is_unique_and_urlsafe() -> None:
    token = generate_token()
    assert token != generate_token()
    assert "/" not in token and "+" not in token


def test_constant_time_equals() -> None:
    assert constant_time_equals("abc", "abc") is True
    assert constant_time_equals("abc", "abd") is False
    assert constant_time_equals(None, "abc") is False


def test_extract_bearer_token() -> None:
    assert extract_bearer_token("Bearer abc") == "abc"
    assert extract_bearer_token("Bearer   ") is None
    assert extract_bearer_token("Basic abc") is None
    assert extract_bearer_token(None) is None


def test_origin_is_allowed() -> None:
    assert origin_is_allowed(None, ["http://a"]) is True
    assert origin_is_allowed("http://a", ["http://a"]) is True
    assert origin_is_allowed("http://evil", ["http://a"]) is False
    assert origin_is_allowed("http://anything", ["*"]) is True
    assert origin_is_allowed("http://anything", []) is False
