from __future__ import annotations

import pytest

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


def test_decode_message_accepts_objects() -> None:
    assert protocol.decode_message('{"type": "ping"}', 100) == {"type": "ping"}


def test_decode_message_rejects_non_objects() -> None:
    with pytest.raises(ValueError, match="must be a JSON object"):
        protocol.decode_message("[1, 2, 3]", 100)


def test_decode_message_rejects_excessive_depth() -> None:
    hostile = "[" * 5000 + "]" * 5000

    with pytest.raises(ValueError, match="maximum depth"):
        protocol.decode_message(hostile, 100)


def test_decode_message_ignores_brackets_inside_strings() -> None:
    # brackets and escaped quotes inside strings must not count towards nesting depth
    payload = protocol.decode_message(r'{"a": "[[[[ \" ]]]]"}', 2)
    assert payload == {"a": '[[[[ " ]]]]'}
