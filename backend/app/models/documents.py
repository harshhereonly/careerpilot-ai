import uuid
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, OwnedMixin, in_check, jsonb_default, owned_fk, owner_key

ATTACHMENT_KINDS = ("resume", "cover_letter", "other")


class Resume(Base, OwnedMixin):
    __tablename__ = "resumes"
    __table_args__ = (
        owner_key("resumes"),
        # At most one default resume per user.
        Index(
            "uq_resumes_user_default", "user_id", unique=True, postgresql_where=text("is_default")
        ),
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)
    storage_path: Mapped[str | None] = mapped_column(Text)  # Supabase Storage object path
    original_filename: Mapped[str | None] = mapped_column(Text)
    mime_type: Mapped[str | None] = mapped_column(Text)
    parsed_text: Mapped[str | None] = mapped_column(Text)
    parsed_data: Mapped[dict[str, Any]] = mapped_column(
        server_default=jsonb_default(), default=dict
    )
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


class ResumeSkill(Base, OwnedMixin):
    __tablename__ = "resume_skills"
    __table_args__ = (
        UniqueConstraint("resume_id", "skill", name="uq_resume_skills_resume_skill"),
        *owned_fk("resume_skills", "resume_id", "resumes"),
    )

    resume_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    skill: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(Text)


class Attachment(Base, OwnedMixin):
    """File metadata for anything attached to a draft (resume PDF, generated cover letter PDF)."""

    __tablename__ = "attachments"
    __table_args__ = (
        in_check("kind", ATTACHMENT_KINDS, "kind"),
        CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name="size_non_negative"),
        UniqueConstraint("user_id", "storage_path", name="uq_attachments_user_storage_path"),
        *owned_fk("attachments", "draft_id", "email_drafts"),
        *owned_fk("attachments", "resume_id", "resumes", "SET NULL"),
    )

    draft_id: Mapped[uuid.UUID | None] = mapped_column()
    resume_id: Mapped[uuid.UUID | None] = mapped_column()
    kind: Mapped[str] = mapped_column(Text, nullable=False, server_default="other")
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
