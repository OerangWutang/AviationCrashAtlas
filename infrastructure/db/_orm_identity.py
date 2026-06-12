"""Identity, auth, and duplicate-review ORM models."""
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import BYTEA, INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from atlas.infrastructure.db._orm_base import Base, gen_uuid, now_utc


class ApiKeyModel(Base):
    __tablename__ = "api_keys"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    key_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    tenant_role: Mapped[str | None] = mapped_column(String(20), nullable=True)
    mfa_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    mfa_secret_encrypted: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
    mfa_enrolled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mfa_enrolled_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    __table_args__ = (
        CheckConstraint("role IN ('analyst', 'reviewer', 'admin')", name="ck_api_keys_role_valid"),
        CheckConstraint(
            "(tenant_id IS NULL) = (tenant_role IS NULL)",
            name="ck_api_keys_tenant_pair_consistent",
        ),
        CheckConstraint(
            "tenant_role IS NULL OR tenant_role IN ('OWNER', 'MEMBER', 'READ_ONLY')",
            name="ck_api_keys_tenant_role_valid",
        ),
        CheckConstraint(
            "(mfa_required = false) OR (mfa_secret_encrypted IS NOT NULL)",
            name="ck_api_keys_mfa_consistency",
        ),
    )


class ApiKeyAttemptModel(Base):
    """Append-only log of API-key authentication attempts (migration 054)."""

    __tablename__ = "api_key_attempts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    client_ip: Mapped[str] = mapped_column(INET, nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    key_prefix: Mapped[str | None] = mapped_column(String(8), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "char_length(key_prefix) <= 8",
            name="ck_api_key_attempts_key_prefix_len",
        ),
    )


class PendingDuplicateReviewModel(Base):
    __tablename__ = "pending_duplicate_reviews"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    event_id_a: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accident_events.id"), nullable=False, index=True
    )
    event_id_b: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accident_events.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="PENDING", index=True)
    match_score: Mapped[float] = mapped_column(Float, nullable=False)
    matched_fields: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'REJECTED', 'MERGED', 'AUTO_MERGED', 'CONFIRMED_DUPLICATE')",
            name="ck_pending_duplicate_reviews_status",
        ),
        Index(
            "uq_pending_duplicate_reviews_pending_pair",
            text("LEAST(event_id_a, event_id_b)"),
            text("GREATEST(event_id_a, event_id_b)"),
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
        ),
        Index(
            "ix_pending_duplicate_reviews_pending_created_id",
            text("created_at DESC"),
            text("id DESC"),
            postgresql_where=text("status = 'PENDING'"),
        ),
    )


class EventIdentityIndexModel(Base):
    """Synchronous event identity substrate - written in the ingestion transaction."""

    __tablename__ = "event_identity_index"
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accident_events.id"), primary_key=True
    )
    event_date_norm: Mapped[str | None] = mapped_column(String(10), nullable=True)
    registration_norm: Mapped[str | None] = mapped_column(String(50), nullable=True)
    operator_norm: Mapped[str | None] = mapped_column(String(255), nullable=True)
    location_norm: Mapped[str | None] = mapped_column(String(255), nullable=True)
    aircraft_type_norm: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_record_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    registration_norms: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (
        Index("ix_identity_date_reg", "event_date_norm", "registration_norm"),
        Index("ix_identity_date", "event_date_norm"),
    )
