from __future__ import annotations

from typing import Any


class FakeWebSocket:
    def __init__(self, *, fail_send: bool = False) -> None:
        self.messages: list[dict[str, Any]] = []
        self.closed = False
        self.close_code: int | None = None
        self.fail_send = fail_send

    async def send_json(self, data: Any) -> None:
        if self.fail_send:
            raise RuntimeError("socket is closing")

        self.messages.append(data)

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed = True
        self.close_code = code

    def last(self, message_type: str) -> dict[str, Any]:
        return next(
            message for message in reversed(self.messages) if message["type"] == message_type
        )
