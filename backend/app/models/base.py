import uuid
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    MetaData,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {
        dict[str, Any]: JSONB,
        list[Any]: JSONB,
        uuid.UUID: UUID(as_uuid=True),
    }


class TimestampMixin:
    # `updated_at` is maintained by a database trigger (see migration 0002), not by the ORM.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UUIDPkMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )


class OwnedMixin(UUIDPkMixin, TimestampMixin):
    """Every user-owned row: UUID pk, timestamps and a user_id owner (cascade on user delete)."""

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )


OnDelete = Literal["CASCADE", "SET NULL", "NO ACTION"]


def owner_key(table: str) -> UniqueConstraint:
    """Makes ``(user_id, id)`` a valid target for same-owner foreign keys (see owned_fk)."""
    return UniqueConstraint("user_id", "id", name=f"uq_{table}_user_id_id")


def owned_fk(
    table: str, column: str, parent: str, on_delete: OnDelete = "CASCADE"
) -> tuple[ForeignKeyConstraint, Index]:
    """Same-owner reference: ``(user_id, column)`` -> ``parent(user_id, id)``.

    A plain foreign key to ``parent.id`` would let a user point a row at somebody else's record,
    because foreign-key checks bypass row-level security. Putting ``user_id`` in the key makes the
    database itself reject cross-tenant references. ``SET NULL`` only clears ``column`` (never
    ``user_id``), which needs PostgreSQL 15+. ``NO ACTION`` is checked at the end of the statement,
    so cascades from a user or contact deletion can finish first.
    """
    action: str | None = {
        "CASCADE": "CASCADE",
        "SET NULL": f"SET NULL ({column})",
        "NO ACTION": None,
    }[on_delete]
    constraint = ForeignKeyConstraint(
        ["user_id", column],
        [f"{parent}.user_id", f"{parent}.id"],
        ondelete=action,
        name=f"fk_{table}_{column}_{parent}",
    )
    return constraint, Index(f"ix_{table}_user_id_{column}", "user_id", column)


def in_check(column: str, values: tuple[str, ...], name: str) -> CheckConstraint:
    quoted = ", ".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{column} IN ({quoted})", name=name)


def lowercase_check(column: str) -> CheckConstraint:
    """Store normalised values so plain unique indexes are enough for case-insensitive dedupe."""
    return CheckConstraint(
        f"{column} IS NULL OR {column} = lower({column})", name=f"{column}_lowercase"
    )


def jsonb_default(kind: str = "object") -> Any:
    return text("'{}'::jsonb" if kind == "object" else "'[]'::jsonb")
