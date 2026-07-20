from __future__ import annotations

import pytest
from pydantic import ValidationError

from mcp_gtw.config import GatewaySettings


def test_parse_csv_list_from_string() -> None:
    settings = GatewaySettings(allowed_provider_origins="http://a, http://b")
    assert settings.allowed_provider_origins == ["http://a", "http://b"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reaper_interval_seconds", 0),
        ("maximum_mcp_sessions_per_channel", 0),
        ("maximum_subscriptions_per_channel", 0),
        ("maximum_channels", -1),
        ("tool_call_timeout_seconds", 0),
        ("maximum_json_depth", 0),
        ("port", 70000),
    ],
)
def test_non_positive_limits_are_rejected(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        GatewaySettings(**{field: value})


def test_env_csv_list_is_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GATEWAY_ALLOWED_MCP_ORIGINS", "http://a,http://b")
    assert GatewaySettings().allowed_mcp_origins == ["http://a", "http://b"]


def test_parse_csv_list_passes_through_lists() -> None:
    settings = GatewaySettings(allowed_mcp_origins=["http://a"])
    assert settings.allowed_mcp_origins == ["http://a"]


def test_admin_path_defaults_to_admin() -> None:
    assert GatewaySettings().admin_path == "/admin"


@pytest.mark.parametrize("value", ["admin", "/", "/admin/", ""])
def test_invalid_admin_path_is_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        GatewaySettings(admin_path=value)


def test_custom_admin_path_is_accepted() -> None:
    assert GatewaySettings(admin_path="/ops/secret").admin_path == "/ops/secret"


def test_empty_env_disables_capacity_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GATEWAY_MAXIMUM_TOOLS", "")
    monkeypatch.setenv("GATEWAY_MAXIMUM_CHANNELS", "  ")
    monkeypatch.setenv("GATEWAY_TOOL_CALL_TIMEOUT_SECONDS", "")
    settings = GatewaySettings()
    assert settings.maximum_tools is None
    assert settings.maximum_channels is None
    assert settings.tool_call_timeout_seconds is None


def test_capacity_limits_accept_none() -> None:
    settings = GatewaySettings(
        maximum_subscriptions_per_channel=None, maximum_pending_calls_per_channel=None
    )
    assert settings.maximum_subscriptions_per_channel is None
    assert settings.maximum_pending_calls_per_channel is None


def test_port_reads_the_platform_port_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GATEWAY_PORT", raising=False)
    monkeypatch.setenv("PORT", "10000")
    assert GatewaySettings().port == 10000


def test_gateway_port_overrides_the_platform_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORT", "10000")
    monkeypatch.setenv("GATEWAY_PORT", "8080")
    assert GatewaySettings().port == 8080
