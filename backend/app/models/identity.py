import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    OwnedMixin,
    TimestampMixin,
    UUIDPkMixin,
    in_check,
    jsonb_default,
    owned_fk,
    owner_key,
)

INTEGRATION_STATUSES = ("connected", "disconnected", "error", "expired")
FACT_CATEGORIES = ("education", "experience", "project", "skill", "award", "other")


class User(Base, TimestampMixin):
    """App-level user. `id` equals Supabase `auth.users.id`.

    There is deliberately no FK to `auth.users`, so local Postgres works without Supabase.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    full_name: Mapped[str | None] = mapped_column(Text)


class CandidateProfile(Base, OwnedMixin):
    __tablename__ = "candidate_profiles"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_candidate_profiles_user_id"),
        owner_key("candidate_profiles"),
    )

    headline: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    school: Mapped[str | None] = mapped_column(Text)
    degree: Mapped[str | None] = mapped_column(Text)
    graduation_date: Mapped[date | None] = mapped_column(Date)
    location: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    links: Mapped[dict[str, Any]] = mapped_column(server_default=jsonb_default(), default=dict)
    target_roles: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{}'::text[]"), default=list
    )
    preferences: Mapped[dict[str, Any]] = mapped_column(
        server_default=jsonb_default(), default=dict
    )


class CandidateFact(Base, OwnedMixin):
    """Atomic, user-verified facts. AI generation may only assert claims grounded in these."""

    __tablename__ = "candidate_facts"
    __table_args__ = (
        in_check("category", FACT_CATEGORIES, "category"),
        *owned_fk("candidate_facts", "profile_id", "candidate_profiles"),
    )

    profile_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False, server_default="other")
    fact: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str | None] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))


class UserSettings(Base, OwnedMixin):
    """Outreach safety controls. Defaults are deliberately conservative."""

    __tablename__ = "user_settings"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_user_settings_user_id"),
        CheckConstraint("daily_send_limit BETWEEN 0 AND 100", name="daily_send_limit_range"),
        CheckConstraint("follow_up_delay_days BETWEEN 1 AND 60", name="follow_up_delay_range"),
        CheckConstraint("max_follow_ups BETWEEN 0 AND 5", name="max_follow_ups_range"),
    )

    daily_send_limit: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="10")
    sending_paused: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    follow_up_delay_days: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="5"
    )
    max_follow_ups: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="2")
    timezone: Mapped[str] = mapped_column(Text, nullable=False, server_default="UTC")


class UserIntegration(Base, OwnedMixin):
    """Non-sensitive integration state (e.g. Gmail connected). Tokens live in IntegrationSecret."""

    __tablename__ = "user_integrations"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_user_integrations_user_provider"),
        owner_key("user_integrations"),
        in_check("status", INTEGRATION_STATUSES, "status"),
    )

    provider: Mapped[str] = mapped_column(Text, nullable=False)  # gmail, hunter, tavily, ...
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # email_delivery, discovery, research
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="disconnected")
    account_identifier: Mapped[str | None] = mapped_column(Text)
    scopes: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{}'::text[]"), default=list
    )
    config: Mapped[dict[str, Any]] = mapped_column(server_default=jsonb_default(), default=dict)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IntegrationSecret(Base, OwnedMixin):
    """Fernet-encrypted OAuth/API credentials.

    Row-level security is enabled with NO policies and all table privileges are revoked from the
    client-facing roles (migration 0002): only the backend's privileged connection can read it.
    """

    __tablename__ = "integration_secrets"
    __table_args__ = (
        UniqueConstraint("integration_id", name="uq_integration_secrets_integration_id"),
        *owned_fk("integration_secrets", "integration_id", "user_integrations"),
    )

    integration_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    encrypted_payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


class ActivityLog(Base, UUIDPkMixin):
    """Append-only audit/usage trail (also the basis for future SaaS usage metering)."""

    __tablename__ = "activity_logs"
    __table_args__ = (
        Index("ix_activity_logs_user_created", "user_id", "created_at"),
        Index("ix_activity_logs_entity", "entity_type", "entity_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)  # e.g. draft.approved, email.sent
    entity_type: Mapped[str | None] = mapped_column(Text)
    entity_id: Mapped[uuid.UUID | None] = mapped_column()
    meta: Mapped[dict[str, Any]] = mapped_column(server_default=jsonb_default(), default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
