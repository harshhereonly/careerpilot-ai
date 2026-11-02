"""Database constraints and triggers, exercised with real SQL on the privileged connection.

These rules must hold even if application code has a bug, so nothing here goes through a service.
"""

from uuid import uuid4

import pytest
from sqlalchemy import Engine, delete, func, select, update
from sqlalchemy.orm import Session

from app.models import (
    Application,
    Base,
    Company,
    Contact,
    EmailDraft,
    EmailMessage,
    FollowUpTask,
    IntegrationSecret,
    Opportunity,
    OutreachCampaign,
    Resume,
    SuppressionEntry,
    User,
    UserIntegration,
    UserSettings,
)
from tests.db.conftest import DbUser, violates
from tests.db.factories import (
    ALL_USER_MODELS,
    add,
    approve,
    company,
    contact,
    conversation,
    draft,
    now,
    outbound_message,
    seed_everything,
    sending_draft,
    unique_email,
)

UNIQUE = "23505"
FOREIGN_KEY = "23503"
CHECK = "23514"


class TestContacts:
    def test_email_is_unique_per_user(self, engine: Engine, alice: DbUser, bob: DbUser) -> None:
        email = unique_email()
        with Session(engine) as session:
            contact(session, alice.id, email=email)
            contact(session, bob.id, email=email)  # another user may know the same person
            session.commit()

        with Session(engine) as session, violates(UNIQUE, "uq_contacts_user_email"):
            contact(session, alice.id, email=email)

    def test_many_contacts_without_email_are_allowed(self, engine: Engine, alice: DbUser) -> None:
        with Session(engine) as session:
            contact(session, alice.id)
            contact(session, alice.id)
            session.commit()

    def test_email_must_be_normalised(self, engine: Engine, alice: DbUser) -> None:
        with Session(engine) as session, violates(CHECK, "ck_contacts_email_lowercase"):
            contact(session, alice.id, email="Grace@Example.Test")

    def test_unknown_status_is_refused(self, engine: Engine, alice: DbUser) -> None:
        with Session(engine) as session, violates(CHECK, "ck_contacts_status"):
            contact(session, alice.id, status="spammed")

    def test_full_name_is_required(self, engine: Engine, alice: DbUser) -> None:
        with Session(engine) as session, violates("23502"):
            add(session, Contact(user_id=alice.id))


class TestSameOwnerReferences:
    """Foreign keys bypass RLS, so the composite keys are what stops these."""

    def test_contact_cannot_point_at_another_users_company(
        self, engine: Engine, alice: DbUser, bob: DbUser
    ) -> None:
        with Session(engine) as session:
            bobs_company = company(session, bob.id)
            session.commit()
            bobs_company_id = bobs_company.id

        with Session(engine) as session, violates(FOREIGN_KEY, "fk_contacts_company_id_companies"):
            contact(session, alice.id, company_id=bobs_company_id)

    def test_draft_cannot_be_addressed_to_another_users_contact(
        self, engine: Engine, alice: DbUser, bob: DbUser
    ) -> None:
        with Session(engine) as session:
            bobs_contact = contact(session, bob.id)
            session.commit()
            bobs_contact_id = bobs_contact.id

        with (
            Session(engine) as session,
            violates(FOREIGN_KEY, "fk_email_drafts_contact_id_contacts"),
        ):
            draft(session, alice.id, bobs_contact_id)

    def test_application_cannot_use_another_users_resume(
        self, engine: Engine, alice: DbUser, bob: DbUser
    ) -> None:
        with Session(engine) as session:
            bobs_resume = add(session, Resume(user_id=bob.id, name="CV"))
            opportunity = add(session, Opportunity(user_id=alice.id, title="Intern"))
            session.commit()
            ids = bobs_resume.id, opportunity.id

        with Session(engine) as session, violates(FOREIGN_KEY, "fk_applications_resume_id_resumes"):
            add(session, Application(user_id=alice.id, opportunity_id=ids[1], resume_id=ids[0]))

    def test_secret_cannot_be_attached_to_another_users_integration(
        self, engine: Engine, alice: DbUser, bob: DbUser
    ) -> None:
        with Session(engine) as session:
            integration = add(
                session, UserIntegration(user_id=bob.id, provider="gmail", kind="email_delivery")
            )
            session.commit()
            integration_id = integration.id

        constraint = "fk_integration_secrets_integration_id_user_integrations"
        with Session(engine) as session, violates(FOREIGN_KEY, constraint):
            add(
                session,
                IntegrationSecret(
                    user_id=alice.id, integration_id=integration_id, encrypted_payload=b"x"
                ),
            )


class TestApprovalRule:
    """No email without explicit approval of the exact text."""

    @pytest.mark.parametrize("status", ["approved", "sending", "sent", "failed"])
    def test_status_after_approval_needs_an_approval(
        self, engine: Engine, alice: DbUser, status: str
    ) -> None:
        with Session(engine) as session:
            contact_row = contact(session, alice.id)
            with violates(CHECK, "ck_email_drafts_approval_required"):
                draft(session, alice.id, contact_row.id, status=status)

    def test_approval_timestamp_alone_is_not_enough(self, engine: Engine, alice: DbUser) -> None:
        """Regression: a NULL approved_version once slipped through, as CHECK passes on NULL."""
        with Session(engine) as session:
            contact_row = contact(session, alice.id)
            with violates(CHECK, "ck_email_drafts_approval_required"):
                draft(session, alice.id, contact_row.id, status="approved", approved_at=now())

    def test_approval_version_alone_is_not_enough(self, engine: Engine, alice: DbUser) -> None:
        with Session(engine) as session:
            contact_row = contact(session, alice.id)
            with violates(CHECK, "ck_email_drafts_approval_required"):
                draft(session, alice.id, contact_row.id, status="approved", approved_version=1)

    def test_approval_must_be_for_the_current_version(self, engine: Engine, alice: DbUser) -> None:
        with Session(engine) as session:
            contact_row = contact(session, alice.id)
            with violates(CHECK, "ck_email_drafts_approval_required"):
                draft(
                    session,
                    alice.id,
                    contact_row.id,
                    status="approved",
                    approved_at=now(),
                    approved_version=7,
                )

    def test_new_draft_starts_unapproved_at_version_one(
        self, engine: Engine, alice: DbUser
    ) -> None:
        with Session(engine) as session:
            contact_row = contact(session, alice.id)
            row = draft(session, alice.id, contact_row.id)
            session.commit()
            session.refresh(row)

            assert row.status == "draft"
            assert row.version == 1
            assert row.approved_at is None

    def test_approving_the_current_version_works(self, engine: Engine, alice: DbUser) -> None:
        with Session(engine) as session:
            row = draft(session, alice.id, contact(session, alice.id).id)
            approve(session, row)
            session.commit()
            session.refresh(row)

            assert row.status == "approved"
            assert row.approved_version == row.version

    def test_editing_bumps_the_version(self, engine: Engine, alice: DbUser) -> None:
        with Session(engine) as session:
            row = draft(session, alice.id, contact(session, alice.id).id)
            row.body = "Edited"
            session.commit()
            session.refresh(row)

            assert row.version == 2

    @pytest.mark.parametrize("field", ["subject", "body"])
    def test_editing_an_approved_draft_withdraws_the_approval(
        self, engine: Engine, alice: DbUser, field: str
    ) -> None:
        with Session(engine) as session:
            row = draft(session, alice.id, contact(session, alice.id).id)
            approve(session, row)
            setattr(row, field, "Changed after approval")
            session.commit()
            session.refresh(row)

            assert row.status == "pending_review"
            assert row.approved_at is None
            assert row.approved_version is None
            assert row.version == 2

    def test_editing_a_failed_draft_needs_a_new_approval(
        self, engine: Engine, alice: DbUser
    ) -> None:
        with Session(engine) as session:
            row = draft(session, alice.id, contact(session, alice.id).id)
            approve(session, row)
            row.status = "failed"
            session.flush()
            row.body = "Fixed the typo"
            session.commit()
            session.refresh(row)

            assert row.status == "pending_review"
            assert row.approved_at is None

    def test_failed_draft_may_retry_without_edit(self, engine: Engine, alice: DbUser) -> None:
        with Session(engine) as session:
            row = draft(session, alice.id, contact(session, alice.id).id)
            approve(session, row)
            row.status = "failed"
            session.flush()
            row.status = "sending"
            session.commit()
            session.refresh(row)

            assert row.status == "sending"

    def test_edit_and_send_in_one_statement_is_refused(self, engine: Engine, alice: DbUser) -> None:
        with Session(engine) as session:
            row = draft(session, alice.id, contact(session, alice.id).id)
            approve(session, row)
            row.body = "Sneaky edit"
            row.status = "sending"
            with violates(CHECK, "ck_email_drafts_approval_required"):
                session.flush()

    def test_version_cannot_be_set_by_hand(self, engine: Engine, alice: DbUser) -> None:
        with Session(engine) as session:
            row = draft(session, alice.id, contact(session, alice.id).id)
            row.version = 99
            session.commit()
            session.refresh(row)

            assert row.version == 1

    def test_old_approval_cannot_be_replayed_after_an_edit(
        self, engine: Engine, alice: DbUser
    ) -> None:
        with Session(engine) as session:
            row = draft(session, alice.id, contact(session, alice.id).id)
            approve(session, row)
            row.body = "Edited after approval"
            session.flush()

            statement = (
                update(EmailDraft)
                .where(EmailDraft.id == row.id)
                .values(status="approved", approved_at=now(), approved_version=1, version=1)
            )
            with violates(CHECK, "ck_email_drafts_approval_required"):
                session.execute(statement)

    @pytest.mark.parametrize("status", ["sending", "sent"])
    def test_text_is_frozen_once_sending_starts(
        self, engine: Engine, alice: DbUser, status: str
    ) -> None:
        with Session(engine) as session:
            row = sending_draft(session, alice.id, contact(session, alice.id).id)
            row.status = status
            session.flush()
            row.body = "Too late"
            with violates(CHECK):
                session.flush()

    @pytest.mark.parametrize("status", ["draft", "approved", "sending", "pending_review"])
    def test_sent_is_final_so_nothing_can_be_sent_twice(
        self, engine: Engine, alice: DbUser, status: str
    ) -> None:
        with Session(engine) as session:
            row = sending_draft(session, alice.id, contact(session, alice.id).id)
            row.status = "sent"
            session.flush()
            row.status = status
            with violates(CHECK):
                session.flush()


class TestDuplicateProtection:
    def test_only_one_live_first_email_per_contact(self, engine: Engine, alice: DbUser) -> None:
        with Session(engine) as session:
            contact_row = contact(session, alice.id)
            draft(session, alice.id, contact_row.id)
            with violates(UNIQUE, "uq_email_drafts_one_initial_per_contact"):
                draft(session, alice.id, contact_row.id)

    @pytest.mark.parametrize("status", ["rejected", "cancelled"])
    def test_a_dropped_draft_does_not_block_a_new_one(
        self, engine: Engine, alice: DbUser, status: str
    ) -> None:
        with Session(engine) as session:
            contact_row = contact(session, alice.id)
            draft(session, alice.id, contact_row.id, status=status)
            draft(session, alice.id, contact_row.id)
            session.commit()

    def test_a_sent_first_email_blocks_another(self, engine: Engine, alice: DbUser) -> None:
        with Session(engine) as session:
            contact_row = contact(session, alice.id)
            row = sending_draft(session, alice.id, contact_row.id)
            row.status = "sent"
            session.flush()
            with violates(UNIQUE, "uq_email_drafts_one_initial_per_contact"):
                draft(session, alice.id, contact_row.id)

    def test_follow_ups_are_not_limited_by_this_rule(self, engine: Engine, alice: DbUser) -> None:
        with Session(engine) as session:
            contact_row = contact(session, alice.id)
            draft(session, alice.id, contact_row.id)
            draft(session, alice.id, contact_row.id, kind="follow_up")
            draft(session, alice.id, contact_row.id, kind="follow_up")
            session.commit()

    def test_follow_up_sequence_cannot_repeat(self, engine: Engine, alice: DbUser) -> None:
        with Session(engine) as session:
            contact_row = contact(session, alice.id)
            thread = conversation(session, alice.id, contact_row.id)
            fields = {
                "user_id": alice.id,
                "conversation_id": thread.id,
                "contact_id": contact_row.id,
                "due_at": now(),
                "sequence_number": 1,
            }
            add(session, FollowUpTask(**fields))
            with violates(UNIQUE, "uq_follow_up_tasks_conversation_sequence"):
                add(session, FollowUpTask(**fields))


class TestOutboundMessages:
    def test_outbound_without_a_draft_is_refused(self, engine: Engine, alice: DbUser) -> None:
        # Two independent guards exist; the trigger fires first, so no constraint name is asserted.
        with Session(engine) as session:
            thread = conversation(session, alice.id, contact(session, alice.id).id)
            with violates(CHECK):
                outbound_message(session, alice.id, thread.id, None)

    @pytest.mark.parametrize("status", ["draft", "pending_review", "rejected", "cancelled"])
    def test_outbound_from_an_unapproved_draft_is_refused(
        self, engine: Engine, alice: DbUser, status: str
    ) -> None:
        with Session(engine) as session:
            contact_row = contact(session, alice.id)
            thread = conversation(session, alice.id, contact_row.id)
            row = draft(session, alice.id, contact_row.id, status=status)
            with violates(CHECK):
                outbound_message(session, alice.id, thread.id, row.id)

    def test_approved_is_not_enough_it_must_be_in_the_send_step(
        self, engine: Engine, alice: DbUser
    ) -> None:
        with Session(engine) as session:
            contact_row = contact(session, alice.id)
            thread = conversation(session, alice.id, contact_row.id)
            row = draft(session, alice.id, contact_row.id)
            approve(session, row)
            with violates(CHECK):
                outbound_message(session, alice.id, thread.id, row.id)

    def test_outbound_from_an_approved_draft_being_sent_works(
        self, engine: Engine, alice: DbUser
    ) -> None:
        with Session(engine) as session:
            contact_row = contact(session, alice.id)
            thread = conversation(session, alice.id, contact_row.id)
            row = sending_draft(session, alice.id, contact_row.id)
            outbound_message(session, alice.id, thread.id, row.id)
            session.commit()

    def test_outbound_cannot_borrow_another_users_approved_draft(
        self, engine: Engine, alice: DbUser, bob: DbUser
    ) -> None:
        with Session(engine) as session:
            bobs_draft = sending_draft(session, bob.id, contact(session, bob.id).id)
            alices_thread = conversation(session, alice.id, contact(session, alice.id).id)
            session.commit()
            draft_id, thread_id = bobs_draft.id, alices_thread.id

        with Session(engine) as session, violates(CHECK):
            outbound_message(session, alice.id, thread_id, draft_id)

    def test_message_cannot_later_be_repointed_at_an_unapproved_draft(
        self, engine: Engine, alice: DbUser
    ) -> None:
        with Session(engine) as session:
            contact_row = contact(session, alice.id)
            thread = conversation(session, alice.id, contact_row.id)
            approved = sending_draft(session, alice.id, contact_row.id)
            message = outbound_message(session, alice.id, thread.id, approved.id)
            unapproved = draft(session, alice.id, contact_row.id, kind="follow_up")
            message.draft_id = unapproved.id
            with violates(CHECK):
                session.flush()

    def test_inbound_messages_need_no_draft(self, engine: Engine, alice: DbUser) -> None:
        with Session(engine) as session:
            thread = conversation(session, alice.id, contact(session, alice.id).id)
            add(
                session,
                EmailMessage(
                    user_id=alice.id,
                    conversation_id=thread.id,
                    direction="inbound",
                    from_address="them@example.test",
                    to_addresses=["me@example.test"],
                    classification="positive",
                    classification_confidence=0.9,
                ),
            )
            session.commit()

    def test_gmail_message_is_recorded_once(self, engine: Engine, alice: DbUser) -> None:
        gmail_id = f"gmail-{uuid4()}"
        with Session(engine) as session:
            thread = conversation(session, alice.id, contact(session, alice.id).id)
            fields = {
                "user_id": alice.id,
                "conversation_id": thread.id,
                "direction": "inbound",
                "gmail_message_id": gmail_id,
                "from_address": "them@example.test",
                "to_addresses": ["me@example.test"],
            }
            add(session, EmailMessage(**fields))
            with violates(UNIQUE, "uq_email_messages_user_gmail"):
                add(session, EmailMessage(**fields))


class TestDeletionBehaviour:
    def test_deleting_a_company_keeps_its_contact_and_clears_only_the_reference(
        self, engine: Engine, alice: DbUser
    ) -> None:
        with Session(engine) as session:
            company_row = company(session, alice.id)
            contact_row = contact(session, alice.id, company_id=company_row.id)
            session.flush()
            session.execute(delete(Company).where(Company.id == company_row.id))
            session.commit()
            session.refresh(contact_row)

            assert contact_row.company_id is None
            assert contact_row.user_id == alice.id

    def test_deleting_a_contact_removes_its_conversation_drafts_and_sent_messages(
        self, engine: Engine, alice: DbUser
    ) -> None:
        with Session(engine) as session:
            contact_row = contact(session, alice.id)
            thread = conversation(session, alice.id, contact_row.id)
            row = sending_draft(session, alice.id, contact_row.id, conversation_id=thread.id)
            outbound_message(session, alice.id, thread.id, row.id)
            session.commit()
            contact_id = contact_row.id

        with Session(engine) as session:
            session.execute(delete(Contact).where(Contact.id == contact_id))
            session.commit()

        with Session(engine) as session:
            for model in (EmailDraft, EmailMessage):
                count = session.scalar(
                    select(func.count()).select_from(model).where(model.user_id == alice.id)
                )
                assert count == 0, model.__name__

    def test_a_draft_with_a_sent_message_cannot_be_deleted_on_its_own(
        self, engine: Engine, alice: DbUser
    ) -> None:
        with Session(engine) as session:
            contact_row = contact(session, alice.id)
            thread = conversation(session, alice.id, contact_row.id)
            row = sending_draft(session, alice.id, contact_row.id)
            outbound_message(session, alice.id, thread.id, row.id)
            session.commit()
            draft_id = row.id

        with (
            Session(engine) as session,
            violates(FOREIGN_KEY, "fk_email_messages_draft_id_email_drafts"),
        ):
            session.execute(delete(EmailDraft).where(EmailDraft.id == draft_id))

    def test_deleting_a_user_removes_every_row_they_own_and_nothing_else(
        self, engine: Engine, alice: DbUser, bob: DbUser
    ) -> None:
        with Session(engine) as session:
            seed_everything(session, alice.id)
            seed_everything(session, bob.id)
            session.commit()

        with Session(engine) as session:
            session.execute(delete(User).where(User.id == alice.id))
            session.commit()

        with Session(engine) as session:
            for model in ALL_USER_MODELS:
                query = select(func.count()).select_from(model).where(model.user_id == alice.id)
                assert session.scalar(query) == 0, f"{model.__name__} survived for Alice"
                query = select(func.count()).select_from(model).where(model.user_id == bob.id)
                assert (session.scalar(query) or 0) >= 1, f"{model.__name__} was lost for Bob"

    def test_seed_covers_every_owned_table(self) -> None:
        covered = {model.__table__.name for model in ALL_USER_MODELS}

        assert covered == set(Base.metadata.tables) - {"users"}


class TestOtherRules:
    def test_only_one_default_resume(self, engine: Engine, alice: DbUser) -> None:
        with Session(engine) as session:
            add(session, Resume(user_id=alice.id, name="A", is_default=True))
            add(session, Resume(user_id=alice.id, name="B", is_default=False))
            with violates(UNIQUE, "uq_resumes_user_default"):
                add(session, Resume(user_id=alice.id, name="C", is_default=True))

    def test_suppression_entry_is_an_email_or_a_domain_not_both(
        self, engine: Engine, alice: DbUser
    ) -> None:
        with Session(engine) as session, violates(CHECK, "ck_suppression_list_email_xor_domain"):
            add(session, SuppressionEntry(user_id=alice.id, email="a@b.test", domain="b.test"))

        with Session(engine) as session, violates(CHECK, "ck_suppression_list_email_xor_domain"):
            add(session, SuppressionEntry(user_id=alice.id))

    def test_suppressed_address_is_listed_once(self, engine: Engine, alice: DbUser) -> None:
        email = unique_email()
        with Session(engine) as session:
            add(session, SuppressionEntry(user_id=alice.id, email=email))
            with violates(UNIQUE, "uq_suppression_user_email"):
                add(session, SuppressionEntry(user_id=alice.id, email=email))

    def test_company_domain_is_unique_per_user(
        self, engine: Engine, alice: DbUser, bob: DbUser
    ) -> None:
        with Session(engine) as session:
            company(session, alice.id, domain="acme.test")
            company(session, bob.id, domain="acme.test")
            with violates(UNIQUE, "uq_companies_user_domain"):
                company(session, alice.id, domain="acme.test")

    def test_opportunity_url_is_unique_ignoring_case(self, engine: Engine, alice: DbUser) -> None:
        with Session(engine) as session:
            add(session, Opportunity(user_id=alice.id, title="A", url="https://x.test/Job"))
            with violates(UNIQUE, "uq_opportunities_user_url"):
                add(session, Opportunity(user_id=alice.id, title="B", url="https://x.test/job"))

    def test_one_application_per_opportunity(self, engine: Engine, alice: DbUser) -> None:
        with Session(engine) as session:
            opportunity = add(session, Opportunity(user_id=alice.id, title="A"))
            add(session, Application(user_id=alice.id, opportunity_id=opportunity.id))
            with violates(UNIQUE, "uq_applications_user_opportunity"):
                add(session, Application(user_id=alice.id, opportunity_id=opportunity.id))

    def test_match_score_is_a_percentage(self, engine: Engine, alice: DbUser) -> None:
        with Session(engine) as session, violates(CHECK, "ck_opportunities_match_score"):
            add(session, Opportunity(user_id=alice.id, title="A", match_score=140))

    def test_settings_default_to_conservative_values(self, engine: Engine, alice: DbUser) -> None:
        with Session(engine) as session:
            row = add(session, UserSettings(user_id=alice.id))
            session.commit()
            session.refresh(row)

            assert row.daily_send_limit == 10
            assert row.sending_paused is False
            assert row.max_follow_ups == 2

    @pytest.mark.parametrize(
        ("fields", "constraint"),
        [
            ({"daily_send_limit": 101}, "ck_user_settings_daily_send_limit_range"),
            ({"daily_send_limit": -1}, "ck_user_settings_daily_send_limit_range"),
            ({"follow_up_delay_days": 0}, "ck_user_settings_follow_up_delay_range"),
            ({"max_follow_ups": 6}, "ck_user_settings_max_follow_ups_range"),
        ],
    )
    def test_settings_cannot_leave_the_safe_range(
        self, engine: Engine, alice: DbUser, fields: dict[str, int], constraint: str
    ) -> None:
        with Session(engine) as session, violates(CHECK, constraint):
            add(session, UserSettings(user_id=alice.id, **fields))

    def test_one_settings_row_per_user(self, engine: Engine, alice: DbUser) -> None:
        with Session(engine) as session:
            add(session, UserSettings(user_id=alice.id))
            with violates(UNIQUE, "uq_user_settings_user_id"):
                add(session, UserSettings(user_id=alice.id))

    def test_campaign_status_is_checked(self, engine: Engine, alice: DbUser) -> None:
        with Session(engine) as session, violates(CHECK, "ck_outreach_campaigns_status"):
            add(session, OutreachCampaign(user_id=alice.id, name="X", status="launched"))

    def test_updated_at_moves_on_update_and_created_at_does_not(
        self, engine: Engine, alice: DbUser
    ) -> None:
        with Session(engine) as session:
            row = contact(session, alice.id)
            session.commit()
            session.refresh(row)
            contact_id, created, updated = row.id, row.created_at, row.updated_at

        with Session(engine) as session:
            session.execute(update(Contact).where(Contact.id == contact_id).values(notes="Changed"))
            session.commit()

        with Session(engine) as session:
            row = session.scalars(select(Contact).where(Contact.id == contact_id)).one()

            assert row.created_at == created
            assert row.updated_at > updated
