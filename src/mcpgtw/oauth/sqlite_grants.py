from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path

from mcpgtw.oauth.channel_grants import ChannelGrantStore
from mcpgtw.oauth.verified_principal import VerifiedPrincipal


class SqliteChannelGrantStore(ChannelGrantStore):
    """Durable grants for single-host deployments; database work runs off the event loop."""

    def __init__(self, path: str, maximum_grants: int = 10000) -> None:
        if not path or path == ":memory:" or maximum_grants < 1:
            raise ValueError("A durable database path and positive grant limit are required")

        self.path = str(Path(path))
        self.maximum_grants = maximum_grants

    def _query(
        self, operation: str, owner: str, channel_id: str, expires: int = 0
    ) -> frozenset[str]:
        Path(self.path).touch(mode=0o600, exist_ok=True)

        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS oauth_grants "
                "(owner TEXT, channel TEXT, expires INTEGER, "
                "PRIMARY KEY(owner, channel))"
            )
            db.execute("CREATE INDEX IF NOT EXISTS oauth_grants_channel ON oauth_grants(channel)")
            db.execute("CREATE INDEX IF NOT EXISTS oauth_grants_expiry ON oauth_grants(expires)")
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM oauth_grants WHERE expires <= ?", (time.time(),))

            if operation == "grant":
                exists = db.execute(
                    "SELECT 1 FROM oauth_grants WHERE owner=? AND channel=?", (owner, channel_id)
                ).fetchone()
                count = db.execute("SELECT count(*) FROM oauth_grants").fetchone()[0]

                if not exists and count >= self.maximum_grants:
                    raise ValueError("Grant capacity exceeded")

                db.execute(
                    "INSERT INTO oauth_grants VALUES (?, ?, ?) ON CONFLICT(owner, channel) "
                    "DO UPDATE SET expires=excluded.expires",
                    (owner, channel_id, expires),
                )
            elif operation == "revoke":
                db.execute(
                    "DELETE FROM oauth_grants WHERE owner=? AND channel=?", (owner, channel_id)
                )
            elif operation == "remove":
                db.execute("DELETE FROM oauth_grants WHERE channel=?", (channel_id,))

            return frozenset(
                row[0]
                for row in db.execute("SELECT channel FROM oauth_grants WHERE owner=?", (owner,))
            )

    async def channels(self, principal: VerifiedPrincipal) -> frozenset[str]:
        return await asyncio.to_thread(self._query, "get", principal.principal_id, "")

    async def grant(self, principal: VerifiedPrincipal, channel_id: str) -> None:
        await asyncio.to_thread(
            self._query, "grant", principal.principal_id, channel_id, principal.expires_at
        )

    async def revoke(self, principal: VerifiedPrincipal, channel_id: str) -> None:
        await asyncio.to_thread(self._query, "revoke", principal.principal_id, channel_id)

    async def remove_channel(self, channel_id: str) -> None:
        await asyncio.to_thread(self._query, "remove", "", channel_id)
