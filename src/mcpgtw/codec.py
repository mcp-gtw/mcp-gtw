from __future__ import annotations

import json
import math
from abc import ABC, abstractmethod
from typing import Any


def _reject_constant(value: str) -> None:
    raise ValueError(f"Non-standard JSON value is not allowed: {value}")


def _parse_float(value: str) -> float:
    number = float(value)

    if not math.isfinite(number):
        raise ValueError(f"Non-finite JSON number is not allowed: {value}")

    return number


class ProtocolCodec(ABC):
    """Parses an untrusted provider frame into a message object.

    Swap the implementation to speak a different wire format (binary, compressed, versioned).
    The default guards against hostile payloads before parsing.
    """

    @abstractmethod
    def decode(self, text: str | None) -> dict[str, Any]: ...


class JsonProtocolCodec(ProtocolCodec):
    """The default codec: depth-bounded JSON decoded into an object."""

    def __init__(self, maximum_depth: int) -> None:
        self._maximum_depth = maximum_depth

    def decode(self, text: str | None) -> dict[str, Any]:
        if text is None:
            raise ValueError("Only text messages are supported")

        if self._exceeds_depth(text):
            raise ValueError(f"Message nesting exceeds the maximum depth of {self._maximum_depth}")

        payload = json.loads(text, parse_constant=_reject_constant, parse_float=_parse_float)

        if not isinstance(payload, dict):
            raise ValueError("Message must be a JSON object")

        return payload

    def _exceeds_depth(self, text: str) -> bool:
        depth = 0
        in_string = False
        escaped = False

        for character in text:
            if in_string:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    in_string = False

                continue

            if character == '"':
                in_string = True
            elif character in "[{":
                depth += 1

                if depth > self._maximum_depth:
                    return True
            elif character in "]}":
                depth -= 1

        return False
