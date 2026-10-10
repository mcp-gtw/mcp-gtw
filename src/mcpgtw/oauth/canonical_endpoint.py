from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send


class CanonicalMcpEndpoint:
    def __init__(self, app: ASGIApp, mount_path: str) -> None:
        self.app = app
        self.mount_path = mount_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.app(
            {**scope, "root_path": scope.get("root_path", "") + self.mount_path}, receive, send
        )
