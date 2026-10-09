from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VerifiedPrincipal:
    issuer: str
    subject: str
    client_id: str
    scopes: frozenset[str]
    expires_at: int

    @property
    def principal_id(self) -> str:
        identity = json.dumps([self.issuer, self.subject, self.client_id, "oauth"])
        return hashlib.sha256(identity.encode()).hexdigest()
