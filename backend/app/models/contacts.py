import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Float, Index, Text, func, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    OwnedMixin,
    in_check,
    jsonb_default,
    lowercase_check,
    owned_fk,
    owner_key,
)

CONTACT_STATUSES = (
    "new",
    "queued",
    "contacted",
    "replied",
    "bounced",
    "do_not_contact",
    "archived",
)
EMAIL_STATUSES = ("unknown", "valid", "risky", "invalid", "catch_all")
CAPTURE_PROVIDERS = ("manual", "csv", "authorized_api")
SUPPRESSION_REASONS = ("opt_out", "bounce", "manual", "complaint")


class Company(Base, OwnedMixin):
    __tablename__ = "companies"
    __table_args__ = (
        owner_key("companies"),
        lowercase_check("domain"),
        Index(
            "uq_companies_user_domain",
            "user_id",
            "domain",
            unique=True,
            postgresql_where=text("domain IS NOT NULL"),
        ),
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str | None] = mapped_column(Text)
    website: Mapped[str | None] = mapped_column(Text)
    industry: Mapped[str | None] = mapped_column(Text)
    size: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)


class CompanyResearch(Base, OwnedMixin):
    __tablename__ = "company_research"
    __table_args__ = (*owned_fk("company_research", "company_id", "companies"),)

    company_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)  # tavily, serper, manual
    query: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    sources: Mapped[list[Any]] = mapped_column(server_default=jsonb_default("array"), default=list)
    raw: Mapped[dict[str, Any]] = mapped_column(server_default=jsonb_default(), default=dict)
    researched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Contact(Base, OwnedMixin):
    __tablename__ = "contacts"
    __table_args__ = (
        owner_key("contacts"),
        in_check("status", CONTACT_STATUSES, "status"),
        in_check("email_status", EMAIL_STATUSES, "email_status"),
        lowercase_check("email"),
        # Duplicate detection: one contact per email per user.
        Index(
            "uq_contacts_user_email",
            "user_id",
            "email",
            unique=True,
            postgresql_where=text("email IS NOT NULL"),
        ),
        Index("ix_contacts_user_status", "user_id", "status"),
        # Fuzzy duplicate lookup for contacts that do not have an email yet.
        Index("ix_contacts_user_name_company", "user_id", text("lower(full_name)"), "company_id"),
        *owned_fk("contacts", "company_id", "companies", "SET NULL"),
    )

    company_id: Mapped[uuid.UUID | None] = mapped_column()
    first_name: Mapped[str | None] = mapped_column(Text)
    last_name: Mapped[str | None] = mapped_column(Text)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    email_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="unknown")
    profile_url: Mapped[str | None] = mapped_column(
        Text
    )  # user-typed reference only; never fetched
    phone: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="new")
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{}'::text[]"), default=list
    )
    last_contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ContactSource(Base, OwnedMixin):
    """Provenance of a contact: which capture provider produced it and when."""

    __tablename__ = "contact_sources"
    __table_args__ = (
        in_check("provider", CAPTURE_PROVIDERS, "provider"),
        *owned_fk("contact_sources", "contact_id", "contacts"),
    )

    contact_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    batch_id: Mapped[uuid.UUID | None] = mapped_column(index=True)  # groups one CSV import
    source_ref: Mapped[str | None] = mapped_column(Text)  # filename, row number, ...
    raw: Mapped[dict[str, Any]] = mapped_column(server_default=jsonb_default(), default=dict)


class EmailVerification(Base, OwnedMixin):
    __tablename__ = "email_verifications"
    __table_args__ = (
        in_check("status", EMAIL_STATUSES, "status"),
        *owned_fk("email_verifications", "contact_id", "contacts"),
    )

    contact_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[float | None] = mapped_column(Float)
    raw: Mapped[dict[str, Any]] = mapped_column(server_default=jsonb_default(), default=dict)
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SuppressionEntry(Base, OwnedMixin):
    """Addresses/domains that must never be emailed (opt-outs, bounces, manual blocks)."""

    __tablename__ = "suppression_list"
    __table_args__ = (
        in_check("reason", SUPPRESSION_REASONS, "reason"),
        lowercase_check("email"),
        lowercase_check("domain"),
        CheckConstraint("(email IS NOT NULL) <> (domain IS NOT NULL)", name="email_xor_domain"),
        Index(
            "uq_suppression_user_email",
            "user_id",
            "email",
            unique=True,
            postgresql_where=text("email IS NOT NULL"),
        ),
        Index(
            "uq_suppression_user_domain",
            "user_id",
            "domain",
            unique=True,
            postgresql_where=text("domain IS NOT NULL"),
        ),
    )

    email: Mapped[str | None] = mapped_column(Text)
    domain: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text, nullable=False, server_default="manual")
    note: Mapped[str | None] = mapped_column(Text)
