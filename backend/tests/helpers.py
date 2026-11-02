"""Locally signed tokens that look like Supabase access tokens. No network, no real keys."""

import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm

AUDIENCE = "authenticated"
ISSUER = "https://test-project.supabase.co/auth/v1"
HS256_SECRET = "test-only-shared-secret-that-is-long-enough-0123456789"


@dataclass(frozen=True)
class SigningKey:
    kid: str
    private_key: ec.EllipticCurvePrivateKey

    def jwk(self) -> dict[str, Any]:
        jwk: dict[str, Any] = ECAlgorithm.to_jwk(self.private_key.public_key(), as_dict=True)
        return {**jwk, "kid": self.kid, "alg": "ES256", "use": "sig"}


def new_signing_key(kid: str = "test-key-1") -> SigningKey:
    return SigningKey(kid=kid, private_key=ec.generate_private_key(ec.SECP256R1()))


def make_claims(
    user_id: UUID | None = None,
    *,
    email: str | None = None,
    expires_in: int = 3600,
    **overrides: Any,
) -> dict[str, Any]:
    now = int(time.time())
    subject = user_id or uuid4()
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": str(subject),
        "email": email or f"{subject}@example.test",
        "role": "authenticated",
        "is_anonymous": False,
        "iat": now,
        "exp": now + expires_in,
    }
    claims.update(overrides)
    return claims


def sign(claims: dict[str, Any], key: SigningKey, *, kid: str | None = None) -> str:
    return jwt.encode(claims, key.private_key, algorithm="ES256", headers={"kid": kid or key.kid})


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
