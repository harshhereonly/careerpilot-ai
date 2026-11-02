"""Fixtures for tests that need a real Postgres.

They run against TEST_DATABASE_URL (environment or root .env) and are skipped, with a visible
reason, when it is not set. Each test creates its own users with random ids and deletes them
afterwards; sessions are opened one at a time so nothing depends on concurrent transactions.
"""

from collections.abc import Callable, Iterator
from contextlib import _GeneratorContextManager, contextmanager
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Engine, create_engine, delete
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.config import ROOT_DIR, Settings
from app.core.db import RLSSession, open_rls_session
from app.models import User
from tests.helpers import make_claims


class _TestDatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    test_database_url: SecretStr = SecretStr("")


def alembic_config(url: str) -> Config:
    config = Config(str(ROOT_DIR / "backend" / "alembic.ini"))
    config.attributes["database_url"] = url
    return config


@pytest.fixture(scope="session")
def test_db_url() -> str:
    raw = _TestDatabaseSettings().test_database_url
    url = Settings(_env_file=None, database_url=raw).sqlalchemy_database_url
    if not url:
        pytest.skip(
            "TEST_DATABASE_URL is not set, so the database tests did NOT run. "
            "Run scripts/test-db.ps1 or see docs/SUPABASE_SETUP.md."
        )
    command.upgrade(alembic_config(url), "head")
    return url


@pytest.fixture(scope="session")
def engine(test_db_url: str) -> Iterator[Engine]:
    # Reuse connections like the app does: a new one per session floods a session-mode pooler.
    test_engine = create_engine(
        test_db_url,
        pool_size=2,
        max_overflow=3,
        connect_args={"prepare_threshold": None, "connect_timeout": 15},
    )
    yield test_engine
    test_engine.dispose()


@dataclass(frozen=True)
class DbUser:
    id: UUID
    email: str
    claims: dict[str, Any]


UserFactory = Callable[[], DbUser]


@pytest.fixture
def make_user(engine: Engine) -> Iterator[UserFactory]:
    created: list[UUID] = []

    def factory() -> DbUser:
        user_id = uuid4()
        user = DbUser(id=user_id, email=f"{user_id}@example.test", claims=make_claims(user_id))
        with Session(engine) as session:
            session.add(User(id=user.id, email=user.email))
            session.commit()
        created.append(user_id)
        return user

    yield factory

    if created:
        with Session(engine) as session:
            session.execute(delete(User).where(User.id.in_(created)))
            session.commit()


@pytest.fixture
def alice(make_user: UserFactory) -> DbUser:
    return make_user()


@pytest.fixture
def bob(make_user: UserFactory) -> DbUser:
    return make_user()


RlsOpener = Callable[[DbUser], _GeneratorContextManager[RLSSession]]


@pytest.fixture
def rls(engine: Engine) -> RlsOpener:
    """Open a session exactly as the API does for a request from this user."""

    @contextmanager
    def open_session(user: DbUser) -> Iterator[RLSSession]:
        session = open_rls_session(user.claims, engine=engine)
        try:
            yield session
        finally:
            session.close()

    return open_session


@contextmanager
def violates(sqlstate: str, constraint: str | None = None) -> Iterator[None]:
    """The block must fail in the database with this SQLSTATE (and constraint, when given)."""
    with pytest.raises(DBAPIError) as caught:
        yield
    original = caught.value.orig
    assert getattr(original, "sqlstate", None) == sqlstate, str(original)
    if constraint is not None:
        assert getattr(getattr(original, "diag", None), "constraint_name", None) == constraint
