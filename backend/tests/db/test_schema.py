"""The shape of the database after migrations: tables, RLS, policies, privileges, drift."""

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect, text
from sqlalchemy.engine import make_url

from app.models import Base
from tests.db.conftest import alembic_config

REQUIRED_TABLES = {
    "users",
    "candidate_profiles",
    "candidate_facts",
    "resumes",
    "resume_skills",
    "companies",
    "company_research",
    "contacts",
    "contact_sources",
    "email_verifications",
    "opportunities",
    "applications",
    "outreach_campaigns",
    "email_drafts",
    "email_messages",
    "conversations",
    "follow_up_tasks",
    "suppression_list",
    "attachments",
    "activity_logs",
    "user_integrations",
}
# Extra tables, each justified in docs/DATABASE.md.
ADDED_TABLES = {"integration_secrets", "user_settings"}
SPECIAL_TABLES = {"users", "activity_logs", "integration_secrets"}
OWNER_TABLES = (REQUIRED_TABLES | ADDED_TABLES) - SPECIAL_TABLES


def test_all_required_tables_exist(engine: Engine) -> None:
    assert set(inspect(engine).get_table_names()) >= REQUIRED_TABLES | ADDED_TABLES


def test_models_declare_exactly_the_expected_tables() -> None:
    assert set(Base.metadata.tables) == REQUIRED_TABLES | ADDED_TABLES


def test_migrations_match_the_models(engine: Engine) -> None:
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        assert compare_metadata(context, Base.metadata) == []


@pytest.mark.parametrize("table_name", sorted(Base.metadata.tables))
def test_primary_key_is_a_uuid_called_id(table_name: str) -> None:
    primary_key = Base.metadata.tables[table_name].primary_key
    assert [column.name for column in primary_key.columns] == ["id"]
    assert primary_key.columns["id"].type.python_type.__name__ == "UUID"


@pytest.mark.parametrize("table_name", sorted(Base.metadata.tables))
def test_timestamps(table_name: str) -> None:
    columns = Base.metadata.tables[table_name].columns
    assert "created_at" in columns
    # activity_logs is append-only: a row is never updated, so it has nothing to record.
    assert ("updated_at" in columns) is (table_name != "activity_logs")


@pytest.mark.parametrize("table_name", sorted(set(Base.metadata.tables) - {"users"}))
def test_every_row_has_an_owner_and_goes_when_the_owner_goes(table_name: str) -> None:
    table = Base.metadata.tables[table_name]
    assert table.columns["user_id"].nullable is False
    owner_keys = [
        fk
        for fk in table.foreign_keys
        if fk.parent.name == "user_id" and fk.column.table.name == "users"
    ]
    assert len(owner_keys) == 1
    assert owner_keys[0].ondelete == "CASCADE"


@pytest.mark.parametrize("table_name", sorted(set(Base.metadata.tables) - {"users"}))
def test_references_between_user_tables_include_the_owner(table_name: str) -> None:
    """Without user_id in the key, one user could point a row at another user's record."""
    table = Base.metadata.tables[table_name]
    for constraint in table.foreign_key_constraints:
        if constraint.referred_table.name == "users":
            continue
        assert "user_id" in constraint.column_keys, constraint.name


def test_rls_is_enabled_on_every_table(engine: Engine) -> None:
    query = text(
        """
        SELECT c.relname
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname = ANY(:tables) AND NOT c.relrowsecurity
        """
    )
    tables = [*Base.metadata.tables, "alembic_version"]
    with engine.connect() as connection:
        assert connection.execute(query, {"tables": tables}).scalars().all() == []


def _policies(engine: Engine) -> dict[str, list[tuple[str, str | None, str | None]]]:
    query = text(
        """
        SELECT tablename, cmd, qual, with_check, roles
        FROM pg_policies WHERE schemaname = 'public'
        """
    )
    policies: dict[str, list[tuple[str, str | None, str | None]]] = {}
    with engine.connect() as connection:
        for table, cmd, qual, with_check, roles in connection.execute(query):
            assert list(roles) == ["authenticated"], f"{table} policy applies to {roles}"
            policies.setdefault(table, []).append((cmd, qual, with_check))
    return policies


def test_owner_tables_have_one_policy_checking_read_and_write(engine: Engine) -> None:
    policies = _policies(engine)
    for table in OWNER_TABLES:
        assert len(policies[table]) == 1, table
        cmd, qual, with_check = policies[table][0]
        assert cmd == "ALL", table
        assert qual is not None
        assert with_check is not None
        assert "user_id" in qual, table
        assert "auth.uid()" in qual, table
        assert "user_id" in with_check, table
        assert "auth.uid()" in with_check, table


def test_special_table_policies(engine: Engine) -> None:
    policies = _policies(engine)
    assert sorted(cmd for cmd, _, _ in policies["users"]) == ["SELECT", "UPDATE"]
    assert sorted(cmd for cmd, _, _ in policies["activity_logs"]) == ["INSERT", "SELECT"]
    assert "integration_secrets" not in policies


def _has_privilege(engine: Engine, role: str, table: str, privilege: str) -> bool:
    query = text("SELECT has_table_privilege(:role, :table, :privilege)")
    with engine.connect() as connection:
        result = connection.scalar(query, {"role": role, "table": table, "privilege": privilege})
    return bool(result)


@pytest.mark.parametrize("table", sorted([*Base.metadata.tables, "alembic_version"]))
@pytest.mark.parametrize("privilege", ["SELECT", "INSERT", "UPDATE", "DELETE"])
def test_anon_has_no_privileges_on_anything(engine: Engine, table: str, privilege: str) -> None:
    assert not _has_privilege(engine, "anon", table, privilege)


@pytest.mark.parametrize("privilege", ["SELECT", "INSERT", "UPDATE", "DELETE"])
def test_nothing_client_facing_can_touch_stored_tokens(engine: Engine, privilege: str) -> None:
    assert not _has_privilege(engine, "authenticated", "integration_secrets", privilege)


@pytest.mark.parametrize(
    ("table", "allowed"),
    [
        ("users", {"SELECT", "UPDATE"}),
        ("activity_logs", {"SELECT", "INSERT"}),
        ("alembic_version", set()),
        ("contacts", {"SELECT", "INSERT", "UPDATE", "DELETE"}),
    ],
)
def test_authenticated_privileges(engine: Engine, table: str, allowed: set[str]) -> None:
    actual = {
        privilege
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE")
        if _has_privilege(engine, "authenticated", table, privilege)
    }
    assert actual == allowed


def test_migrations_are_reversible(test_db_url: str, engine: Engine) -> None:
    """Destructive, so it only ever runs against a throwaway database on this machine."""
    if make_url(test_db_url).host not in {"localhost", "127.0.0.1"}:
        pytest.skip("downgrade test only runs against a local throwaway database")

    config = alembic_config(test_db_url)
    command.downgrade(config, "base")
    remaining = set(inspect(engine).get_table_names()) & set(Base.metadata.tables)
    command.upgrade(config, "head")

    assert remaining == set()
    assert set(inspect(engine).get_table_names()) >= set(Base.metadata.tables)
