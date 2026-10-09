from __future__ import annotations

from typing import Literal


class McpAccessError(Exception):
    def __init__(
        self,
        reason: Literal[
            "missing_token",
            "invalid_token",
            "insufficient_scope",
            "channel_forbidden",
            "verifier_unavailable",
            "invalid_session",
        ],
    ) -> None:
        self.reason = reason
        super().__init__(reason)

    @property
    def status_code(self) -> int:
        return {
            "missing_token": 401,
            "invalid_token": 401,
            "insufficient_scope": 403,
            "channel_forbidden": 404,
            "verifier_unavailable": 503,
            "invalid_session": 404,
        }[self.reason]
