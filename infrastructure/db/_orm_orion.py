"""Orion knowledge-graph ORM models."""
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from atlas.infrastructure.db._orm_base import Base, gen_uuid, now_utc

_ORION_ENTITY_TYPES = (
    "AIRCRAFT",
    "OPERATOR",
    "AIRPORT",
    "AIRCRAFT_TYPE",
    "MANUFACTURER",
    "INVESTIGATION_AGENCY",
    "COUNTRY",
)
_ORION_REL_TYPES = (
    "INVOLVED_AIRCRAFT",
    "OPERATED_BY",
    "AIRCRAFT_TYPE",
    "MANUFACTURED_BY",
    "OCCURRED_AT",
    "LOCATED_IN",
    "INVESTIGATED_BY",
)
_ORION_REVIEW_STATUSES = ("PENDING", "MERGED", "REJECTED", "AUTO_MERGED")


class OrionEntityModel(Base):
    __tablename__ = "orion_entities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    canonical_name: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    merged_into_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orion_entities.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE', 'MERGED', 'DEPRECATED')",
            name="ck_orion_entities_status",
        ),
        CheckConstraint(
            f"entity_type IN ({', '.join(repr(t) for t in _ORION_ENTITY_TYPES)})",
            name="ck_orion_entities_entity_type",
        ),
    )


class OrionEntityIdentifierModel(Base):
    __tablename__ = "orion_entity_identifiers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orion_entities.id"), nullable=False, index=True
    )
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    identifier_type: Mapped[str] = mapped_column(String(100), nullable=False)
    identifier_value: Mapped[str] = mapped_column(String(500), nullable=False)
    normalized_value: Mapped[str] = mapped_column(String(500), nullable=False)
    source_claim_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claims.id"), nullable=True
    )
    raw_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_snapshots.id"), nullable=True
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    __table_args__ = (
        Index(
            "ix_orion_entity_identifiers_type_norm",
            "identifier_type",
            "normalized_value",
        ),
        UniqueConstraint(
            "entity_id",
            "identifier_type",
            "normalized_value",
            name="uq_orion_entity_identifiers_entity_type_norm",
        ),
        Index(
            "uq_orion_entity_identifiers_active_strong_identity",
            "entity_type",
            "identifier_type",
            "normalized_value",
            unique=True,
            postgresql_where=text("valid_to IS NULL"),
        ),
    )


class OrionRelationshipModel(Base):
    __tablename__ = "orion_relationships"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    subject_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orion_entities.id"), nullable=True, index=True
    )
    relationship_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    object_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orion_entities.id"), nullable=False, index=True
    )
    accident_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accident_events.id"), nullable=False, index=True
    )
    source_claim_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claims.id"), nullable=True
    )
    raw_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_snapshots.id"), nullable=True
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    __table_args__ = (
        CheckConstraint(
            f"relationship_type IN ({', '.join(repr(t) for t in _ORION_REL_TYPES)})",
            name="ck_orion_relationships_type",
        ),
        Index(
            "uq_orion_relationships_event_level",
            "relationship_type",
            "object_entity_id",
            "accident_event_id",
            unique=True,
            postgresql_where=text("subject_entity_id IS NULL"),
        ),
        Index(
            "uq_orion_relationships_entity_level",
            "subject_entity_id",
            "relationship_type",
            "object_entity_id",
            "accident_event_id",
            unique=True,
            postgresql_where=text("subject_entity_id IS NOT NULL"),
        ),
    )


class OrionEntityClaimLinkModel(Base):
    __tablename__ = "orion_entity_claim_links"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orion_entities.id"), nullable=False, index=True
    )
    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claims.id"), nullable=False, index=True
    )
    raw_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_snapshots.id"), nullable=True
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), nullable=False
    )
    accident_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accident_events.id"), nullable=False, index=True
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    __table_args__ = (
        UniqueConstraint(
            "entity_id",
            "claim_id",
            "accident_event_id",
            name="uq_orion_entity_claim_links_entity_claim_event",
        ),
    )


class OrionEntityReviewModel(Base):
    __tablename__ = "orion_entity_reviews"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    candidate_entity_id_a: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orion_entities.id"), nullable=False
    )
    candidate_entity_id_b: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orion_entities.id"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    match_score: Mapped[float] = mapped_column(Float, nullable=False)
    matched_identifiers: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    __table_args__ = (
        CheckConstraint(
            f"status IN ({', '.join(repr(s) for s in _ORION_REVIEW_STATUSES)})",
            name="ck_orion_entity_reviews_status",
        ),
        CheckConstraint(
            f"entity_type IN ({', '.join(repr(t) for t in _ORION_ENTITY_TYPES)})",
            name="ck_orion_entity_reviews_entity_type",
        ),
        Index(
            "uq_orion_entity_reviews_pending_pair",
            text("LEAST(candidate_entity_id_a::text, candidate_entity_id_b::text)"),
            text("GREATEST(candidate_entity_id_a::text, candidate_entity_id_b::text)"),
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
        ),
    )
