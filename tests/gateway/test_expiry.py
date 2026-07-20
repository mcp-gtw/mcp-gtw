from __future__ import annotations

import math

import pytest

from mcp_gtw.expiry import ExpiryPolicy, TtlExpiryPolicy


def test_initial_deadline_uses_offline_ttl_by_default() -> None:
    policy = TtlExpiryPolicy(300.0)
    assert policy.initial_deadline(1000.0, None) == 1300.0


def test_initial_deadline_honours_explicit_ttl() -> None:
    policy = TtlExpiryPolicy(300.0)
    assert policy.initial_deadline(1000.0, 5.0) == 1005.0


def test_connected_deadline_is_infinite() -> None:
    assert math.isinf(TtlExpiryPolicy(300.0).connected_deadline())


def test_disconnected_deadline_adds_offline_ttl() -> None:
    assert TtlExpiryPolicy(300.0).disconnected_deadline(1000.0) == 1300.0


def test_is_expired() -> None:
    policy = TtlExpiryPolicy(300.0)
    assert policy.is_expired(1000.0, 1000.0) is True
    assert policy.is_expired(1001.0, 1000.0) is False


def test_reclaim_in_is_none_for_infinite_deadline() -> None:
    assert TtlExpiryPolicy(300.0).reclaim_in(math.inf, 1000.0) is None


def test_reclaim_in_never_negative() -> None:
    policy = TtlExpiryPolicy(300.0)
    assert policy.reclaim_in(1200.0, 1000.0) == 200.0
    assert policy.reclaim_in(900.0, 1000.0) == 0.0


def test_expiry_policy_is_abstract() -> None:
    with pytest.raises(TypeError):
        ExpiryPolicy()  # type: ignore[abstract]
