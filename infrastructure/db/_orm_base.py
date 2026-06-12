"""Shared base class, mixin, and helpers for all ORM sub-modules."""
import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def gen_uuid() -> uuid.UUID:
    return uuid.uuid4()


def now_utc() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class ComplianceMixin:
    """Legal hold + retention + redaction columns (migration 052)."""

    legal_hold: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    legal_hold_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    legal_hold_set_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    legal_hold_set_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    redacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    redacted_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    redaction_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
