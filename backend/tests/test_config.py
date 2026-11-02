import pytest

from app.core.config import Settings


def test_secrets_have_no_hardcoded_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("DATABASE_URL", "SECRETS_ENCRYPTION_KEY", "SUPABASE_JWT_SECRET", "REDIS_URL"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)

    assert settings.database_url.get_secret_value() == ""
    assert settings.secrets_encryption_key.get_secret_value() == ""
    assert settings.supabase_jwt_secret.get_secret_value() == ""
    assert settings.redis_url.get_secret_value() == ""


def test_environment_overrides_defaults_and_secrets_stay_out_of_repr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:hunter2@h/db")

    settings = Settings(_env_file=None)

    assert settings.app_env == "production"
    assert settings.database_url.get_secret_value() == "postgresql+psycopg://u:hunter2@h/db"
    assert "hunter2" not in repr(settings)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("postgresql://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"),
        ("postgres://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"),
        ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("", ""),
    ],
)
def test_database_url_is_normalised_for_psycopg(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: str
) -> None:
    monkeypatch.setenv("DATABASE_URL", raw)

    assert Settings(_env_file=None).sqlalchemy_database_url == expected


def test_cors_origins_are_parsed_from_a_comma_separated_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000/, https://app.example.test ,")

    assert Settings(_env_file=None).cors_origins == [
        "http://localhost:3000",
        "https://app.example.test",
    ]


def test_empty_cors_origins_means_no_cross_origin_access(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "")

    assert Settings(_env_file=None).cors_origins == []


def test_issuer_and_jwks_url_derive_from_supabase_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co/")
    monkeypatch.setenv("SUPABASE_JWKS_URL", "")

    settings = Settings(_env_file=None)

    assert settings.jwt_issuer == "https://abc.supabase.co/auth/v1"
    assert settings.jwks_url == "https://abc.supabase.co/auth/v1/.well-known/jwks.json"


def test_jwks_url_can_be_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    monkeypatch.setenv("SUPABASE_JWKS_URL", "https://abc.supabase.co/auth/v1/jwks")

    assert Settings(_env_file=None).jwks_url == "https://abc.supabase.co/auth/v1/jwks"


def test_no_supabase_url_means_no_issuer_or_jwks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_JWKS_URL", "")

    settings = Settings(_env_file=None)

    assert settings.jwt_issuer is None
    assert settings.jwks_url is None
