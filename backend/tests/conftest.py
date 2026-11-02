from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.db import dispose_engine
from app.main import create_app
from app.modules.auth.deps import get_token_verifier
from app.modules.auth.service import clear_provisioning_cache
from app.modules.auth.tokens import JWKSProvider, TokenVerifier
from tests.helpers import AUDIENCE, ISSUER, SigningKey, new_signing_key

ALLOWED_ORIGIN = "https://app.example.test"


def _reset_caches() -> None:
    get_settings.cache_clear()
    get_token_verifier.cache_clear()
    clear_provisioning_cache()
    dispose_engine()


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Tests never see the developer's .env: real environment variables win over the file."""
    monkeypatch.setenv("APP_ENV", "test")
    for name in (
        "DATABASE_URL",
        "REDIS_URL",
        "SECRETS_ENCRYPTION_KEY",
        "SUPABASE_URL",
        "SUPABASE_JWKS_URL",
        "SUPABASE_JWT_SECRET",
        "CORS_ORIGINS",
    ):
        monkeypatch.setenv(name, "")
    _reset_caches()
    yield
    _reset_caches()


@pytest.fixture
def signing_key() -> SigningKey:
    return new_signing_key()


@pytest.fixture
def verifier(signing_key: SigningKey) -> TokenVerifier:
    return TokenVerifier(
        audience=AUDIENCE,
        issuer=ISSUER,
        jwks=JWKSProvider(lambda: {"keys": [signing_key.jwk()]}),
        hs256_secret=None,
        leeway_seconds=0,
    )


@pytest.fixture
def app(verifier: TokenVerifier) -> FastAPI:
    application = create_app(Settings(_env_file=None, cors_origins=[ALLOWED_ORIGIN]))
    application.dependency_overrides[get_token_verifier] = lambda: verifier
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
