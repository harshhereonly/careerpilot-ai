"""The readiness checker: it must report honestly and never print a secret."""

import getpass
from typing import Any

import httpx
import jwt
import pytest

from app.cli.verify_supabase import (
    Report,
    check_configuration,
    check_real_token,
    check_signing_keys,
)
from app.core.config import Settings
from tests.helpers import HS256_SECRET, SigningKey, make_claims

PASSWORD = "not-a-real-password-S3cret"
SUPABASE_URL = "https://test-project.supabase.co"


def settings_with(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)


def serve_jwks(monkeypatch: pytest.MonkeyPatch, document: Any, status: int = 200) -> None:
    def fake_get(url: str, **_kwargs: Any) -> httpx.Response:
        return httpx.Response(status, json=document, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)


class TestConfiguration:
    def test_missing_values_fail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        report = Report()

        assert check_configuration(settings_with(monkeypatch), report) is False
        assert report.failed

    def test_complete_configuration_passes_without_printing_the_password(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        settings = settings_with(
            monkeypatch,
            DATABASE_URL=f"postgresql://postgres.abc:{PASSWORD}@pooler.supabase.com:5432/postgres",
            SUPABASE_URL=SUPABASE_URL,
            SECRETS_ENCRYPTION_KEY="configured",
        )
        report = Report()

        assert check_configuration(settings, report) is True
        output = capsys.readouterr().out
        assert not report.failed
        assert "pooler.supabase.com:5432" in output
        assert PASSWORD not in output

    def test_transaction_pooler_gets_a_warning(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        settings = settings_with(
            monkeypatch,
            DATABASE_URL=f"postgresql://u:{PASSWORD}@pooler.supabase.com:6543/postgres",
            SUPABASE_URL=SUPABASE_URL,
        )

        check_configuration(settings, Report())

        assert "transaction pooler" in capsys.readouterr().out


class TestSigningKeys:
    def test_asymmetric_keys_pass(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        signing_key: SigningKey,
    ) -> None:
        serve_jwks(monkeypatch, {"keys": [signing_key.jwk()]})
        report = Report()

        check_signing_keys(settings_with(monkeypatch, SUPABASE_URL=SUPABASE_URL), report)

        assert not report.failed
        assert "ES256" in capsys.readouterr().out

    def test_no_keys_and_no_secret_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        serve_jwks(monkeypatch, {"keys": []})
        report = Report()

        check_signing_keys(settings_with(monkeypatch, SUPABASE_URL=SUPABASE_URL), report)

        assert report.failed

    def test_no_keys_with_legacy_secret_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        serve_jwks(monkeypatch, {"keys": []})
        report = Report()
        settings = settings_with(
            monkeypatch, SUPABASE_URL=SUPABASE_URL, SUPABASE_JWT_SECRET=HS256_SECRET
        )

        check_signing_keys(settings, report)

        assert not report.failed

    def test_wrong_url_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        serve_jwks(monkeypatch, {"error": "not found"}, status=404)
        report = Report()

        check_signing_keys(settings_with(monkeypatch, SUPABASE_URL=SUPABASE_URL), report)

        assert report.failed


class TestRealToken:
    def run(
        self, monkeypatch: pytest.MonkeyPatch, token: str, *, claims_ok: bool = True
    ) -> tuple[Report, str]:
        monkeypatch.setattr(getpass, "getpass", lambda _prompt: token)
        settings = settings_with(monkeypatch, SUPABASE_JWT_SECRET=HS256_SECRET)
        report = Report()
        check_real_token(settings, report)
        return report, token

    def test_valid_token_passes_and_is_not_printed(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        token = jwt.encode(make_claims(), HS256_SECRET, algorithm="HS256")

        report, _ = self.run(monkeypatch, token)

        assert not report.failed
        assert token not in capsys.readouterr().out

    def test_bad_token_fails_and_is_not_printed(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        report, token = self.run(monkeypatch, "definitely-not-a-valid-token")

        assert report.failed
        assert token not in capsys.readouterr().out

    def test_empty_input_skips_with_a_warning_and_no_pass(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        report, _ = self.run(monkeypatch, "  ")

        assert not report.failed
        assert "skipped" in capsys.readouterr().out
