"""Database access.

Two kinds of session exist on purpose:

* ``RLSSession`` runs every transaction as the Postgres role ``authenticated`` with the caller's
  JWT claims, so row-level security applies to API queries as it does for direct client access.
* ``system_session`` is the privileged connection (table owner, bypasses RLS) for provisioning
  and background jobs, which must scope by ``user_id`` explicitly.
"""

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

from sqlalchemy import Connection, Engine, create_engine, event, text
from sqlalchemy.orm import Session, SessionTransaction

from app.core.config import get_settings

RLS_ROLE = "authenticated"
RLS_CLAIMS_KEY = "rls_claims"


class DatabaseNotConfiguredError(RuntimeError):
    """DATABASE_URL is empty."""


def database_configured() -> bool:
    return bool(get_settings().sqlalchemy_database_url)


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    url = settings.sqlalchemy_database_url
    if not url:
        raise DatabaseNotConfiguredError("DATABASE_URL is not set")
    return create_engine(
        url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        pool_recycle=1800,
        # Prepared statements break behind a transaction-mode pooler; not worth the risk here.
        connect_args={"prepare_threshold": None, "connect_timeout": 10},
    )


def dispose_engine() -> None:
    if get_engine.cache_info().currsize:
        get_engine().dispose()
    get_engine.cache_clear()


def apply_rls_context(connection: Connection, claims: Mapping[str, Any]) -> None:
    """Make the current transaction behave like a Supabase request from the token's user.

    ``SET LOCAL`` and ``set_config(..., true)`` last until the transaction ends, so nothing leaks
    to the next user of the pooled connection. ``auth.uid()`` reads these settings.
    """
    connection.execute(text(f"SET LOCAL ROLE {RLS_ROLE}"))
    connection.execute(
        text(
            "SELECT set_config('request.jwt.claims', :claims, true),"
            " set_config('request.jwt.claim.sub', :sub, true),"
            " set_config('request.jwt.claim.role', :role, true)"
        ),
        {"claims": json.dumps(claims, default=str), "sub": str(claims["sub"]), "role": RLS_ROLE},
    )


class RLSSession(Session):
    """Every transaction this session begins, including ones after a commit, is RLS-scoped."""


@event.listens_for(RLSSession, "after_begin")
def _apply_rls_on_begin(
    session: Session, _transaction: SessionTransaction, connection: Connection
) -> None:
    claims = session.info.get(RLS_CLAIMS_KEY)
    if not claims:
        # Fail closed: an unscoped transaction would silently run with the owner's privileges.
        raise RuntimeError("RLSSession began a transaction without JWT claims")
    apply_rls_context(connection, claims)


def open_rls_session(claims: Mapping[str, Any], engine: Engine | None = None) -> RLSSession:
    session = RLSSession(bind=engine or get_engine(), expire_on_commit=False)
    session.info[RLS_CLAIMS_KEY] = dict(claims)
    return session


@contextmanager
def system_session(engine: Engine | None = None) -> Iterator[Session]:
    with Session(bind=engine or get_engine(), expire_on_commit=False) as session:
        yield session
