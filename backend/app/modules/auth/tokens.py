"""Supabase access-token verification.

Asymmetric (ES256/RS256) tokens are checked against the project's public JWKS; HS256 tokens only
when a legacy shared secret is configured. The accepted algorithms come from the server's own
configuration, never from what the token claims, which closes algorithm-confusion attacks.
"""

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import httpx
import jwt
import structlog
from jwt import PyJWK

from app.core.config import Settings

log = structlog.get_logger(__name__)

ASYMMETRIC_ALGORITHMS = ("ES256", "RS256")
HMAC_ALGORITHM = "HS256"
MAX_TOKEN_LENGTH = 8192
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class TokenError(Exception):
    """The token must be rejected. ``code`` is safe for clients, ``reason`` is for logs only."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


class AuthUnavailableError(Exception):
    """Tokens cannot be checked now (unconfigured, or keys unreachable); not the client's fault."""


@dataclass(frozen=True)
class AuthenticatedUser:
    """Identity taken from a verified token. This is the only trusted source of ``user_id``."""

    id: UUID
    email: str
    claims: Mapping[str, Any] = field(repr=False)


def user_from_claims(claims: Mapping[str, Any]) -> AuthenticatedUser:
    if claims.get("role") != "authenticated":
        raise TokenError("invalid_token", "role claim is not 'authenticated'")
    if claims.get("is_anonymous") is True:
        raise TokenError("invalid_token", "anonymous sessions are not accepted")
    try:
        user_id = UUID(str(claims["sub"]))
    except (KeyError, ValueError) as exc:
        raise TokenError("invalid_token", "sub claim is not a UUID") from exc
    email = claims.get("email")
    if not isinstance(email, str) or not email:
        raise TokenError("invalid_token", "email claim is missing")
    return AuthenticatedUser(id=user_id, email=email, claims=dict(claims))


JWKSFetcher = Callable[[], Mapping[str, Any]]


def http_jwks_fetcher(url: str, *, timeout: float = 5.0) -> JWKSFetcher:
    def fetch() -> Mapping[str, Any]:
        parsed = urlparse(url)
        if parsed.scheme != "https" and parsed.hostname not in _LOCAL_HOSTS:
            raise ValueError("JWKS URL must use https")
        response = httpx.get(
            url, timeout=timeout, follow_redirects=False, headers={"Accept": "application/json"}
        )
        response.raise_for_status()
        document = response.json()
        if not isinstance(document, dict):
            raise ValueError("JWKS response is not a JSON object")
        return document

    return fetch


class JWKSProvider:
    """Cached signing keys, safe to share between request threads.

    An unknown ``kid`` triggers at most one refetch per ``min_refresh_seconds``, so a stream of
    forged tokens cannot turn into a stream of requests to the identity provider. If a refresh
    fails while older keys are cached, the cached keys keep working.
    """

    def __init__(
        self,
        fetch: JWKSFetcher,
        *,
        ttl_seconds: float = 600.0,
        min_refresh_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetch = fetch
        self._ttl = ttl_seconds
        self._min_refresh = min_refresh_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._keys: dict[str, PyJWK] = {}
        self._loaded = False
        self._fetched_at = 0.0
        self._last_attempt: float | None = None

    def get_key(self, kid: str) -> PyJWK | None:
        with self._lock:
            now = self._clock()
            if not self._loaded:
                if not self._may_attempt(now):
                    raise AuthUnavailableError("signing keys are unavailable")
                self._refresh(now)
            elif now - self._fetched_at > self._ttl or (
                kid not in self._keys and self._may_attempt(now)
            ):
                self._refresh(now)
            return self._keys.get(kid)

    def _may_attempt(self, now: float) -> bool:
        return self._last_attempt is None or now - self._last_attempt >= self._min_refresh

    def _refresh(self, now: float) -> None:
        self._last_attempt = now
        try:
            keys = _parse_jwks(self._fetch())
        except Exception as exc:
            log.warning("jwks_refresh_failed", error=type(exc).__name__, detail=str(exc))
            if not self._loaded:
                raise AuthUnavailableError("signing keys are unavailable") from exc
            return
        self._keys = keys
        self._loaded = True
        self._fetched_at = now


def _parse_jwks(document: Mapping[str, Any]) -> dict[str, PyJWK]:
    raw_keys = document.get("keys")
    if not isinstance(raw_keys, list):
        raise ValueError("JWKS document has no 'keys' list")
    keys: dict[str, PyJWK] = {}
    for raw in raw_keys:
        if not isinstance(raw, dict):
            continue
        kid = raw.get("kid")
        if not isinstance(kid, str) or not kid or raw.get("use") not in (None, "sig"):
            continue
        try:
            keys[kid] = PyJWK(raw)
        except jwt.PyJWTError:
            log.warning("jwks_key_skipped", kid=kid)
    return keys


class TokenVerifier:
    def __init__(
        self,
        *,
        audience: str,
        issuer: str | None,
        jwks: JWKSProvider | None,
        hs256_secret: str | None,
        leeway_seconds: int = 10,
    ) -> None:
        self._audience = audience
        self._issuer = issuer
        self._jwks = jwks
        self._hs256_secret = hs256_secret
        self._leeway = leeway_seconds

    def verify(self, token: str) -> dict[str, Any]:
        """Return the verified claims, or raise TokenError / AuthUnavailableError."""
        if self._jwks is None and not self._hs256_secret:
            raise AuthUnavailableError("authentication is not configured")
        if not token or len(token) > MAX_TOKEN_LENGTH:
            raise TokenError("invalid_token", "empty or oversized token")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise TokenError("invalid_token", "malformed token") from exc

        algorithm = header.get("alg")
        key: Any
        if algorithm == HMAC_ALGORITHM:
            if not self._hs256_secret:
                raise TokenError("invalid_token", "HS256 tokens are not accepted")
            key = self._hs256_secret
        elif algorithm in ASYMMETRIC_ALGORITHMS:
            if self._jwks is None:
                raise TokenError("invalid_token", "asymmetric tokens are not accepted")
            kid = header.get("kid")
            if not isinstance(kid, str) or not kid:
                raise TokenError("invalid_token", "token has no key id")
            jwk = self._jwks.get_key(kid)
            if jwk is None:
                raise TokenError("invalid_token", "unknown signing key")
            if jwk.algorithm_name != algorithm:
                raise TokenError("invalid_token", "key does not match token algorithm")
            key = jwk.key
        else:
            raise TokenError("invalid_token", "unsupported algorithm")

        required = ["exp", "sub", "aud"] + (["iss"] if self._issuer else [])
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key=key,
                algorithms=[str(algorithm)],
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway,
                options={"require": required},
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenError("token_expired", "token expired") from exc
        except jwt.PyJWTError as exc:
            raise TokenError("invalid_token", type(exc).__name__) from exc
        return claims

    def verify_user(self, token: str) -> AuthenticatedUser:
        return user_from_claims(self.verify(token))


def build_token_verifier(settings: Settings) -> TokenVerifier:
    jwks_url = settings.jwks_url
    return TokenVerifier(
        audience=settings.jwt_audience,
        issuer=settings.jwt_issuer,
        jwks=JWKSProvider(http_jwks_fetcher(jwks_url)) if jwks_url else None,
        hs256_secret=settings.supabase_jwt_secret.get_secret_value() or None,
        leeway_seconds=settings.jwt_leeway_seconds,
    )
