"""Argus signal-detection ORM models."""
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
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from atlas.infrastructure.db._orm_base import Base, gen_uuid, now_utc

_ARGUS_SIGNAL_TYPES = (
    "NEW_SOURCE_CHANGE",
    "TIMELINE_SEQUENCE_CONFLICT",
    "HIGH_CONFLICT_ACCIDENT_RECORD",
    "REPEATED_AIRCRAFT_INVOLVEMENT",
    "REPEATED_OPERATOR_INVOLVEMENT",
    "SOURCE_FETCH_FAILURE_SPIKE",
    "ECHO_STRONG_PRECEDENT_MATCH",
)
_ARGUS_STATUSES = ("OPEN", "CONFIRMED", "DISMISSED", "NEEDS_MORE_REVIEW", "AUTO_RESOLVED")
_ARGUS_SEVERITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
_ARGUS_EVIDENCE_TYPES = (
    "ATLAS_CLAIM",
    "ATLAS_CONFLICT",
    "ATLAS_ACCIDENT_EVENT",
    "ORION_ENTITY",
    "ORION_RELATIONSHIP",
    "CHRONOS_TIMELINE_EVENT",
    "CHRONOS_SEQUENCE_REVIEW",
    "HERMES_SOURCE_CHANGE",
    "HERMES_FETCH_JOB",
    "HERMES_FETCHED_DOCUMENT",
    "ECHO_CROSSREF_RESULT",
)
_ARGUS_DECISIONS = ("CONFIRMED", "DISMISSED", "NEEDS_MORE_REVIEW")


class ArgusSignalModel(Base):
    __tablename__ = "argus_signals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    signal_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="OPEN", index=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    accident_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accident_events.id"), nullable=True, index=True
    )
    primary_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    source_engine: Mapped[str] = mapped_column(String(50), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    first_detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    last_detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )

    __table_args__ = (
        CheckConstraint(
            f"signal_type IN ({', '.join(repr(v) for v in _ARGUS_SIGNAL_TYPES)})",
            name="ck_argus_signals_signal_type",
        ),
        CheckConstraint(
            f"status IN ({', '.join(repr(v) for v in _ARGUS_STATUSES)})",
            name="ck_argus_signals_status",
        ),
        CheckConstraint(
            f"severity IN ({', '.join(repr(v) for v in _ARGUS_SEVERITIES)})",
            name="ck_argus_signals_severity",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_argus_signals_confidence"),
        CheckConstraint("version >= 1", name="ck_argus_signals_version_positive"),
        Index("uq_argus_signals_dedupe_key", "dedupe_key", unique=True),
        Index(
            "ix_argus_signals_last_detected_id_desc",
            "last_detected_at",
            "id",
        ),
    )


class ArgusSignalEvidenceModel(Base):
    __tablename__ = "argus_signal_evidence"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    signal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("argus_signals.id"), nullable=False, index=True
    )
    evidence_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    evidence_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    engine: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    __table_args__ = (
        CheckConstraint(
            f"evidence_type IN ({', '.join(repr(v) for v in _ARGUS_EVIDENCE_TYPES)})",
            name="ck_argus_signal_evidence_type",
        ),
        UniqueConstraint(
            "signal_id", "evidence_type", "evidence_id", name="uq_argus_signal_evidence_link"
        ),
    )


class ArgusSignalReviewModel(Base):
    __tablename__ = "argus_signal_reviews"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    signal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("argus_signals.id"), nullable=False, index=True
    )
    decision: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, index=True
    )

    __table_args__ = (
        CheckConstraint(
            f"decision IN ({', '.join(repr(v) for v in _ARGUS_DECISIONS)})",
            name="ck_argus_signal_reviews_decision",
        ),
    )
