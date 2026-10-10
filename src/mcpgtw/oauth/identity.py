from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
import sqlite3
from abc import ABC, abstractmethod
from contextlib import closing
from pathlib import Path

from mcpgtw.errors import OAuthRateLimitError
from mcpgtw.oauth.access_error import McpAccessError


class IdentityAuthenticator(ABC):
    @property
    @abstractmethod
    def registration_enabled(self) -> bool: ...

    @abstractmethod
    async def authenticate(
        self, username: str, password: str, register: bool = False
    ) -> str | None: ...


class SqlitePasswordIdentity(IdentityAuthenticator):
    def __init__(
        self, path: str, registration_enabled: bool = False, maximum_users: int = 10000
    ) -> None:
        if not path or path == ":memory:" or maximum_users < 1:
            raise ValueError("Durable identity storage and finite capacity are required")

        self.path = path
        self._registration_enabled = registration_enabled
        self.maximum_users = maximum_users
        self._workers = asyncio.Semaphore(2)

    @property
    def registration_enabled(self) -> bool:
        return self._registration_enabled

    def _authenticate(self, username: str, password: str, register: bool) -> str | None:
        try:
            return self._password(username, password, register)
        except (sqlite3.Error, OSError) as exc:
            raise McpAccessError("verifier_unavailable") from exc

    def _password(self, username: str, password: str, register: bool) -> str | None:
        Path(self.path).touch(mode=0o600, exist_ok=True)

        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS oauth_users (username TEXT PRIMARY KEY"
                ", subject TEXT UNIQUE, salt BLOB, password BLOB)"
            )
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT subject,salt,password FROM oauth_users WHERE username=?", (username,)
            ).fetchone()
            salt = row[1] if row else secrets.token_bytes(32)
            hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600000)

            if register:
                if (
                    not self.registration_enabled
                    or row is not None
                    or db.execute("SELECT count(*) FROM oauth_users").fetchone()[0]
                    >= self.maximum_users
                ):
                    return None

                subject = secrets.token_urlsafe(32)
                db.execute(
                    "INSERT INTO oauth_users VALUES (?,?,?,?)", (username, subject, salt, hashed)
                )
                return subject

            return row[0] if row is not None and secrets.compare_digest(hashed, row[2]) else None

    async def authenticate(
        self, username: str, password: str, register: bool = False
    ) -> str | None:
        if not re.fullmatch(r"[a-zA-Z0-9_.-]{3,64}", username) or not 12 <= len(password) <= 256:
            return None

        if self._workers.locked():
            raise OAuthRateLimitError(1)

        async with self._workers:
            return await asyncio.to_thread(
                self._authenticate, username.casefold(), password, register
            )
