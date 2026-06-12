"""Tenancy, tenant features, crossref ORM models."""
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
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
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from atlas.infrastructure.db._orm_base import Base, ComplianceMixin, gen_uuid, now_utc


class TenantModel(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    display_name: Mapped[str] = mapped_column(String(300), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )

    __table_args__ = (Index("uq_tenants_slug", "slug", unique=True),)


class TenantMembershipModel(Base):
    __tablename__ = "tenant_memberships"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    tenant_role: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "tenant_role IN ('OWNER', 'MEMBER', 'READ_ONLY')",
            name="ck_tenant_memberships_role",
        ),
        Index("uq_tenant_memberships_user", "tenant_id", "user_id", unique=True),
        Index("ix_tenant_memberships_user_id", "user_id"),
    )


class TenantSourceModel(Base):
    __tablename__ = "tenant_sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    reliability_tier: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )

    __table_args__ = (
        Index(
            "uq_tenant_sources_tenant_name",
            "tenant_id",
            "name",
            unique=True,
        ),
    )


class TenantIngestionRunModel(Base):
    __tablename__ = "tenant_ingestion_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    tenant_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenant_sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="running", server_default=text("'running'")
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="ck_tenant_ingestion_runs_status",
        ),
        Index("ix_tenant_ingestion_runs_tenant", "tenant_id"),
    )


class TenantClaimModel(Base, ComplianceMixin):
    __tablename__ = "tenant_claims"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accident_events.id"), nullable=False
    )
    tenant_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenant_sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenant_ingestion_runs.id", ondelete="CASCADE"),
        nullable=True,
    )
    field_name: Mapped[str] = mapped_column(String(200), nullable=False)
    field_value: Mapped[Any] = mapped_column(JSONB, nullable=True)
    claim_kind: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="OTHER",
        server_default=text("'OTHER'"),
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "claim_kind IN ('FOQA', 'ASAP', 'OTHER')",
            name="ck_tenant_claims_claim_kind",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)",
            name="ck_tenant_claims_confidence_range",
        ),
        Index("ix_tenant_claims_tenant_event", "tenant_id", "event_id"),
        Index("ix_tenant_claims_tenant_field", "tenant_id", "field_name"),
        Index(
            "ix_tenant_claims_tenant_event_kind",
            "tenant_id",
            "event_id",
            "claim_kind",
        ),
    )


class TenantEventOverlayModel(Base):
    __tablename__ = "tenant_event_overlays"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accident_events.id"), nullable=False
    )
    notes_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    overlay_fields: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )

    __table_args__ = (
        Index(
            "uq_tenant_event_overlays_tenant_event",
            "tenant_id",
            "event_id",
            unique=True,
        ),
    )


class TenantSafetyReportModel(Base):
    """Tenant-private ASAP-style narrative safety report."""

    __tablename__ = "tenant_safety_reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    report_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    narrative_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    deidentified_attested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    external_report_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    submitter_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "report_kind IN ('FOQA', 'ASAP', 'OTHER')",
            name="ck_tenant_safety_reports_kind",
        ),
        Index(
            "ix_tenant_safety_reports_tenant_created",
            "tenant_id",
            text("created_at DESC"),
        ),
    )


class TenantEventAssociationModel(Base):
    """Editorial association between tenant evidence and a public event."""

    __tablename__ = "tenant_event_associations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accident_events.id"),
        nullable=False,
    )
    claim_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenant_claims.id", ondelete="CASCADE"),
        nullable=True,
    )
    safety_report_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenant_safety_reports.id", ondelete="CASCADE"),
        nullable=True,
    )
    association_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "association_kind IN ('RELATED', 'CONTRIBUTED_TO', 'PRECEDED')",
            name="ck_tenant_event_associations_kind",
        ),
        CheckConstraint(
            "(claim_id IS NOT NULL)::int + (safety_report_id IS NOT NULL)::int = 1",
            name="ck_tenant_event_associations_exactly_one_source",
        ),
        Index(
            "ix_tenant_event_associations_tenant_event",
            "tenant_id",
            "event_id",
        ),
        Index("ix_tenant_event_associations_claim", "claim_id"),
        Index(
            "ix_tenant_event_associations_safety_report",
            "safety_report_id",
        ),
    )


class TenantCrossrefResultModel(Base):
    """Tenant-private Echo cross-reference result set."""

    __tablename__ = "tenant_crossref_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    safety_report_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenant_safety_reports.id", ondelete="CASCADE"),
        nullable=True,
    )
    claim_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenant_claims.id", ondelete="CASCADE"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'PENDING'")
    )
    matches_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    matcher_config_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    match_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'COMPLETE', 'FAILED')",
            name="ck_tenant_crossref_results_status",
        ),
        CheckConstraint(
            "(safety_report_id IS NOT NULL)::int + (claim_id IS NOT NULL)::int = 1",
            name="ck_tenant_crossref_results_source_xor",
        ),
        CheckConstraint(
            "match_count >= 0",
            name="ck_tenant_crossref_results_match_count_nonneg",
        ),
        Index(
            "ix_tenant_crossref_results_tenant_report",
            "tenant_id",
            "safety_report_id",
            postgresql_where=text("safety_report_id IS NOT NULL"),
        ),
        Index(
            "ix_tenant_crossref_results_tenant_requested",
            "tenant_id",
            text("requested_at DESC"),
        ),
    )
