"""Projection, outbox, and archive ORM models."""
import uuid
from datetime import datetime
from typing import Any

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
from sqlalchemy.dialects.postgresql import BYTEA, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from atlas.infrastructure.db._orm_base import Base, gen_uuid, now_utc


class ProjectedAccidentRecordModel(Base):
    __tablename__ = "projected_accident_records"
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accident_events.id"), primary_key=True
    )
    projection_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    completeness_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    unresolved_conflict_fields: Mapped[list[str]] = mapped_column(
        JSONB, default=list, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


_OUTBOX_EVENT_TYPES = ("CLAIMS_UPDATED", "ECHO_CROSSREF_REQUESTED")


class OutboxEventModel(Base):
    __tablename__ = "outbox_events"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    event_type: Mapped[str] = mapped_column(String(255), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="PENDING", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'PROCESSED', 'FAILED', 'DEAD_LETTER')",
            name="ck_outbox_events_status",
        ),
        CheckConstraint(
            f"event_type IN ({', '.join(repr(e) for e in _OUTBOX_EVENT_TYPES)})",
            name="ck_outbox_events_event_type",
        ),
        Index(
            "ix_outbox_events_pending_created",
            "created_at",
            "id",
            postgresql_where=text("status = 'PENDING'"),
        ),
        Index(
            "ix_outbox_events_failed_retry_created",
            text("next_attempt_at ASC NULLS FIRST"),
            "created_at",
            "id",
            postgresql_where=text("status = 'FAILED'"),
        ),
        Index(
            "ix_outbox_events_unprocessed_created",
            "created_at",
            "id",
            postgresql_where=text("status IN ('PENDING', 'PROCESSING', 'FAILED')"),
        ),
        Index(
            "ix_outbox_events_processing_locked",
            "locked_at",
            "id",
            postgresql_where=text("status = 'PROCESSING'"),
        ),
    )


class OutboxWorkerHeartbeatModel(Base):
    __tablename__ = "outbox_worker_heartbeats"
    worker_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    last_loop_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_successful_batch_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    __table_args__ = (
        Index("ix_outbox_worker_heartbeats_last_loop", "last_loop_at"),
        Index("ix_outbox_worker_heartbeats_last_success", "last_successful_batch_at"),
    )


class AccidentProjectionHistoryModel(Base):
    __tablename__ = "accident_projection_history"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    accident_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accident_events.id"), nullable=False
    )
    projection_version: Mapped[int] = mapped_column(Integer, nullable=False)
    caused_by_conflict_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claim_conflicts.id"), nullable=True
    )
    caused_by_ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingestion_runs.id"), nullable=True
    )
    caused_by_outbox_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("outbox_events.id"), nullable=True
    )
    projected_record_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    projected_record_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    changed_fields: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    prev_hash: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
    row_hash: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
    __table_args__ = (
        Index(
            "uq_projection_history_version",
            "accident_event_id",
            "projection_version",
            unique=True,
            postgresql_include=["id"],
        ),
        Index(
            "uq_projection_history_outbox_event",
            "caused_by_outbox_event_id",
            unique=True,
            postgresql_where=text("caused_by_outbox_event_id IS NOT NULL"),
        ),
    )


class ArchiveManifestModel(Base):
    __tablename__ = "archive_manifests"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    object_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    date_range_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    date_range_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_system: Mapped[str] = mapped_column(String(255), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum: Mapped[str] = mapped_column(String(255), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    created_by_process_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
