"""Hermes web-crawler ORM models."""
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
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
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from atlas.infrastructure.db._orm_base import Base, gen_uuid, now_utc


class HermesSourceModel(Base):
    __tablename__ = "hermes_sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    reliability_tier: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )

    __table_args__ = (
        CheckConstraint(
            "source_type IN ('OFFICIAL_AGENCY','NEWS','DATABASE','ARCHIVE','OTHER')",
            name="ck_hermes_sources_source_type",
        ),
        Index("uq_hermes_sources_name_lower", text("lower(name)"), unique=True),
    )


class HermesCrawlTargetModel(Base):
    __tablename__ = "hermes_crawl_targets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hermes_sources.id"), nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE", index=True)
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_fetch_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    last_fetched_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    last_content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE','PAUSED','DISABLED')",
            name="ck_hermes_crawl_targets_status",
        ),
    )


class HermesFetchJobModel(Base):
    __tablename__ = "hermes_fetch_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    target_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hermes_crawl_targets.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="QUEUED", index=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED')",
            name="ck_hermes_fetch_jobs_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_hermes_fetch_jobs_attempt_count"),
        CheckConstraint("max_attempts >= 1", name="ck_hermes_fetch_jobs_max_attempts"),
        Index(
            "uq_hermes_fetch_jobs_one_active_per_target",
            "target_id",
            unique=True,
            postgresql_where=text("status IN ('QUEUED', 'RUNNING')"),
        ),
        Index(
            "ix_hermes_fetch_jobs_stale_running",
            "lease_expires_at",
            "id",
            postgresql_where=text("status = 'RUNNING' AND lease_expires_at IS NOT NULL"),
        ),
    )


class HermesFetchedDocumentModel(Base):
    __tablename__ = "hermes_fetched_documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    target_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hermes_crawl_targets.id"), nullable=False, index=True
    )
    fetch_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hermes_fetch_jobs.id"), nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    final_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_type: Mapped[str] = mapped_column(String(20), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    content_length: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    storage_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_text_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    __table_args__ = (
        CheckConstraint(
            "content_type IN ('HTML','PDF','TEXT','JSON','XML','BINARY','UNKNOWN')",
            name="ck_hermes_fetched_documents_content_type",
        ),
        CheckConstraint("content_length >= 0", name="ck_hermes_fetched_documents_content_length"),
        UniqueConstraint(
            "target_id", "content_sha256", name="uq_hermes_fetched_documents_target_hash"
        ),
    )


class HermesSourceChangeModel(Base):
    __tablename__ = "hermes_source_changes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    target_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hermes_crawl_targets.id"), nullable=False, index=True
    )
    fetch_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hermes_fetch_jobs.id"), nullable=True, index=True
    )
    previous_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hermes_fetched_documents.id"), nullable=True
    )
    new_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hermes_fetched_documents.id"), nullable=True
    )
    change_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    previous_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    new_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    __table_args__ = (
        CheckConstraint(
            "change_type IN ('FIRST_SEEN','CONTENT_CHANGED','CONTENT_UNCHANGED','FETCH_FAILED')",
            name="ck_hermes_source_changes_change_type",
        ),
    )
