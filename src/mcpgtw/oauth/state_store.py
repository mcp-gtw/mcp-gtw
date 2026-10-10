from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import sqlite3
import time
from abc import ABC, abstractmethod
from contextlib import closing
from pathlib import Path
from typing import Any

from mcpgtw.oauth.access_error import McpAccessError


class OAuthStateStore(ABC):
    @abstractmethod
    async def put(self, kind: str, payload: dict, ttl: int, secret: str = "") -> str: ...

    @abstractmethod
    async def get(self, kind: str, secret: str, consume: bool = False) -> dict | None: ...

    @abstractmethod
    async def remove(self, kind: str, secret: str) -> None: ...

    @abstractmethod
    async def rotate(
        self, secret: str, client: str, resource: str, scopes: set[str]
    ) -> tuple[dict, str] | None: ...

    @abstractmethod
    async def revoke_subject(self, subject: str) -> None: ...


class SqliteOAuthStateStore(OAuthStateStore):
    def __init__(self, path: str, maximum_records: int = 50000) -> None:
        if not path or path == ":memory:" or maximum_records < 1:
            raise ValueError("Durable OAuth storage and finite capacity are required")

        self.path = path
        self.maximum_records = maximum_records

    @staticmethod
    def digest(secret: str) -> str:
        return hashlib.sha256(secret.encode()).hexdigest()

    def _query(self, operation: str, kind: str, secret: str, payload: dict, ttl: int) -> Any:
        try:
            return self._execute(operation, kind, secret, payload, ttl)
        except (sqlite3.Error, OSError) as exc:
            raise McpAccessError("verifier_unavailable") from exc

    def _execute(self, operation: str, kind: str, secret: str, payload: dict, ttl: int) -> Any:
        Path(self.path).touch(mode=0o600, exist_ok=True)

        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS oauth_state (kind TEXT, key TEXT, payl"
                "oad TEXT, expires REAL, PRIMARY KEY(kind,key))"
            )
            db.execute("CREATE INDEX IF NOT EXISTS oauth_state_expiry ON oauth_state(expires)")
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM oauth_state WHERE expires <= ?", (time.time(),))
            key = self.digest(secret)
            row = db.execute(
                "SELECT payload,expires FROM oauth_state WHERE kind=? AND key=?", (kind, key)
            ).fetchone()

            if operation == "put":
                if (
                    row is None
                    and db.execute("SELECT count(*) FROM oauth_state").fetchone()[0]
                    >= self.maximum_records
                ):
                    raise ValueError("OAuth storage capacity exceeded")

                db.execute(
                    (
                        "INSERT INTO oauth_state VALUES (?,?,?,?) ON CONFLICT(kind,key) DO"
                        " UPDATE SET payload=excluded.payload,expires=excluded.expires"
                    ),
                    (kind, key, json.dumps(payload), time.time() + ttl),
                )
            elif operation == "remove" or operation == "consume":
                db.execute("DELETE FROM oauth_state WHERE kind=? AND key=?", (kind, key))
            elif operation == "subject":
                families = db.execute(
                    "SELECT kind,key,payload FROM oauth_state WHERE kind IN ('family',"
                    "'login','code')"
                ).fetchall()

                for record_kind, family_key, raw in families:
                    if json.loads(raw)["subject"] == secret:
                        db.execute(
                            "DELETE FROM oauth_state WHERE kind=? AND key=?",
                            (record_kind, family_key),
                        )
            elif operation == "rotate":
                if row is None:
                    return None

                current = json.loads(row[0])

                if (
                    current["client_id"] != payload["client_id"]
                    or current["resource"] != payload["resource"]
                    or not set(payload["scopes"]) <= set(current["scopes"])
                ):
                    return None

                family_key = self.digest(current["family"])
                family = db.execute(
                    "SELECT payload FROM oauth_state WHERE kind='family' AND key=?", (family_key,)
                ).fetchone()

                if current["used"]:
                    db.execute(
                        "DELETE FROM oauth_state WHERE kind='family' AND key=?", (family_key,)
                    )
                    return None

                if family is None:
                    return None

                if (
                    db.execute("SELECT count(*) FROM oauth_state").fetchone()[0]
                    >= self.maximum_records
                ):
                    raise ValueError("OAuth storage capacity exceeded")

                successor = secrets.token_urlsafe(48)
                current["used"] = True
                db.execute(
                    "UPDATE oauth_state SET payload=? WHERE kind='refresh' AND key=?",
                    (json.dumps(current), key),
                )
                current["used"] = False
                current["scopes"] = payload["scopes"]
                db.execute(
                    "INSERT INTO oauth_state VALUES ('refresh',?,?,?)",
                    (self.digest(successor), json.dumps(current), row[1]),
                )
                return current, successor

            return json.loads(row[0]) if row is not None else None

    async def put(self, kind: str, payload: dict, ttl: int, secret: str = "") -> str:
        secret = secret or secrets.token_urlsafe(48)
        await asyncio.to_thread(self._query, "put", kind, secret, payload, ttl)
        return secret

    async def get(self, kind: str, secret: str, consume: bool = False) -> dict | None:
        return await asyncio.to_thread(
            self._query, "consume" if consume else "get", kind, secret, {}, 0
        )

    async def remove(self, kind: str, secret: str) -> None:
        await asyncio.to_thread(self._query, "remove", kind, secret, {}, 0)

    async def rotate(
        self, secret: str, client: str, resource: str, scopes: set[str]
    ) -> tuple[dict, str] | None:
        return await asyncio.to_thread(
            self._query,
            "rotate",
            "refresh",
            secret,
            {"client_id": client, "resource": resource, "scopes": sorted(scopes)},
            0,
        )

    async def revoke_subject(self, subject: str) -> None:
        await asyncio.to_thread(self._query, "subject", "family", subject, {}, 0)
