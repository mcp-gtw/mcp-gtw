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
