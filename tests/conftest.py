from __future__ import annotations

from typing import Any

import pytest
from support import FakeWebSocket

from mcp_gtw.config import GatewaySettings


@pytest.fixture
def settings() -> GatewaySettings:
    return GatewaySettings(
        tool_call_timeout_seconds=0.05,
        maximum_pending_calls_per_channel=4,
        allowed_provider_origins=["http://testserver"],
    )


@pytest.fixture
def move_tool() -> dict[str, Any]:
    return {
        "name": "move",
        "description": "Moves a player",
        "inputSchema": {
            "type": "object",
            "properties": {"direction": {"type": "string", "enum": ["left", "right"]}},
            "required": ["direction"],
            "additionalProperties": False,
        },
    }


@pytest.fixture
def fake_ws() -> FakeWebSocket:
    return FakeWebSocket()
