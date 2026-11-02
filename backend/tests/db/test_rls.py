"""Cross-user isolation, through sessions opened exactly the way the API opens them."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, create_engine, delete, func, insert, select, text, update
from sqlalchemy.orm import Session

from app.core.db import RLSSession, open_rls_session
from app.core.errors import NotFoundError
from app.core.ownership import get_owned_or_404, owned_select
from app.models import ActivityLog, Contact, IntegrationSecret, User, UserIntegration
from tests.db.conftest import DbUser, RlsOpener, violates
from tests.db.factories import (
    OWNED_MODELS,
    RLS_VISIBLE_MODELS,
    add,
    company,
    contact,
    rows_changed,
    seed_everything,
)

PERMISSION_DENIED = "42501"


def _alices_contact(engine: Engine, alice: DbUser) -> UUID:
    with Session(engine) as session:
        row = contact(session, alice.id, full_name="Alice's secret contact")
        session.commit()
        return row.id


class TestSessionContext:
    def test_runs_as_the_restricted_role_with_the_callers_identity(
        self, rls: RlsOpener, alice: DbUser
    ) -> None:
        with rls(alice) as db:
            assert db.scalar(text("SELECT current_user")) == "authenticated"
            assert db.scalar(text("SELECT auth.uid()")) == alice.id

    def test_restriction_survives_a_commit(
        self, rls: RlsOpener, alice: DbUser, bob: DbUser
    ) -> None:
        """Regression guard: SET LOCAL ends with the transaction, the next one must re-apply it."""
        with rls(alice) as db:
            db.scalar(text("SELECT 1"))
            db.commit()

            assert db.scalar(text("SELECT current_user")) == "authenticated"
            assert db.scalar(text("SELECT auth.uid()")) == alice.id

    def test_restriction_survives_a_rollback(self, rls: RlsOpener, alice: DbUser) -> None:
        with rls(alice) as db:
            db.scalar(text("SELECT 1"))
            db.rollback()

            assert db.scalar(text("SELECT current_user")) == "authenticated"
            assert db.scalar(text("SELECT auth.uid()")) == alice.id

    def test_session_without_claims_refuses_to_run(self, engine: Engine) -> None:
        with (
            RLSSession(bind=engine) as session,
            pytest.raises(RuntimeError, match="without JWT claims"),
        ):
            session.execute(text("SELECT 1"))

    def test_nothing_leaks_to_the_next_user_of_a_pooled_connection(
        self, test_db_url: str, alice: DbUser
    ) -> None:
        pooled = create_engine(
            test_db_url, pool_size=1, max_overflow=0, connect_args={"prepare_threshold": None}
        )
        try:
            with open_rls_session(alice.claims, engine=pooled) as session:
                assert session.scalar(text("SELECT current_user")) == "authenticated"

            with pooled.connect() as connection:
                assert connection.scalar(text("SELECT current_user")) != "authenticated"
                assert connection.scalar(text("SELECT auth.uid()")) is None
        finally:
            pooled.dispose()

    def test_two_users_on_one_pooled_connection_stay_separate(
        self, test_db_url: str, alice: DbUser, bob: DbUser
    ) -> None:
        pooled = create_engine(
            test_db_url, pool_size=1, max_overflow=0, connect_args={"prepare_threshold": None}
        )
        try:
            for user in (alice, bob, alice):
                with open_rls_session(user.claims, engine=pooled) as session:
                    assert session.scalar(text("SELECT auth.uid()")) == user.id
        finally:
            pooled.dispose()


class TestCrossUserIsolation:
    def test_every_table_hides_other_users_rows_and_shows_your_own(
        self, engine: Engine, rls: RlsOpener, alice: DbUser, bob: DbUser
    ) -> None:
        with Session(engine) as session:
            seed_everything(session, alice.id)
            session.commit()

        with rls(bob) as db:
            visible = {
                model.__name__: db.scalar(select(func.count()).select_from(model))
                for model in RLS_VISIBLE_MODELS
            }
        assert visible == dict.fromkeys(visible, 0)

        with rls(alice) as db:
            for model in RLS_VISIBLE_MODELS:
                count = db.scalar(select(func.count()).select_from(model))
                assert (count or 0) >= 1, f"Alice cannot see her own {model.__name__}"

    def test_every_table_refuses_cross_user_update_and_delete(
        self, engine: Engine, rls: RlsOpener, alice: DbUser, bob: DbUser
    ) -> None:
        with Session(engine) as session:
            seed_everything(session, alice.id)
            session.commit()

        with rls(bob) as db:
            for model in OWNED_MODELS:
                changed = db.execute(
                    update(model).where(model.user_id == alice.id).values(user_id=bob.id)
                )
                assert rows_changed(changed) == 0, f"Bob updated Alice's {model.__name__}"
            # Delete children-first-agnostic: RLS must make every one of these match nothing.
            for model in OWNED_MODELS:
                removed = db.execute(delete(model).where(model.user_id == alice.id))
                assert rows_changed(removed) == 0, f"Bob deleted Alice's {model.__name__}"
            db.commit()

        with Session(engine) as session:
            for model in OWNED_MODELS:
                count = session.scalar(
                    select(func.count()).select_from(model).where(model.user_id == alice.id)
                )
                assert (count or 0) >= 1, f"Alice lost her {model.__name__}"

    def test_lookup_by_id_finds_nothing(
        self, engine: Engine, rls: RlsOpener, alice: DbUser, bob: DbUser
    ) -> None:
        contact_id = _alices_contact(engine, alice)

        with rls(bob) as db:
            assert db.get(Contact, contact_id) is None

    def test_cannot_create_a_row_in_someone_elses_name(
        self, rls: RlsOpener, alice: DbUser, bob: DbUser
    ) -> None:
        with rls(bob) as db, violates(PERMISSION_DENIED):
            contact(db, alice.id)

    def test_cannot_give_your_own_row_away(
        self, engine: Engine, rls: RlsOpener, alice: DbUser, bob: DbUser
    ) -> None:
        contact_id = _alices_contact(engine, alice)

        with rls(alice) as db, violates(PERMISSION_DENIED):
            db.execute(update(Contact).where(Contact.id == contact_id).values(user_id=bob.id))

    def test_cannot_reference_another_users_row_even_when_guessing_its_id(
        self, engine: Engine, rls: RlsOpener, alice: DbUser, bob: DbUser
    ) -> None:
        """Foreign keys ignore RLS: without composite keys this succeeds and leaks existence."""
        with Session(engine) as session:
            bobs_company = company(session, bob.id)
            session.commit()
            company_id = bobs_company.id

        with rls(alice) as db, violates("23503", "fk_contacts_company_id_companies"):
            contact(db, alice.id, company_id=company_id)

    def test_you_can_work_with_your_own_rows(self, rls: RlsOpener, alice: DbUser) -> None:
        with rls(alice) as db:
            row = contact(db, alice.id, full_name="Mine")
            db.commit()
            row.notes = "Updated after commit"
            db.commit()
            db.delete(row)
            db.commit()

    def test_no_identity_sees_nothing(self, engine: Engine, alice: DbUser) -> None:
        _alices_contact(engine, alice)

        with Session(engine) as session:
            session.execute(text("SET LOCAL ROLE authenticated"))
            assert session.scalar(text("SELECT auth.uid()")) is None
            assert session.scalar(select(func.count()).select_from(Contact)) == 0


class TestUsersTable:
    def test_you_only_see_yourself(self, rls: RlsOpener, alice: DbUser, bob: DbUser) -> None:
        with rls(alice) as db:
            assert db.scalars(select(User.id)).all() == [alice.id]
            assert db.get(User, bob.id) is None

    def test_you_can_update_yourself_but_not_others(
        self, engine: Engine, rls: RlsOpener, alice: DbUser, bob: DbUser
    ) -> None:
        with rls(alice) as db:
            mine = db.execute(update(User).where(User.id == alice.id).values(full_name="Alice"))
            theirs = db.execute(update(User).where(User.id == bob.id).values(full_name="Hacked"))
            db.commit()

        assert rows_changed(mine) == 1
        assert rows_changed(theirs) == 0
        with Session(engine) as session:
            assert session.scalar(select(User.full_name).where(User.id == bob.id)) is None

    def test_clients_cannot_create_users(self, rls: RlsOpener, alice: DbUser) -> None:
        with rls(alice) as db, violates(PERMISSION_DENIED):
            db.execute(insert(User).values(id=uuid4(), email=f"{uuid4()}@example.test"))

    def test_clients_cannot_delete_users_directly(self, rls: RlsOpener, alice: DbUser) -> None:
        with rls(alice) as db, violates(PERMISSION_DENIED):
            db.execute(delete(User).where(User.id == alice.id))


class TestSecrets:
    def test_tokens_are_unreadable_even_for_their_owner(
        self, engine: Engine, rls: RlsOpener, alice: DbUser
    ) -> None:
        with Session(engine) as session:
            seed_everything(session, alice.id)
            session.commit()

        with rls(alice) as db, violates(PERMISSION_DENIED):
            db.scalars(select(IntegrationSecret)).all()

    def test_tokens_cannot_be_written_by_clients(
        self, engine: Engine, rls: RlsOpener, alice: DbUser
    ) -> None:
        with Session(engine) as session:
            integration = add(
                session, UserIntegration(user_id=alice.id, provider="hunter", kind="discovery")
            )
            session.commit()
            integration_id = integration.id

        with rls(alice) as db, violates(PERMISSION_DENIED):
            add(
                db,
                IntegrationSecret(
                    user_id=alice.id, integration_id=integration_id, encrypted_payload=b"x"
                ),
            )

    def test_the_owner_can_see_that_an_integration_exists(
        self, engine: Engine, rls: RlsOpener, alice: DbUser
    ) -> None:
        with Session(engine) as session:
            add(session, UserIntegration(user_id=alice.id, provider="hunter", kind="discovery"))
            session.commit()

        with rls(alice) as db:
            assert db.scalars(select(UserIntegration.provider)).all() == ["hunter"]


class TestAuditLog:
    def _log_entry(self, db: RLSSession, user: DbUser) -> ActivityLog:
        return add(db, ActivityLog(user_id=user.id, action="contact.created"))

    def test_you_can_write_and_read_your_own_entries(self, rls: RlsOpener, alice: DbUser) -> None:
        with rls(alice) as db:
            self._log_entry(db, alice)
            db.commit()

            assert db.scalars(select(ActivityLog.action)).all() == ["contact.created"]

    def test_you_cannot_write_entries_for_someone_else(
        self, rls: RlsOpener, alice: DbUser, bob: DbUser
    ) -> None:
        with rls(bob) as db, violates(PERMISSION_DENIED):
            self._log_entry(db, alice)

    def test_entries_cannot_be_changed(self, rls: RlsOpener, alice: DbUser) -> None:
        with rls(alice) as db:
            self._log_entry(db, alice)
            db.commit()
            with violates(PERMISSION_DENIED):
                db.execute(update(ActivityLog).values(action="tampered"))

    def test_entries_cannot_be_deleted(self, rls: RlsOpener, alice: DbUser) -> None:
        with rls(alice) as db:
            self._log_entry(db, alice)
            db.commit()
            with violates(PERMISSION_DENIED):
                db.execute(delete(ActivityLog))


class TestAnonymousRole:
    @pytest.mark.parametrize("table", ["contacts", "users", "integration_secrets", "email_drafts"])
    def test_public_key_requests_cannot_read_anything(self, engine: Engine, table: str) -> None:
        """What a request with only the publishable key runs as through Supabase's Data API."""
        with Session(engine) as session:
            session.execute(text("SET LOCAL ROLE anon"))
            with violates(PERMISSION_DENIED):
                session.execute(text(f"SELECT * FROM {table}"))


class TestOwnershipHelpers:
    """The application layer must hold even on a connection where RLS does not apply."""

    def test_owner_gets_the_row(self, engine: Engine, alice: DbUser) -> None:
        contact_id = _alices_contact(engine, alice)

        with Session(engine) as session:
            assert get_owned_or_404(session, Contact, contact_id, alice.id).id == contact_id

    def test_other_user_gets_404_on_a_privileged_connection(
        self, engine: Engine, alice: DbUser, bob: DbUser
    ) -> None:
        contact_id = _alices_contact(engine, alice)

        with Session(engine) as session, pytest.raises(NotFoundError):
            get_owned_or_404(session, Contact, contact_id, bob.id)

    def test_list_query_only_returns_the_callers_rows(
        self, engine: Engine, alice: DbUser, bob: DbUser
    ) -> None:
        alices = _alices_contact(engine, alice)
        bobs = _alices_contact(engine, bob)

        with Session(engine) as session:
            ids = session.scalars(
                owned_select(Contact, alice.id).with_only_columns(Contact.id)
            ).all()

        assert alices in ids
        assert bobs not in ids
