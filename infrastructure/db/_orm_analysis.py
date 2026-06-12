"""HFACS taxonomy, SHELO factors, NL search, and metering ORM models."""
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
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
from sqlalchemy.dialects.postgresql import BYTEA, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from atlas.infrastructure.db._orm_base import Base, ComplianceMixin, gen_uuid, now_utc


class HfacsCategoryModel(Base):
    __tablename__ = "hfacs_categories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    tier_code: Mapped[str] = mapped_column(String(4), nullable=False)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    tier: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    is_custom: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "tier IN ('ORGANIZATIONAL', 'SUPERVISION', 'PRECONDITIONS', 'UNSAFE_ACTS')",
            name="ck_hfacs_categories_tier",
        ),
        Index("uq_hfacs_categories_code", "code", unique=True),
    )


class HfacsSubcategoryModel(Base):
    __tablename__ = "hfacs_subcategories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hfacs_categories.id"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_custom: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )

    __table_args__ = (
        Index("uq_hfacs_subcategories_code", "code", unique=True),
        Index("ix_hfacs_subcategories_category", "category_id"),
    )


class EventHfacsAttributionModel(Base):
    __tablename__ = "event_hfacs_attributions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accident_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hfacs_categories.id"),
        nullable=False,
    )
    subcategory_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hfacs_subcategories.id"),
        nullable=True,
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    editor_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_event_hfacs_attributions_confidence_range",
        ),
        Index(
            "ix_event_hfacs_attributions_event",
            "event_id",
        ),
    )


class SheloFactorModel(Base):
    __tablename__ = "shelo_factors"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accident_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    factor_class: Mapped[str] = mapped_column(String(20), nullable=False)
    label: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    editor_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "factor_class IN ('SOFTWARE', 'HARDWARE', 'ENVIRONMENT', 'LIVEWARE', 'OTHER')",
            name="ck_shelo_factors_class",
        ),
        Index("ix_shelo_factors_event", "event_id"),
    )


class SheloFactorInteractionModel(Base):
    __tablename__ = "shelo_factor_interactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accident_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_factor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shelo_factors.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_factor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shelo_factors.id", ondelete="CASCADE"),
        nullable=False,
    )
    interaction_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    editor_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "interaction_kind IN ('PRECONDITION', 'AGGRAVATED', 'MITIGATED', 'MASKED')",
            name="ck_shelo_factor_interactions_kind",
        ),
        CheckConstraint(
            "source_factor_id <> target_factor_id",
            name="ck_shelo_factor_interactions_no_self_loop",
        ),
        Index(
            "uq_shelo_factor_interactions_natural",
            "event_id",
            "source_factor_id",
            "target_factor_id",
            "interaction_kind",
            unique=True,
        ),
        Index("ix_shelo_factor_interactions_event", "event_id"),
    )


class NlQueryLogModel(Base):
    __tablename__ = "nl_query_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    raw_query: Mapped[str] = mapped_column(Text, nullable=False)
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    parsed_filters: Mapped[Any] = mapped_column(JSONB, nullable=False)
    result_count: Mapped[int] = mapped_column(Integer, nullable=False)
    parser_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    hour_bucket: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "parser_confidence >= 0.0 AND parser_confidence <= 1.0",
            name="ck_nl_query_log_confidence_range",
        ),
        CheckConstraint(
            "result_count >= 0",
            name="ck_nl_query_log_result_count_nonneg",
        ),
        Index("ix_nl_query_log_hour_bucket", "hour_bucket"),
        Index("ix_nl_query_log_query_hash", "query_hash"),
    )


class SavedNlQueryModel(Base):
    __tablename__ = "saved_nl_queries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    raw_query: Mapped[str] = mapped_column(Text, nullable=False)
    frozen_filters: Mapped[Any] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )

    __table_args__ = (Index("ix_saved_nl_queries_user", "user_id"),)


class UsageEventModel(Base, ComplianceMixin):
    __tablename__ = "usage_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    metric_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )
    prev_hash: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
    row_hash: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "metric_kind IN ('TENANT_CLAIM_INGESTED', "
            "'TENANT_REPORT_FILED', "
            "'TENANT_INGESTION_RUN_COMPLETED', "
            "'NL_QUERY_EXECUTED', "
            "'HFACS_ATTRIBUTION_CREATED', 'ECHO_CROSSREF_RUN')",
            name="ck_usage_events_metric_kind",
        ),
        Index(
            "ix_usage_events_tenant_recorded_at",
            "tenant_id",
            "recorded_at",
        ),
        Index(
            "ix_usage_events_metric_recorded_at",
            "metric_kind",
            "recorded_at",
        ),
        Index("ix_usage_events_resource_id", "resource_id"),
    )


class UsageDailyRollupModel(Base):
    __tablename__ = "usage_daily_rollups"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    metric_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "metric_kind IN ('TENANT_CLAIM_INGESTED', "
            "'TENANT_REPORT_FILED', "
            "'TENANT_INGESTION_RUN_COMPLETED', "
            "'NL_QUERY_EXECUTED', "
            "'HFACS_ATTRIBUTION_CREATED', 'ECHO_CROSSREF_RUN')",
            name="ck_usage_daily_rollups_metric_kind",
        ),
        CheckConstraint(
            "count >= 0",
            name="ck_usage_daily_rollups_count_nonneg",
        ),
        UniqueConstraint(
            "tenant_id",
            "metric_kind",
            "day",
            name="uq_usage_daily_rollups_natural",
        ),
        Index("ix_usage_daily_rollups_day", "day"),
    )
