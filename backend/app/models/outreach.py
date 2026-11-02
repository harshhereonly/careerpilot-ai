import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, OwnedMixin, in_check, jsonb_default, owned_fk, owner_key

OPPORTUNITY_STATUSES = (
    "saved",
    "interested",
    "applied",
    "interviewing",
    "offer",
    "rejected",
    "closed",
)
APPLICATION_STATUSES = ("draft", "applied", "interviewing", "offer", "rejected", "withdrawn")
CAMPAIGN_STATUSES = ("draft", "active", "paused", "completed")
DRAFT_KINDS = ("initial", "follow_up", "reply")
DRAFT_STATUSES = (
    "draft",
    "pending_review",
    "approved",
    "rejected",
    "sending",
    "sent",
    "failed",
    "cancelled",
)
CONVERSATION_STATUSES = ("open", "replied", "closed", "bounced")
REPLY_CLASSES = (
    "positive",
    "negative",
    "out_of_office",
    "referral",
    "unsubscribe",
    "bounce",
    "other",
)
FOLLOW_UP_STATUSES = ("pending", "drafted", "done", "cancelled")


class Opportunity(Base, OwnedMixin):
    __tablename__ = "opportunities"
    __table_args__ = (
        owner_key("opportunities"),
        in_check("status", OPPORTUNITY_STATUSES, "status"),
        CheckConstraint("match_score IS NULL OR match_score BETWEEN 0 AND 100", name="match_score"),
        Index(
            "uq_opportunities_user_url",
            "user_id",
            text("lower(url)"),
            unique=True,
            postgresql_where=text("url IS NOT NULL"),
        ),
        Index("ix_opportunities_user_status", "user_id", "status"),
        *owned_fk("opportunities", "company_id", "companies", "SET NULL"),
    )

    company_id: Mapped[uuid.UUID | None] = mapped_column()
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    employment_type: Mapped[str | None] = mapped_column(Text)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="saved")
    match_score: Mapped[float | None] = mapped_column(Float)
    match_analysis: Mapped[dict[str, Any]] = mapped_column(
        server_default=jsonb_default(), default=dict
    )


class Application(Base, OwnedMixin):
    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("user_id", "opportunity_id", name="uq_applications_user_opportunity"),
        in_check("status", APPLICATION_STATUSES, "status"),
        Index("ix_applications_user_status", "user_id", "status"),
        *owned_fk("applications", "opportunity_id", "opportunities"),
        *owned_fk("applications", "resume_id", "resumes", "SET NULL"),
    )

    opportunity_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    resume_id: Mapped[uuid.UUID | None] = mapped_column()
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="draft")
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cover_letter: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)


class OutreachCampaign(Base, OwnedMixin):
    __tablename__ = "outreach_campaigns"
    __table_args__ = (
        owner_key("outreach_campaigns"),
        in_check("status", CAMPAIGN_STATUSES, "status"),
        *owned_fk("outreach_campaigns", "resume_id", "resumes", "SET NULL"),
        *owned_fk("outreach_campaigns", "opportunity_id", "opportunities", "SET NULL"),
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="draft")
    resume_id: Mapped[uuid.UUID | None] = mapped_column()
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column()
    settings: Mapped[dict[str, Any]] = mapped_column(server_default=jsonb_default(), default=dict)


class Conversation(Base, OwnedMixin):
    __tablename__ = "conversations"
    __table_args__ = (
        owner_key("conversations"),
        in_check("status", CONVERSATION_STATUSES, "status"),
        Index(
            "uq_conversations_user_thread",
            "user_id",
            "gmail_thread_id",
            unique=True,
            postgresql_where=text("gmail_thread_id IS NOT NULL"),
        ),
        Index("ix_conversations_user_last_message", "user_id", "last_message_at"),
        *owned_fk("conversations", "contact_id", "contacts"),
        *owned_fk("conversations", "campaign_id", "outreach_campaigns", "SET NULL"),
    )

    contact_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column()
    gmail_thread_id: Mapped[str | None] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="open")
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EmailDraft(Base, OwnedMixin):
    """AI-prepared email awaiting human review. The core rule of the product lives here.

    * ``approval_required``: a draft can only be approved, sending, sent or failed if it carries
      ``approved_at`` and ``approved_version`` equals the current ``version``, so an approval always
      refers to the exact text it was given for.
    * A trigger (migration 0002) bumps ``version`` on any subject/body change, withdraws approval
      of an approved draft and refuses edits to drafts that are sending or sent.
    * ``uq_email_drafts_one_initial_per_contact`` is the duplicate protection: one live first email
      per contact. A rejected or cancelled draft does not count.
    """

    __tablename__ = "email_drafts"
    __table_args__ = (
        owner_key("email_drafts"),
        in_check("kind", DRAFT_KINDS, "kind"),
        in_check("status", DRAFT_STATUSES, "status"),
        CheckConstraint(
            # Not a plain "=": a NULL approved_version would make the expression NULL, and a CHECK
            # passes on NULL, so a draft could be marked approved with no version at all.
            "status NOT IN ('approved', 'sending', 'sent', 'failed') "
            "OR (approved_at IS NOT NULL AND approved_version IS NOT DISTINCT FROM version)",
            name="approval_required",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "uq_email_drafts_one_initial_per_contact",
            "user_id",
            "contact_id",
            unique=True,
            postgresql_where=text("kind = 'initial' AND status NOT IN ('rejected', 'cancelled')"),
        ),
        Index("ix_email_drafts_user_status", "user_id", "status"),
        Index("ix_email_drafts_user_template", "user_id", "template_key"),
        *owned_fk("email_drafts", "contact_id", "contacts"),
        *owned_fk("email_drafts", "campaign_id", "outreach_campaigns", "SET NULL"),
        *owned_fk("email_drafts", "conversation_id", "conversations", "SET NULL"),
        *owned_fk("email_drafts", "opportunity_id", "opportunities", "SET NULL"),
        *owned_fk("email_drafts", "resume_id", "resumes", "SET NULL"),
    )

    contact_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column()
    conversation_id: Mapped[uuid.UUID | None] = mapped_column()
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column()
    resume_id: Mapped[uuid.UUID | None] = mapped_column()
    kind: Mapped[str] = mapped_column(Text, nullable=False, server_default="initial")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="draft")
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    template_key: Mapped[str | None] = mapped_column(Text)  # "performance by template" analytics
    ai_provider: Mapped[str | None] = mapped_column(Text)
    ai_model: Mapped[str | None] = mapped_column(Text)
    generation_meta: Mapped[dict[str, Any]] = mapped_column(
        server_default=jsonb_default(), default=dict
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_version: Mapped[int | None] = mapped_column(Integer)
    rejected_reason: Mapped[str | None] = mapped_column(Text)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EmailMessage(Base, OwnedMixin):
    """A message actually sent or received (mirrors Gmail).

    Outbound rows must point at an approved draft: a CHECK requires ``draft_id`` and a trigger
    (migration 0002) requires that draft to be sending or sent. The draft foreign key is NO ACTION
    so a sent draft cannot be deleted while its message exists, yet deleting a whole contact or
    user still works because the cascade removes the messages in the same statement.
    """

    __tablename__ = "email_messages"
    __table_args__ = (
        Index(
            "uq_email_messages_user_gmail",
            "user_id",
            "gmail_message_id",
            unique=True,
            postgresql_where=text("gmail_message_id IS NOT NULL"),
        ),
        in_check("direction", ("outbound", "inbound"), "direction"),
        CheckConstraint(
            "classification IS NULL OR classification IN "
            "(" + ", ".join(f"'{value}'" for value in REPLY_CLASSES) + ")",
            name="classification",
        ),
        CheckConstraint(
            "classification_confidence IS NULL OR classification_confidence BETWEEN 0 AND 1",
            name="classification_confidence",
        ),
        CheckConstraint(
            "direction <> 'outbound' OR draft_id IS NOT NULL", name="outbound_has_draft"
        ),
        *owned_fk("email_messages", "conversation_id", "conversations"),
        *owned_fk("email_messages", "draft_id", "email_drafts", "NO ACTION"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    draft_id: Mapped[uuid.UUID | None] = mapped_column()
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    gmail_message_id: Mapped[str | None] = mapped_column(Text)
    from_address: Mapped[str] = mapped_column(Text, nullable=False)
    to_addresses: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    subject: Mapped[str | None] = mapped_column(Text)
    body_text: Mapped[str | None] = mapped_column(Text)
    body_html: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    classification: Mapped[str | None] = mapped_column(Text)
    classification_confidence: Mapped[float | None] = mapped_column(Float)


class FollowUpTask(Base, OwnedMixin):
    __tablename__ = "follow_up_tasks"
    __table_args__ = (
        in_check("status", FOLLOW_UP_STATUSES, "status"),
        CheckConstraint("sequence_number >= 1", name="sequence_positive"),
        UniqueConstraint(
            "conversation_id", "sequence_number", name="uq_follow_up_tasks_conversation_sequence"
        ),
        Index("ix_follow_up_tasks_user_due", "user_id", "status", "due_at"),
        *owned_fk("follow_up_tasks", "conversation_id", "conversations"),
        *owned_fk("follow_up_tasks", "contact_id", "contacts"),
        *owned_fk("follow_up_tasks", "draft_id", "email_drafts", "SET NULL"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    contact_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    draft_id: Mapped[uuid.UUID | None] = mapped_column()
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
