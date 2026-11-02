"""Check a real Supabase project against what the backend needs. Never prints a secret.

Run from the repository root after filling in `.env`:

    backend\\.venv\\Scripts\\python -m app.cli.verify_supabase            read-only checks
    backend\\.venv\\Scripts\\python -m app.cli.verify_supabase --migrate  also apply migrations
    backend\\.venv\\Scripts\\python -m app.cli.verify_supabase --token    also check a real token
"""

import argparse
import getpass
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import ROOT_DIR, Settings, get_settings
from app.core.db import dispose_engine, get_engine, open_rls_session
from app.modules.auth.tokens import (
    AuthUnavailableError,
    TokenError,
    build_token_verifier,
    http_jwks_fetcher,
)

MINIMUM_POSTGRES = 150000
TRANSACTION_POOLER_PORT = 6543


class Report:
    def __init__(self) -> None:
        self.failed = False

    def ok(self, message: str) -> None:
        print(f"[ ok ] {message}")

    def warn(self, message: str) -> None:
        print(f"[warn] {message}")

    def fail(self, message: str) -> None:
        print(f"[FAIL] {message}")
        self.failed = True


def check_configuration(settings: Settings, report: Report) -> bool:
    url = settings.sqlalchemy_database_url
    if not url:
        report.fail("DATABASE_URL is empty. See docs/SUPABASE_SETUP.md.")
    else:
        parsed = make_url(url)
        report.ok(f"DATABASE_URL points at {parsed.host}:{parsed.port} (user {parsed.username})")
        if parsed.port == TRANSACTION_POOLER_PORT:
            report.warn("That is the transaction pooler. Use the session pooler for migrations.")
    if settings.supabase_url:
        report.ok(f"SUPABASE_URL is {settings.supabase_url}")
    else:
        report.fail("SUPABASE_URL is empty, so tokens cannot be verified.")
    if not settings.secrets_encryption_key.get_secret_value():
        report.warn("SECRETS_ENCRYPTION_KEY is empty. Needed once integrations store tokens.")
    return bool(url)


def check_signing_keys(settings: Settings, report: Report) -> None:
    jwks_url = settings.jwks_url
    has_secret = bool(settings.supabase_jwt_secret.get_secret_value())
    if not jwks_url:
        return
    try:
        document = http_jwks_fetcher(jwks_url)()
    except Exception as exc:
        report.fail(f"Could not fetch {jwks_url}: {type(exc).__name__}: {exc}")
        return
    keys = document.get("keys")
    if isinstance(keys, list) and keys:
        algorithms = sorted({str(key.get("alg")) for key in keys if isinstance(key, dict)})
        report.ok(f"JWKS at {jwks_url} has {len(keys)} key(s): {', '.join(algorithms)}")
    elif has_secret:
        report.ok("JWKS is empty: this project signs with the legacy secret, which is configured.")
    else:
        report.fail(
            "JWKS is empty and SUPABASE_JWT_SECRET is not set: no token can be verified. "
            "Enable asymmetric JWT signing keys, or set the legacy secret."
        )


def check_database(report: Report) -> bool:
    try:
        with get_engine().connect() as connection:
            version = int(connection.scalar(text("SHOW server_version_num")) or 0)
            user = connection.scalar(text("SELECT current_user"))
            roles = set(
                connection.scalars(
                    text("SELECT rolname FROM pg_roles WHERE rolname IN ('authenticated', 'anon')")
                )
            )
            has_uid = connection.scalar(text("SELECT to_regprocedure('auth.uid()') IS NOT NULL"))
    except SQLAlchemyError as exc:
        original = getattr(exc, "orig", exc)
        report.fail(f"Could not connect: {str(original).splitlines()[0]}")
        report.warn(
            "Check the password's URL-encoding, that the project is not paused, and on an IPv4 "
            "network use the Session pooler string instead of the direct one."
        )
        return False

    report.ok(f"Connected as {user}")
    if version >= MINIMUM_POSTGRES:
        report.ok(f"PostgreSQL version number {version}")
    else:
        report.fail(f"PostgreSQL {version} is too old; 15 or newer is required.")
    if roles == {"authenticated", "anon"}:
        report.ok("Roles authenticated and anon exist")
    else:
        report.warn(f"Only found roles {sorted(roles)}; migration 0002 will create stand-ins.")
    if has_uid:
        report.ok("auth.uid() exists")
    else:
        report.warn("auth.uid() is missing: this does not look like a Supabase database.")
    return True


def check_rls_session(report: Report) -> None:
    """Our per-request context must drive Supabase's own auth.uid(), not just our stand-in."""
    user_id = uuid4()
    session = open_rls_session({"sub": str(user_id), "role": "authenticated"})
    try:
        role = session.scalar(text("SELECT current_user"))
        uid = session.scalar(text("SELECT auth.uid()"))
    except SQLAlchemyError as exc:
        original = getattr(exc, "orig", exc)
        report.fail(f"Could not switch to the authenticated role: {str(original).splitlines()[0]}")
        return
    finally:
        session.close()
    if role == "authenticated" and uid == user_id:
        report.ok("RLS session runs as authenticated and auth.uid() sees the caller")
    else:
        report.fail(f"RLS session is wrong: role={role}, auth.uid() matched={uid == user_id}")


def check_migrations(migrate: bool, report: Report) -> None:
    config = Config(str(ROOT_DIR / "backend" / "alembic.ini"))
    config.attributes["database_url"] = get_settings().sqlalchemy_database_url
    head = ScriptDirectory.from_config(config).get_current_head()
    if migrate:
        command.upgrade(config, "head")
    with get_engine().connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
    if current == head:
        report.ok(f"Schema is at the latest migration ({head})")
    elif migrate:
        report.fail(f"Schema is at {current}, expected {head}")
    else:
        report.warn(f"Schema is at {current}, latest is {head}. Re-run with --migrate.")


def check_real_token(settings: Settings, report: Report) -> None:
    token = getpass.getpass("Paste a Supabase access token (input hidden, empty to skip): ").strip()
    if not token:
        report.warn("No token given; real-token verification was skipped.")
        return
    try:
        user = build_token_verifier(settings).verify_user(token)
    except TokenError as exc:
        report.fail(f"Token rejected: {exc.code} ({exc.reason})")
    except AuthUnavailableError as exc:
        report.fail(f"Tokens cannot be checked: {exc}")
    else:
        report.ok(f"Real token accepted for user {user.id}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check a real Supabase project.")
    parser.add_argument("--migrate", action="store_true", help="apply pending migrations")
    parser.add_argument("--token", action="store_true", help="also verify a real access token")
    options = parser.parse_args()

    settings = get_settings()
    report = Report()
    if check_configuration(settings, report):
        check_signing_keys(settings, report)
        if check_database(report):
            # In this order: on plain Postgres the role only exists once migrations have run.
            check_migrations(options.migrate, report)
            check_rls_session(report)
    if options.token:
        check_real_token(settings, report)
    dispose_engine()

    print("\nResult:", "PROBLEMS FOUND" if report.failed else "all required checks passed")
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
