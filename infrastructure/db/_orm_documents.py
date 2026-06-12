"""Uploaded documents, report exports, and compliance ledger ORM models."""
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
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


class UploadedDocumentModel(Base, ComplianceMixin):
    """Metadata record for a PDF/docket file uploaded for ingestion."""

    __tablename__ = "uploaded_documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), nullable=True
    )
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accident_events.id"), nullable=True
    )
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False, default="application/pdf")
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parse_status: Mapped[str] = mapped_column(String(50), nullable=False, default="uploaded")
    parse_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )

    __table_args__ = (
        CheckConstraint(
            "parse_status IN ('uploaded','parsing','parsed','parse_failed',"
            "'ingesting','ingested','ingest_failed')",
            name="ck_uploaded_documents_status",
        ),
        CheckConstraint("size_bytes >= 0", name="ck_uploaded_documents_size_nonneg"),
        Index("ix_uploaded_documents_event_id", "event_id"),
        Index("ix_uploaded_documents_source_id", "source_id"),
        Index("ix_uploaded_documents_created", text("created_at DESC")),
    )


class ReportExportModel(Base):
    """An immutable versioned PDF report export for a case."""

    __tablename__ = "report_exports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accident_events.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    projection_version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    html_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    prev_hash: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
    row_hash: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)

    __table_args__ = (
        UniqueConstraint("event_id", "version", name="uq_report_exports_event_version"),
        Index("ix_report_exports_event_id", "event_id"),
        Index("ix_report_exports_generated", text("generated_at DESC")),
    )


class ComplianceEventModel(Base):
    """Append-only ledger of compliance actions (migration 052)."""

    __tablename__ = "compliance_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        server_default=text("now()"),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "entity_type IN ('accident_event', 'claim', 'tenant_claim', "
            "'uploaded_document', 'usage_event')",
            name="ck_compliance_events_entity_type",
        ),
        CheckConstraint(
            "action IN ('LEGAL_HOLD_APPLIED', 'LEGAL_HOLD_RELEASED', "
            "'REDACTION_APPLIED', 'RETENTION_SET', 'DELETION_APPLIED')",
            name="ck_compliance_events_action",
        ),
        CheckConstraint(
            "actor_type IN ('USER', 'SYSTEM')",
            name="ck_compliance_events_actor_type",
        ),
        CheckConstraint(
            "(actor_type = 'SYSTEM') OR (actor_id IS NOT NULL)",
            name="ck_compliance_events_actor_id_required_for_user",
        ),
        Index(
            "ix_compliance_events_entity",
            "entity_type",
            "entity_id",
            "created_at",
        ),
        Index("ix_compliance_events_action_created", "action", "created_at"),
    )
