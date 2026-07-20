from __future__ import annotations

from mcp_gtw import protocol


def test_message_builders() -> None:
    assert protocol.hello_ack("v1", "chan") == {
        "type": "hello.ack",
        "protocolVersion": "v1",
        "channelId": "chan",
    }
    assert protocol.ack("tools", 3) == {"type": "ack", "registry": "tools", "count": 3}
    assert protocol.request("rid", "tools/call", {"name": "move"}) == {
        "type": "request",
        "requestId": "rid",
        "method": "tools/call",
        "params": {"name": "move"},
    }
    assert protocol.cancel("rid", "timeout") == {
        "type": "cancel",
        "requestId": "rid",
        "reason": "timeout",
    }
    assert protocol.protocol_error("boom") == {"type": "protocol.error", "message": "boom"}
