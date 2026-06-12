"""Publication, search-index, and map-index ORM models."""
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import BYTEA, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from atlas.infrastructure.db._orm_base import Base, gen_uuid, now_utc


class PublicEventPageModel(Base):
    __tablename__ = "public_event_pages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accident_events.id"), nullable=False
    )
    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    short_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    narrative_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="DRAFT", server_default=text("'DRAFT'")
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    first_published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retraction_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT', 'IN_REVIEW', 'APPROVED', 'PUBLISHED', 'ARCHIVED', 'RETRACTED')",
            name="ck_public_event_pages_status",
        ),
        CheckConstraint(
            "status <> 'PUBLISHED' OR last_published_at IS NOT NULL",
            name="ck_public_event_pages_published_requires_timestamp",
        ),
        CheckConstraint(
            "status <> 'RETRACTED' OR retracted_at IS NOT NULL",
            name="ck_public_event_pages_retracted_requires_timestamp",
        ),
        CheckConstraint(
            "version >= 1",
            name="ck_public_event_pages_version_ge_1",
        ),
        Index("uq_public_event_pages_slug", "slug", unique=True),
        Index("uq_public_event_pages_event_id", "event_id", unique=True),
        Index(
            "ix_public_event_pages_published_pub_id",
            text("last_published_at DESC"),
            text("id DESC"),
            postgresql_where=text("status = 'PUBLISHED'"),
        ),
        Index(
            "ix_public_event_pages_status_updated",
            "status",
            text("updated_at DESC"),
            text("id DESC"),
        ),
    )


class PublicEventPageRevisionModel(Base):
    """Immutable audit row written for every editorial transition."""

    __tablename__ = "public_event_page_revisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    page_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("public_event_pages.id"), nullable=False
    )
    version_at_moment: Mapped[int] = mapped_column(Integer, nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    short_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    narrative_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    editor_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    transition_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    correction_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )
    prev_hash: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
    row_hash: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "from_status IS NULL OR from_status IN "
            "('DRAFT', 'IN_REVIEW', 'APPROVED', 'PUBLISHED', 'ARCHIVED', 'RETRACTED')",
            name="ck_public_event_page_revisions_from_status",
        ),
        CheckConstraint(
            "to_status IN ('DRAFT', 'IN_REVIEW', 'APPROVED', 'PUBLISHED', 'ARCHIVED', 'RETRACTED')",
            name="ck_public_event_page_revisions_to_status",
        ),
        CheckConstraint(
            "version_at_moment >= 1",
            name="ck_public_event_page_revisions_version_ge_1",
        ),
        Index(
            "ix_public_event_page_revisions_page_version",
            "page_id",
            "version_at_moment",
            "id",
        ),
    )


class SearchIndexEntryModel(Base):
    """Materialized search-index row for one PUBLISHED public event."""

    __tablename__ = "search_index_entries"

    page_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public_event_pages.id", ondelete="CASCADE"),
        primary_key=True,
    )
    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    short_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    operator: Mapped[str | None] = mapped_column(String(300), nullable=True)
    aircraft_type: Mapped[str | None] = mapped_column(String(300), nullable=True)
    country: Mapped[str | None] = mapped_column(String(300), nullable=True)
    event_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    fatalities_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence_band: Mapped[str] = mapped_column(String(10), nullable=False)
    last_published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    indexed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )
    search_vector: Mapped[str] = mapped_column(TSVECTOR(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "confidence_band IN ('high', 'medium', 'low', 'unknown')",
            name="ck_search_index_entries_confidence_band",
        ),
        Index(
            "ix_search_index_entries_search_vector",
            "search_vector",
            postgresql_using="gin",
        ),
        Index("ix_search_index_entries_operator", "operator"),
        Index("ix_search_index_entries_aircraft_type", "aircraft_type"),
        Index("ix_search_index_entries_event_date", "event_date"),
        Index(
            "ix_search_index_entries_pub_id",
            text("last_published_at DESC"),
            text("page_id DESC"),
        ),
    )


class MapIndexEntryModel(Base):
    """Materialised geo-index row for one PUBLISHED public event."""

    __tablename__ = "map_index_entries"

    page_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public_event_pages.id", ondelete="CASCADE"),
        primary_key=True,
    )
    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    operator: Mapped[str | None] = mapped_column(String(300), nullable=True)
    aircraft_type: Mapped[str | None] = mapped_column(String(300), nullable=True)
    country: Mapped[str | None] = mapped_column(String(300), nullable=True)
    event_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    fatalities_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence_band: Mapped[str] = mapped_column(String(10), nullable=False)
    last_published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    indexed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )
    geom: Mapped[Any] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "confidence_band IN ('high', 'medium', 'low', 'unknown')",
            name="ck_map_index_entries_confidence_band",
        ),
        Index("ix_map_index_entries_operator", "operator"),
        Index("ix_map_index_entries_aircraft_type", "aircraft_type"),
        Index("ix_map_index_entries_event_date", "event_date"),
        Index(
            "ix_map_index_entries_pub_id",
            text("last_published_at DESC"),
            text("page_id DESC"),
        ),
    )
