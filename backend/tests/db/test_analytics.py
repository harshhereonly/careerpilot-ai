"""Dashboard counts: correct for each user, and blind to everyone else's data."""

from datetime import timedelta

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.models import Application, EmailMessage, FollowUpTask, Opportunity
from app.modules.analytics.service import get_summary
from tests.db.conftest import DbUser, RlsOpener
from tests.db.factories import (
    add,
    contact,
    conversation,
    draft,
    now,
    outbound_message,
    seed_everything,
    sending_draft,
)


def _inbound(session: Session, user: DbUser, thread_id: object, label: str | None) -> None:
    add(
        session,
        EmailMessage(
            user_id=user.id,
            conversation_id=thread_id,
            direction="inbound",
            from_address="them@example.test",
            to_addresses=["me@example.test"],
            classification=label,
        ),
    )


def test_new_user_sees_zeros(rls: RlsOpener, alice: DbUser) -> None:
    with rls(alice) as db:
        summary = get_summary(db, alice.id)

    assert set(summary.model_dump().values()) == {0}


def test_counts_match_the_records(engine: Engine, rls: RlsOpener, alice: DbUser) -> None:
    with Session(engine) as session:
        first = contact(session, alice.id)
        second = contact(session, alice.id)
        thread = conversation(session, alice.id, first.id)
        outbound_message(
            session, alice.id, thread.id, sending_draft(session, alice.id, first.id).id
        )
        _inbound(session, alice, thread.id, "positive")
        _inbound(session, alice, thread.id, "negative")
        _inbound(session, alice, thread.id, None)
        draft(session, alice.id, second.id, status="pending_review")
        for status in ("interviewing", "interviewing", "rejected", "applied"):
            opportunity = add(session, Opportunity(user_id=alice.id, title=status))
            add(
                session, Application(user_id=alice.id, opportunity_id=opportunity.id, status=status)
            )
        for due, status in ((-1, "pending"), (-2, "pending"), (3, "pending"), (-1, "cancelled")):
            add(
                session,
                FollowUpTask(
                    user_id=alice.id,
                    conversation_id=thread.id,
                    contact_id=first.id,
                    due_at=now() + timedelta(days=due),
                    sequence_number={-1: 1, -2: 2, 3: 3}[due]
                    + (10 if status == "cancelled" else 0),
                    status=status,
                ),
            )
        session.commit()

    with rls(alice) as db:
        summary = get_summary(db, alice.id)

    assert summary.model_dump() == {
        "total_contacts": 2,
        "emails_sent": 1,
        "replies_received": 3,
        "positive_replies": 1,
        "interviews": 2,
        "rejections": 1,
        "pending_drafts": 1,
        "follow_ups_due": 2,
    }


def test_other_users_data_is_never_counted(
    engine: Engine, rls: RlsOpener, alice: DbUser, bob: DbUser
) -> None:
    with Session(engine) as session:
        seed_everything(session, alice.id)
        session.commit()

    with rls(bob) as db:
        summary_through_rls = get_summary(db, bob.id)
    # Even on the privileged connection, where RLS does not apply, the owner filter holds.
    with Session(engine) as session:
        summary_without_rls = get_summary(session, bob.id)

    assert set(summary_through_rls.model_dump().values()) == {0}
    assert set(summary_without_rls.model_dump().values()) == {0}
