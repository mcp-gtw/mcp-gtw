from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


class OAuthSigningKey:
    def __init__(self, path: str) -> None:
        target = Path(path)

        if not target.exists():
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            pem = key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )

            try:
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, "wb") as file:
                    file.write(pem)

        if target.stat().st_mode & 0o077:
            raise ValueError("OAuth signing key permissions must be private")

        self.private = serialization.load_pem_private_key(target.read_bytes(), password=None)

        if not isinstance(self.private, rsa.RSAPrivateKey) or self.private.key_size < 2048:
            raise ValueError("OAuth signing key must be RSA with at least 2048 bits")

        self.public = self.private.public_key()
        self.jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.public))
        self.kid = hashlib.sha256(json.dumps(self.jwk, sort_keys=True).encode()).hexdigest()[:32]
        self.jwk.update(kid=self.kid, alg="RS256", use="sig")

    def encode(self, claims: dict, typ: str = "at+jwt") -> str:
        return jwt.encode(
            claims, self.private, algorithm="RS256", headers={"kid": self.kid, "typ": typ}
        )

    def decode(self, token: str, issuer: str, audience: str, typ: str = "at+jwt") -> dict:
        header = jwt.get_unverified_header(token)

        if (
            header.get("typ") != typ
            or header.get("kid") != self.kid
            or any(k in header for k in ("jku", "x5u", "crit"))
        ):
            raise ValueError("Invalid token header")

        return jwt.decode(
            token,
            self.public,
            algorithms=["RS256"],
            issuer=issuer,
            audience=audience,
            options={"require": ["iss", "sub", "aud", "exp", "iat"]},
        )
