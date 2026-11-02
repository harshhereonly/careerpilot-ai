import base64
import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from app.core.config import Settings
from app.modules.auth.tokens import (
    AuthUnavailableError,
    JWKSProvider,
    TokenError,
    TokenVerifier,
    build_token_verifier,
    http_jwks_fetcher,
    user_from_claims,
)
from tests.helpers import (
    AUDIENCE,
    HS256_SECRET,
    ISSUER,
    SigningKey,
    make_claims,
    new_signing_key,
    sign,
)


def hs256_verifier(
    secret: str | None = HS256_SECRET, jwks: JWKSProvider | None = None
) -> TokenVerifier:
    return TokenVerifier(
        audience=AUDIENCE, issuer=ISSUER, jwks=jwks, hs256_secret=secret, leeway_seconds=0
    )


def assert_rejected(verifier: TokenVerifier, token: str, code: str = "invalid_token") -> None:
    with pytest.raises(TokenError) as caught:
        verifier.verify_user(token)
    assert caught.value.code == code


class TestValidTokens:
    def test_es256_token_is_accepted(
        self, verifier: TokenVerifier, signing_key: SigningKey
    ) -> None:
        user_id = uuid4()
        token = sign(make_claims(user_id, email="ada@example.test"), signing_key)

        user = verifier.verify_user(token)

        assert user.id == user_id
        assert user.email == "ada@example.test"
        assert user.claims["role"] == "authenticated"

    def test_rs256_token_is_accepted(self) -> None:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk: dict[str, Any] = RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
        jwk.update(kid="rsa-1", alg="RS256", use="sig")
        verifier = hs256_verifier(secret=None, jwks=JWKSProvider(lambda: {"keys": [jwk]}))
        token = jwt.encode(make_claims(), private_key, algorithm="RS256", headers={"kid": "rsa-1"})

        assert verifier.verify_user(token).email.endswith("@example.test")

    def test_legacy_hs256_token_is_accepted_only_with_the_configured_secret(self) -> None:
        token = jwt.encode(make_claims(), HS256_SECRET, algorithm="HS256")

        assert hs256_verifier().verify_user(token).email.endswith("@example.test")

    def test_user_repr_does_not_contain_claims(
        self, verifier: TokenVerifier, signing_key: SigningKey
    ) -> None:
        user = verifier.verify_user(sign(make_claims(session_id="sensitive"), signing_key))

        assert "sensitive" not in repr(user)


class TestRejectedTokens:
    def test_expired(self, verifier: TokenVerifier, signing_key: SigningKey) -> None:
        assert_rejected(verifier, sign(make_claims(expires_in=-60), signing_key), "token_expired")

    def test_wrong_audience(self, verifier: TokenVerifier, signing_key: SigningKey) -> None:
        assert_rejected(verifier, sign(make_claims(aud="anon"), signing_key))

    def test_wrong_issuer(self, verifier: TokenVerifier, signing_key: SigningKey) -> None:
        issuer = "https://other-project.supabase.co/auth/v1"
        assert_rejected(verifier, sign(make_claims(iss=issuer), signing_key))

    def test_not_yet_valid(self, verifier: TokenVerifier, signing_key: SigningKey) -> None:
        claims = make_claims()
        claims["nbf"] = claims["iat"] + 3600
        assert_rejected(verifier, sign(claims, signing_key))

    @pytest.mark.parametrize("claim", ["exp", "sub", "aud", "iss"])
    def test_missing_required_claim(
        self, verifier: TokenVerifier, signing_key: SigningKey, claim: str
    ) -> None:
        claims = make_claims()
        del claims[claim]
        assert_rejected(verifier, sign(claims, signing_key))

    def test_signed_by_an_untrusted_key_with_a_trusted_kid(
        self, verifier: TokenVerifier, signing_key: SigningKey
    ) -> None:
        attacker = new_signing_key(kid=signing_key.kid)
        assert_rejected(verifier, sign(make_claims(), attacker))

    def test_tampered_payload(self, verifier: TokenVerifier, signing_key: SigningKey) -> None:
        header, _payload, signature = sign(make_claims(), signing_key).split(".")
        forged = (
            base64.urlsafe_b64encode(json.dumps(make_claims(uuid4())).encode())
            .rstrip(b"=")
            .decode()
        )
        assert_rejected(verifier, f"{header}.{forged}.{signature}")

    def test_alg_none_is_rejected(self, verifier: TokenVerifier) -> None:
        assert_rejected(verifier, jwt.encode(make_claims(), key="", algorithm="none"))

    def test_hs256_is_rejected_when_no_shared_secret_is_configured(
        self, verifier: TokenVerifier
    ) -> None:
        token = jwt.encode(make_claims(), HS256_SECRET, algorithm="HS256")
        assert_rejected(verifier, token)

    def test_algorithm_confusion_public_key_used_as_hmac_secret(
        self, signing_key: SigningKey
    ) -> None:
        """The classic attack: sign with HS256 using the *public* key as the shared secret."""
        public_pem = signing_key.private_key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        forged = _hs256_token_with_raw_secret(make_claims(), public_pem, kid=signing_key.kid)
        jwks = JWKSProvider(lambda: {"keys": [signing_key.jwk()]})

        assert_rejected(hs256_verifier(secret=None, jwks=jwks), forged)
        assert_rejected(hs256_verifier(secret=HS256_SECRET, jwks=jwks), forged)

    def test_asymmetric_token_is_rejected_when_only_a_shared_secret_is_configured(
        self, signing_key: SigningKey
    ) -> None:
        assert_rejected(hs256_verifier(), sign(make_claims(), signing_key))

    def test_wrong_shared_secret(self) -> None:
        token = jwt.encode(
            make_claims(), "a-different-secret-that-is-also-long-enough", algorithm="HS256"
        )
        assert_rejected(hs256_verifier(), token)

    def test_unknown_kid(self, verifier: TokenVerifier, signing_key: SigningKey) -> None:
        assert_rejected(verifier, sign(make_claims(), signing_key, kid="unknown"))

    def test_missing_kid(self, verifier: TokenVerifier, signing_key: SigningKey) -> None:
        token = jwt.encode(make_claims(), signing_key.private_key, algorithm="ES256")
        assert_rejected(verifier, token)

    @pytest.mark.parametrize("token", ["", "garbage", "a.b.c", "....", "x" * 9000])
    def test_malformed(self, verifier: TokenVerifier, token: str) -> None:
        assert_rejected(verifier, token)


class TestClaimRules:
    def test_service_role_token_is_not_a_user(
        self, verifier: TokenVerifier, signing_key: SigningKey
    ) -> None:
        assert_rejected(verifier, sign(make_claims(role="service_role"), signing_key))

    def test_anon_role_is_not_a_user(
        self, verifier: TokenVerifier, signing_key: SigningKey
    ) -> None:
        assert_rejected(verifier, sign(make_claims(role="anon"), signing_key))

    def test_anonymous_sign_in_is_not_accepted(
        self, verifier: TokenVerifier, signing_key: SigningKey
    ) -> None:
        assert_rejected(verifier, sign(make_claims(is_anonymous=True), signing_key))

    def test_sub_must_be_a_uuid(self, verifier: TokenVerifier, signing_key: SigningKey) -> None:
        assert_rejected(verifier, sign(make_claims(sub="not-a-uuid"), signing_key))

    def test_email_is_required(self, verifier: TokenVerifier, signing_key: SigningKey) -> None:
        claims = make_claims()
        del claims["email"]
        assert_rejected(verifier, sign(claims, signing_key))

    def test_user_from_claims_requires_sub(self) -> None:
        claims = make_claims()
        del claims["sub"]
        with pytest.raises(TokenError):
            user_from_claims(claims)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class CountingFetcher:
    def __init__(self, *keys: SigningKey) -> None:
        self.keys = list(keys)
        self.calls = 0
        self.fail = False

    def __call__(self) -> Mapping[str, Any]:
        self.calls += 1
        if self.fail:
            raise httpx.ConnectError("identity provider is down")
        return {"keys": [key.jwk() for key in self.keys]}


class TestJWKSProvider:
    def test_keys_are_cached(self, signing_key: SigningKey) -> None:
        fetcher = CountingFetcher(signing_key)
        provider = JWKSProvider(fetcher)

        assert provider.get_key(signing_key.kid) is not None
        assert provider.get_key(signing_key.kid) is not None
        assert fetcher.calls == 1

    def test_keys_are_refreshed_after_the_ttl(self, signing_key: SigningKey) -> None:
        fetcher = CountingFetcher(signing_key)
        clock = FakeClock()
        provider = JWKSProvider(fetcher, ttl_seconds=600, clock=clock)
        provider.get_key(signing_key.kid)

        clock.now += 601
        provider.get_key(signing_key.kid)

        assert fetcher.calls == 2

    def test_forged_kids_cannot_flood_the_identity_provider(self, signing_key: SigningKey) -> None:
        fetcher = CountingFetcher(signing_key)
        clock = FakeClock()
        provider = JWKSProvider(fetcher, min_refresh_seconds=30, clock=clock)
        provider.get_key(signing_key.kid)

        for attempt in range(100):
            assert provider.get_key(f"forged-{attempt}") is None

        assert fetcher.calls == 1  # the refresh budget was already used by the initial load
        clock.now += 31
        assert provider.get_key("forged-again") is None
        assert fetcher.calls == 2

    def test_key_rotation_is_picked_up_on_an_unknown_kid(self, signing_key: SigningKey) -> None:
        rotated = new_signing_key(kid="rotated")
        fetcher = CountingFetcher(signing_key)
        clock = FakeClock()
        provider = JWKSProvider(fetcher, min_refresh_seconds=30, clock=clock)
        provider.get_key(signing_key.kid)

        fetcher.keys.append(rotated)
        clock.now += 31

        assert provider.get_key("rotated") is not None

    def test_first_load_failure_is_unavailable_not_invalid(self, signing_key: SigningKey) -> None:
        fetcher = CountingFetcher(signing_key)
        fetcher.fail = True
        provider = JWKSProvider(fetcher)

        with pytest.raises(AuthUnavailableError):
            provider.get_key(signing_key.kid)

    def test_failed_first_load_is_not_retried_on_every_request(
        self, signing_key: SigningKey
    ) -> None:
        fetcher = CountingFetcher(signing_key)
        fetcher.fail = True
        clock = FakeClock()
        provider = JWKSProvider(fetcher, min_refresh_seconds=30, clock=clock)

        for _ in range(5):
            with pytest.raises(AuthUnavailableError):
                provider.get_key(signing_key.kid)
        assert fetcher.calls == 1

        fetcher.fail = False
        clock.now += 31
        assert provider.get_key(signing_key.kid) is not None

    def test_cached_keys_survive_a_failed_refresh(self, signing_key: SigningKey) -> None:
        fetcher = CountingFetcher(signing_key)
        clock = FakeClock()
        provider = JWKSProvider(fetcher, ttl_seconds=600, clock=clock)
        provider.get_key(signing_key.kid)

        fetcher.fail = True
        clock.now += 601

        assert provider.get_key(signing_key.kid) is not None

    def test_empty_key_set_is_a_normal_answer(self) -> None:
        """Supabase returns no keys for projects still on the legacy shared secret."""
        provider = JWKSProvider(lambda: {"keys": []})

        assert provider.get_key("anything") is None

    def test_malformed_documents_are_treated_as_a_failed_fetch(self) -> None:
        provider = JWKSProvider(lambda: {"not_keys": []})

        with pytest.raises(AuthUnavailableError):
            provider.get_key("anything")

    def test_unusable_and_non_signing_keys_are_skipped(self, signing_key: SigningKey) -> None:
        document = {
            "keys": [
                "not-an-object",
                {"kty": "EC"},  # no kid
                {"kid": "bad", "kty": "nonsense"},
                {**signing_key.jwk(), "kid": "encryption", "use": "enc"},
                signing_key.jwk(),
            ]
        }
        provider = JWKSProvider(lambda: document)

        assert provider.get_key(signing_key.kid) is not None
        assert provider.get_key("bad") is None
        assert provider.get_key("encryption") is None


class TestConfiguration:
    def test_no_configuration_is_unavailable_not_invalid(self) -> None:
        verifier = TokenVerifier(audience=AUDIENCE, issuer=None, jwks=None, hs256_secret=None)

        with pytest.raises(AuthUnavailableError):
            verifier.verify("anything")

    def test_unreachable_keys_are_unavailable(self, signing_key: SigningKey) -> None:
        fetcher = CountingFetcher(signing_key)
        fetcher.fail = True
        verifier = hs256_verifier(secret=None, jwks=JWKSProvider(fetcher))

        with pytest.raises(AuthUnavailableError):
            verifier.verify(sign(make_claims(), signing_key))

    def test_build_uses_jwks_and_issuer_from_settings(
        self, monkeypatch: pytest.MonkeyPatch, signing_key: SigningKey
    ) -> None:
        monkeypatch.setenv("SUPABASE_URL", "https://test-project.supabase.co")
        requested: list[str] = []

        def fake_get(url: str, **_kwargs: Any) -> httpx.Response:
            requested.append(url)
            return httpx.Response(
                200, json={"keys": [signing_key.jwk()]}, request=httpx.Request("GET", url)
            )

        monkeypatch.setattr(httpx, "get", fake_get)
        verifier = build_token_verifier(Settings(_env_file=None))

        assert verifier.verify_user(sign(make_claims(), signing_key))
        assert requested == ["https://test-project.supabase.co/auth/v1/.well-known/jwks.json"]

    def test_plain_http_jwks_is_refused_except_for_localhost(self) -> None:
        with pytest.raises(ValueError, match="https"):
            http_jwks_fetcher("http://example.test/jwks.json")()

    def test_fetcher_rejects_a_non_object_document(self, monkeypatch: pytest.MonkeyPatch) -> None:
        url = "https://example.test/jwks.json"
        monkeypatch.setattr(
            httpx,
            "get",
            lambda *_a, **_k: httpx.Response(200, json=[1], request=httpx.Request("GET", url)),
        )
        with pytest.raises(ValueError, match="object"):
            http_jwks_fetcher(url)()


def _hs256_token_with_raw_secret(claims: dict[str, Any], secret: bytes, *, kid: str) -> str:
    """Hand-rolled: PyJWT refuses to sign HS256 with PEM data, which is the attacker's move."""

    def encode(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    header = encode(json.dumps({"alg": "HS256", "typ": "JWT", "kid": kid}).encode())
    payload = encode(json.dumps(claims).encode())
    signature = hmac.new(secret, f"{header}.{payload}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload}.{encode(signature)}"
