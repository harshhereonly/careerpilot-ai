from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from app.models import Application, Contact, EmailDraft, EmailMessage, FollowUpTask
from app.modules.analytics.schemas import Summary


def _count(model: type[Any], user_id: UUID, *conditions: ColumnElement[bool]) -> Any:
    return (
        select(func.count())
        .select_from(model)
        .where(model.user_id == user_id, *conditions)
        .scalar_subquery()
    )


def get_summary(db: Session, user_id: UUID) -> Summary:
    """One round trip. Each count also filters by owner, on top of row-level security."""
    row = db.execute(
        select(
            _count(Contact, user_id).label("total_contacts"),
            _count(EmailMessage, user_id, EmailMessage.direction == "outbound").label(
                "emails_sent"
            ),
            _count(EmailMessage, user_id, EmailMessage.direction == "inbound").label(
                "replies_received"
            ),
            _count(
                EmailMessage,
                user_id,
                EmailMessage.direction == "inbound",
                EmailMessage.classification == "positive",
            ).label("positive_replies"),
            _count(Application, user_id, Application.status == "interviewing").label("interviews"),
            _count(Application, user_id, Application.status == "rejected").label("rejections"),
            _count(EmailDraft, user_id, EmailDraft.status == "pending_review").label(
                "pending_drafts"
            ),
            _count(
                FollowUpTask,
                user_id,
                FollowUpTask.status == "pending",
                FollowUpTask.due_at <= func.now(),
            ).label("follow_ups_due"),
        )
    ).one()
    return Summary.model_validate(row._asdict())
