"""The readiness checker against a real database, and against one that is not there."""

import pytest

from app.cli.verify_supabase import Report, check_database, check_migrations, check_rls_session
from app.core.config import get_settings
from app.core.db import dispose_engine


@pytest.fixture
def database(test_db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", test_db_url)
    get_settings.cache_clear()
    dispose_engine()


@pytest.mark.usefixtures("database")
def test_database_checks_pass() -> None:
    report = Report()

    assert check_database(report) is True
    assert not report.failed


@pytest.mark.usefixtures("database")
def test_rls_session_check_passes(capsys: pytest.CaptureFixture[str]) -> None:
    report = Report()

    check_rls_session(report)

    assert not report.failed
    assert "auth.uid() sees the caller" in capsys.readouterr().out


@pytest.mark.usefixtures("database")
def test_migration_check_sees_the_latest_schema(capsys: pytest.CaptureFixture[str]) -> None:
    report = Report()

    check_migrations(migrate=False, report=report)

    assert not report.failed
    assert "latest migration" in capsys.readouterr().out


def test_unreachable_database_fails_without_printing_the_password(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:hunter2-secret@127.0.0.1:1/nothing")
    get_settings.cache_clear()
    dispose_engine()
    report = Report()

    assert check_database(report) is False
    assert report.failed
    assert "hunter2-secret" not in capsys.readouterr().out
