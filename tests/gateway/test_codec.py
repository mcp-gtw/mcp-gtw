from __future__ import annotations

import pytest

from mcp_gtw.codec import JsonProtocolCodec, ProtocolCodec


def test_decode_accepts_objects() -> None:
    assert JsonProtocolCodec(100).decode('{"type": "ping"}') == {"type": "ping"}


def test_decode_rejects_missing_text() -> None:
    with pytest.raises(ValueError, match="Only text messages are supported"):
        JsonProtocolCodec(100).decode(None)


def test_decode_rejects_non_objects() -> None:
    with pytest.raises(ValueError, match="must be a JSON object"):
        JsonProtocolCodec(100).decode("[1, 2, 3]")


def test_decode_rejects_excessive_depth() -> None:
    hostile = "[" * 5000 + "]" * 5000

    with pytest.raises(ValueError, match="maximum depth"):
        JsonProtocolCodec(100).decode(hostile)


def test_decode_ignores_brackets_inside_strings() -> None:
    payload = JsonProtocolCodec(2).decode(r'{"a": "[[[[ \" ]]]]"}')
    assert payload == {"a": '[[[[ " ]]]]'}


@pytest.mark.parametrize("text", ['{"x": NaN}', '{"x": Infinity}', '{"x": -Infinity}'])
def test_decode_rejects_non_standard_constants(text: str) -> None:
    with pytest.raises(ValueError, match="Non-standard JSON value"):
        JsonProtocolCodec(100).decode(text)


@pytest.mark.parametrize("text", ['{"x": 1e999}', '{"x": -1e999}'])
def test_decode_rejects_overflowing_floats(text: str) -> None:
    with pytest.raises(ValueError, match="Non-finite JSON number"):
        JsonProtocolCodec(100).decode(text)


def test_decode_accepts_finite_floats() -> None:
    assert JsonProtocolCodec(100).decode('{"x": 3.14}') == {"x": 3.14}


def test_protocol_codec_is_abstract() -> None:
    with pytest.raises(TypeError):
        ProtocolCodec()  # type: ignore[abstract]
