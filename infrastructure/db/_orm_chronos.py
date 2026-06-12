"""Chronos timeline ORM models."""
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from atlas.infrastructure.db._orm_base import Base, gen_uuid, now_utc

_CHRONOS_EVENT_TYPES = (
    "SCHEDULED_DEPARTURE",
    "ACTUAL_DEPARTURE",
    "TAKEOFF",
    "LAST_CONTACT",
    "EMERGENCY_DECLARED",
    "IMPACT",
    "LANDING",
    "RESCUE_STARTED",
    "INVESTIGATION_OPENED",
    "REPORT_PUBLISHED",
)

_CHRONOS_PRECISIONS = ("EXACT", "MINUTE", "HOUR", "DAY", "APPROXIMATE", "RELATIVE", "UNKNOWN")
_CHRONOS_REVIEW_STATUSES = ("PENDING", "CONFIRMED", "REJECTED", "AUTO_CONFIRMED")


class ChronosTimelineEventModel(Base):
    __tablename__ = "chronos_timeline_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    accident_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accident_events.id"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    occurred_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    timestamp_precision: Mapped[str] = mapped_column(String(20), nullable=False)
    sequence_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    source_claim_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claims.id"), nullable=True
    )
    raw_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_snapshots.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )

    __table_args__ = (
        CheckConstraint(
            f"event_type IN ({', '.join(repr(t) for t in _CHRONOS_EVENT_TYPES)})",
            name="ck_chronos_timeline_events_event_type",
        ),
        CheckConstraint(
            f"timestamp_precision IN ({', '.join(repr(p) for p in _CHRONOS_PRECISIONS)})",
            name="ck_chronos_timeline_events_precision",
        ),
        Index(
            "uq_chronos_timeline_events_idempotent",
            "accident_event_id",
            "event_type",
            "raw_value",
            unique=True,
        ),
    )


class ChronosEventLinkModel(Base):
    __tablename__ = "chronos_event_links"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    accident_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accident_events.id"), nullable=False, index=True
    )
    predecessor_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chronos_timeline_events.id"), nullable=False, index=True
    )
    successor_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chronos_timeline_events.id"), nullable=False, index=True
    )
    relationship_type: Mapped[str] = mapped_column(String(100), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    source_claim_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claims.id"), nullable=True
    )
    raw_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_snapshots.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    __table_args__ = (
        CheckConstraint(
            "predecessor_event_id != successor_event_id", name="ck_chronos_event_links_no_self_link"
        ),
        Index(
            "uq_chronos_event_links_pair",
            "accident_event_id",
            "predecessor_event_id",
            "successor_event_id",
            "relationship_type",
            unique=True,
        ),
    )


class ChronosSequenceReviewModel(Base):
    __tablename__ = "chronos_sequence_reviews"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    accident_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accident_events.id"), nullable=False, index=True
    )
    timeline_event_id_a: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chronos_timeline_events.id"), nullable=False
    )
    timeline_event_id_b: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chronos_timeline_events.id"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            f"status IN ({', '.join(repr(s) for s in _CHRONOS_REVIEW_STATUSES)})",
            name="ck_chronos_sequence_reviews_status",
        ),
        CheckConstraint(
            "timeline_event_id_a != timeline_event_id_b",
            name="ck_chronos_sequence_reviews_no_self_pair",
        ),
        Index(
            "uq_chronos_sequence_reviews_pending_pair",
            text("LEAST(timeline_event_id_a::text, timeline_event_id_b::text)"),
            text("GREATEST(timeline_event_id_a::text, timeline_event_id_b::text)"),
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
        ),
    )
