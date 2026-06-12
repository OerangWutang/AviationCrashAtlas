"""Core ingestion aggregate ORM models."""
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import BYTEA, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from atlas.infrastructure.db._orm_base import Base, ComplianceMixin, gen_uuid, now_utc


class SourceModel(Base):
    __tablename__ = "sources"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    reliability_tier: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    field_mapping_json: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    __table_args__ = (
        CheckConstraint("reliability_tier >= 1", name="ck_sources_reliability_tier_ge_1"),
        CheckConstraint("kind IN ('EXTERNAL', 'INTERNAL')", name="ck_sources_kind"),
    )


class IngestionRunModel(Base):
    __tablename__ = "ingestion_runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(50), default="RUNNING")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (
        CheckConstraint(
            "status IN ('RUNNING', 'FINISHED', 'FAILED')",
            name="ck_ingestion_runs_status",
        ),
    )


class RawSnapshotModel(Base):
    __tablename__ = "raw_snapshots"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), nullable=False
    )
    ingestion_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingestion_runs.id"), nullable=False
    )
    payload_hash: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    source_record_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw_payload_hash: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    submission_hash: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    submission_fingerprint_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    ingestion_result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "ingestion_run_id",
            name="uq_raw_snapshot_ingestion_key",
        ),
        Index(
            "ix_raw_snapshot_source_record",
            "source_id",
            "source_record_id",
            postgresql_where=text("source_record_id IS NOT NULL"),
        ),
        CheckConstraint(
            "(raw_payload_hash IS NULL) = (submission_fingerprint_json IS NULL)",
            name="ck_raw_snapshots_audit_pair_consistent",
        ),
    )


class AccidentEventModel(Base, ComplianceMixin):
    __tablename__ = "accident_events"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    merged_into_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accident_events.id"), nullable=True
    )


class ClaimModel(Base, ComplianceMixin):
    __tablename__ = "claims"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accident_events.id"), nullable=False
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), nullable=False, index=True
    )
    raw_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_snapshots.id"), nullable=True
    )
    field_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    field_value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    claim_type: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    superseded_by_claim_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claims.id"), nullable=True
    )
    __table_args__ = (
        CheckConstraint(
            "claim_type IN ('RAW', 'CONFIRMED', 'MANUAL_OVERRIDE', 'SUPERSEDED')",
            name="ck_claims_claim_type",
        ),
        Index("ix_claims_event_created_id", "event_id", "created_at", "id"),
        Index(
            "ix_claims_active_event",
            "event_id",
            postgresql_where=text("claim_type IN ('RAW', 'CONFIRMED', 'MANUAL_OVERRIDE')"),
        ),
        Index(
            "ix_claims_active_event_field",
            "event_id",
            "field_name",
            postgresql_where=text("claim_type IN ('RAW', 'CONFIRMED', 'MANUAL_OVERRIDE')"),
        ),
        Index("ix_claims_raw_snapshot_id", "raw_snapshot_id"),
        Index(
            "ix_claims_superseded_by_claim_id",
            "superseded_by_claim_id",
            postgresql_where=text("superseded_by_claim_id IS NOT NULL"),
        ),
    )


class ClaimHistoryModel(Base):
    __tablename__ = "claim_history"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claims.id"), nullable=False, index=True
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accident_events.id"), nullable=False
    )
    from_value: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    to_value: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    from_claim_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    to_claim_type: Mapped[str] = mapped_column(String(50), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False, default="updated")
    reason: Mapped[str] = mapped_column(Text, default="")
    modifier_type: Mapped[str] = mapped_column(String(50), nullable=False)
    modifier_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    prev_hash: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
    row_hash: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
    __table_args__ = (
        CheckConstraint(
            "action IN ('updated', 'created', 'superseded', 'merged', 'reactivated')",
            name="ck_claim_history_action",
        ),
        CheckConstraint(
            "from_claim_type IS NULL OR from_claim_type IN "
            "('RAW', 'CONFIRMED', 'MANUAL_OVERRIDE', 'SUPERSEDED')",
            name="ck_claim_history_from_claim_type",
        ),
        CheckConstraint(
            "to_claim_type IN ('RAW', 'CONFIRMED', 'MANUAL_OVERRIDE', 'SUPERSEDED')",
            name="ck_claim_history_to_claim_type",
        ),
        CheckConstraint(
            "modifier_type IN ('USER', 'INGESTION', 'SYSTEM')",
            name="ck_claim_history_modifier_type",
        ),
        Index("ix_claim_history_event_created_id", "event_id", "created_at", "id"),
    )


class ClaimConflictModel(Base):
    __tablename__ = "claim_conflicts"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accident_events.id"), nullable=False
    )
    field_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="OPEN", index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_modified_reason: Mapped[str] = mapped_column(String(50), default="INITIAL")
    last_modified_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    winning_claim_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claims.id"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (
        CheckConstraint("status IN ('OPEN', 'RESOLVED')", name="ck_claim_conflicts_status"),
        CheckConstraint(
            "last_modified_reason IN "
            "('INITIAL', 'NEW_EVIDENCE', 'EVIDENCE_UPDATED', "
            "'USER_RESOLVED', 'USER_REOPENED', 'SYSTEM_AUTO_CLOSED')",
            name="ck_claim_conflicts_last_modified_reason",
        ),
        Index(
            "uq_open_conflict_event_field",
            "event_id",
            "field_name",
            unique=True,
            postgresql_where=text("status = 'OPEN'"),
        ),
        Index("ix_claim_conflicts_event_created_id", "event_id", "created_at", "id"),
        Index(
            "ix_claim_conflicts_resolved_winning_claim",
            "winning_claim_id",
            postgresql_where=text("status = 'RESOLVED' AND winning_claim_id IS NOT NULL"),
        ),
    )


class ClaimConflictClaimModel(Base):
    __tablename__ = "claim_conflict_claims"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    conflict_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claim_conflicts.id"), nullable=False
    )
    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claims.id"), nullable=False, index=True
    )
    __table_args__ = (UniqueConstraint("conflict_id", "claim_id", name="uq_conflict_claim"),)


class ConflictActivityLogModel(Base):
    __tablename__ = "conflict_activity_log"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    conflict_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claim_conflicts.id"), nullable=False
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accident_events.id"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    to_status: Mapped[str] = mapped_column(String(50), nullable=False)
    modifier_type: Mapped[str] = mapped_column(String(50), nullable=False)
    modifier_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    version_at_moment: Mapped[int] = mapped_column(Integer, nullable=False)
    claims_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    prev_hash: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
    row_hash: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
    __table_args__ = (
        CheckConstraint(
            "from_status IS NULL OR from_status IN ('OPEN', 'RESOLVED')",
            name="ck_conflict_activity_from_status",
        ),
        CheckConstraint("to_status IN ('OPEN', 'RESOLVED')", name="ck_conflict_activity_to_status"),
        CheckConstraint(
            "modifier_type IN ('USER', 'INGESTION', 'SYSTEM')",
            name="ck_conflict_activity_modifier_type",
        ),
        UniqueConstraint("conflict_id", "sequence", name="uq_conflict_activity_sequence"),
        Index("ix_conflict_activity_event_created_id", "event_id", "created_at", "id"),
    )
