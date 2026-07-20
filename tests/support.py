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


class FakeProviderWebSocket(FakeWebSocket):
    def __init__(
        self, query_params: dict[str, str] | None = None, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__()
        self.query_params = query_params or {}
        self.headers = headers or {}
        self.accepted = False
        self._incoming: list[dict[str, Any]] = []

    def queue(self, *messages: str) -> None:
        for message in messages:
            self._incoming.append({"type": "websocket.receive", "text": message})

    def queue_bytes(self, payload: bytes) -> None:
        self._incoming.append({"type": "websocket.receive", "bytes": payload})

    async def accept(self) -> None:
        self.accepted = True

    async def receive(self) -> dict[str, Any]:
        if self._incoming:
            return self._incoming.pop(0)

        return {"type": "websocket.disconnect", "code": 1005}
