from __future__ import annotations

import pytest

from mcp_gtw.origin import ListOriginPolicy, OriginPolicy


def test_missing_origin_is_always_allowed() -> None:
    assert ListOriginPolicy(["http://a"]).allows(None) is True
    assert ListOriginPolicy([]).allows(None) is True


def test_list_membership() -> None:
    policy = ListOriginPolicy(["http://a"])
    assert policy.allows("http://a") is True
    assert policy.allows("http://evil") is False


def test_wildcard_allows_every_origin() -> None:
    policy = ListOriginPolicy(["*"])
    assert policy.allows("http://anything") is True


def test_empty_list_allows_no_origin() -> None:
    assert ListOriginPolicy([]).allows("http://anything") is False


def test_origin_policy_is_abstract() -> None:
    with pytest.raises(TypeError):
        OriginPolicy()  # type: ignore[abstract]
