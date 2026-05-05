"""
ORM models for the claim-based aviation accident schema.

Key invariants enforced here:
- raw_snapshots are never mutated after insert
- claims are never deleted, only superseded or disputed
- accident_records is a derived projection, never a source of truth
- is_winning on Claim is set by ProjectionService, not by writers
"""
from __future__ import annotations

import enum
from datetime import date, datetime
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ── Enumerations ──────────────────────────────────────────────────────────────

class ClaimType(enum.StrEnum):
    CONFIRMED  = "confirmed"   # directly stated in source
    INFERRED   = "inferred"    # derived from context
    DISPUTED   = "disputed"    # contradicted by another source — pending resolution
    REJECTED   = "rejected"    # explicitly discarded during conflict resolution
    SUPERSEDED = "superseded"  # source issued correction
    PENDING    = "pending"     # received, not yet reviewed


class RecordStatus(enum.StrEnum):
    ACTIVE    = "active"
    MERGED    = "merged"
    DISPUTED  = "disputed"
    RETRACTED = "retracted"


class InvestigationStatus(enum.StrEnum):
    PRELIMINARY    = "preliminary"
    FACTUAL        = "factual"
    PROBABLE_CAUSE = "probable_cause"
    FINAL          = "final"
    CLOSED         = "closed"


class DatePrecision(enum.StrEnum):
    EXACT = "exact"
    DAY   = "day"
    MONTH = "month"
    YEAR  = "year"


# ── Ingestion run ledger ───────────────────────────────────────────────────────

class IngestionRun(Base):
    """
    Durable operational record for each ingestion run.

    Answers operational questions:
      - When was NTSB last synced?
      - How many records changed?
      - Which records failed?
      - Which projection steps failed?
      - Which source is stale?

    A row is created at the start of each run (status='running') and updated
    to 'completed' or 'failed' at the end.  Errors are appended to the errors
    JSONB array.
    """
    __tablename__ = "ingestion_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("sources.id"), nullable=True,
        comment="Which source was ingested (NULL for multi-source runs)",
    )
    source_name: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="running",
        comment="running | completed | failed",
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    records_fetched: Mapped[int] = mapped_column(Integer, default=0)
    snapshots_new: Mapped[int] = mapped_column(Integer, default=0)
    snapshots_skipped: Mapped[int] = mapped_column(Integer, default=0)
    events_created: Mapped[int] = mapped_column(Integer, default=0)
    events_updated: Mapped[int] = mapped_column(Integer, default=0)
    claims_written: Mapped[int] = mapped_column(Integer, default=0)
    projection_errors: Mapped[int] = mapped_column(Integer, default=0)
    ingestion_errors: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list[str] | None] = mapped_column(
        JSONB, default=None,
        comment="List of error strings from this run",
    )


# ── API key registry ──────────────────────────────────────────────────────────

class ApiKey(Base):
    """
    Hashed API keys used to authenticate operators against write endpoints.

    The raw key is generated once and shown to the operator; only the
    SHA-256 hex digest is stored.  Roles:
      reviewer — may resolve conflicts
      admin    — may do everything a reviewer can + future admin operations

    is_active=False revokes a key without deleting the audit record.
    """
    __tablename__ = "api_keys"
    __table_args__ = (
        Index("ix_api_key_hash", "key_hash", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True,
                                          comment="SHA-256 hex digest of the raw key")
    operator_id: Mapped[str] = mapped_column(String(100), nullable=False,
                                              comment="Human-readable identity (username/email)")
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="reviewer",
                                      comment="reviewer | admin")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        comment="Optional expiry. NULL = never expires. Checked on every auth request.",
    )
    description: Mapped[str | None] = mapped_column(Text,
                                                     comment="Optional note about this key's purpose")




class Source(Base):
    __tablename__ = "sources"

    id: Mapped[str]  = mapped_column(String(36), primary_key=True)
    short_name: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    tier: Mapped[int] = mapped_column(Integer, nullable=False)
    license_type: Mapped[str] = mapped_column(String(50), nullable=False)
    base_url: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    ingestion_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    raw_snapshots: Mapped[list[RawSnapshot]] = relationship(back_populates="source")
    claims: Mapped[list[Claim]] = relationship(back_populates="source")


# ── Raw snapshots (immutable archive) ─────────────────────────────────────────

class RawSnapshot(Base):
    """
    Exact bytes received from a source — never mutated post-insert.
    payload_hash (SHA-256) makes ingestion idempotent.
    """
    __tablename__ = "raw_snapshots"
    __table_args__ = (
        UniqueConstraint("source_id", "payload_hash", name="uq_snapshot_source_hash"),
        Index("ix_snapshot_source_record", "source_id", "source_record_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(36), ForeignKey("sources.id"), nullable=False)
    source_record_id: Mapped[str | None] = mapped_column(String(200))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    ingestion_run_id: Mapped[str | None] = mapped_column(String(36))

    source: Mapped[Source] = relationship(back_populates="raw_snapshots")
    claims: Mapped[list[Claim]] = relationship(back_populates="snapshot")


# ── Accident events ───────────────────────────────────────────────────────────

class AccidentEvent(Base):
    __tablename__ = "accident_events"
    __table_args__ = (
        Index("ix_event_location", "location_point", postgresql_using="gist"),
        Index("ix_event_occurred_at", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    canonical_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    occurred_at_precision: Mapped[str] = mapped_column(String(10), default=DatePrecision.DAY.value)
    location_point: Mapped[Any | None] = mapped_column(Geometry("POINT", srid=4326))
    location_text: Mapped[str | None] = mapped_column(Text)
    country_code: Mapped[str | None] = mapped_column(String(3))
    overall_confidence_score: Mapped[float | None] = mapped_column(Numeric(4, 3))
    record_status: Mapped[str] = mapped_column(String(20), default=RecordStatus.ACTIVE.value)
    merged_into_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("accident_events.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    claims: Mapped[list[Claim]] = relationship(back_populates="event")
    conflicts: Mapped[list[ClaimConflict]] = relationship(back_populates="event")
    record: Mapped[AccidentRecord | None] = relationship(back_populates="event", uselist=False)
    source_documents: Mapped[list[SourceDocument]] = relationship(back_populates="event")


# ── Claims (the truth store) ──────────────────────────────────────────────────

class Claim(Base):
    """
    One field-level assertion from one source about one event.

    field_value is always a JSON envelope:
      {"v": <typed_value>, "type": "<python_type_name>"}

    Use claim_value.encode() / decode() to serialize/deserialize safely.
    Never write raw Python objects directly into field_value.
    """
    __tablename__ = "claims"
    __table_args__ = (
        Index("ix_claim_event_field", "event_id", "field_name"),
        Index("ix_claim_winning", "event_id", "is_winning"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(36), ForeignKey("accident_events.id"), nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), ForeignKey("sources.id"), nullable=False)
    snapshot_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("raw_snapshots.id"))
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    field_value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    claim_type: Mapped[str] = mapped_column(String(20), nullable=False, default=ClaimType.CONFIRMED.value)
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_winning: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    event: Mapped[AccidentEvent] = relationship(back_populates="claims")
    source: Mapped[Source] = relationship(back_populates="claims")
    snapshot: Mapped[RawSnapshot | None] = relationship(back_populates="claims")
    history: Mapped[list[ClaimHistory]] = relationship(back_populates="claim")
    source_document_links: Mapped[list[ClaimSourceDocument]] = relationship(
        back_populates="claim", cascade="all, delete-orphan"
    )


class ClaimHistory(Base):
    """Immutable audit trail for every mutation to a Claim row."""
    __tablename__ = "claim_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    claim_id: Mapped[str] = mapped_column(String(36), ForeignKey("claims.id"), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    old_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    new_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    old_claim_type: Mapped[str | None] = mapped_column(String(20))
    new_claim_type: Mapped[str | None] = mapped_column(String(20))
    change_reason: Mapped[str | None] = mapped_column(Text)
    changed_by: Mapped[str | None] = mapped_column(String(100))

    claim: Mapped[Claim] = relationship(back_populates="history")


# ── Claim conflicts ───────────────────────────────────────────────────────────

class ClaimConflict(Base):
    __tablename__ = "claim_conflicts"
    __table_args__ = (
        Index("ix_conflict_event_field", "event_id", "field_name"),
        # Prevent duplicate rows if projection re-runs for the same event.
        # Claim IDs are canonicalised (min first) before insert so (A,B) and (B,A)
        # cannot both exist — see ClaimWriter._detect_conflicts.
        UniqueConstraint("event_id", "field_name", "claim_a_id", "claim_b_id",
                         name="uq_conflict_claim_pair"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(36), ForeignKey("accident_events.id"), nullable=False)
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    claim_a_id: Mapped[str] = mapped_column(String(36), ForeignKey("claims.id"), nullable=False)
    claim_b_id: Mapped[str] = mapped_column(String(36), ForeignKey("claims.id"), nullable=False)

    # Lifecycle status — one of: "open" | "resolved" | "obsolete"
    # open: unresolved field disagreement, blocks projection of this field
    # resolved: manually accepted one claim as authoritative
    # obsolete: both conflicting claims have been superseded; no longer relevant
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")

    # Structured resolution — populated when status = "resolved"
    # These fields make the resolution auditable and prevent auto-reconciliation
    # from reinstating a claim that was explicitly rejected.
    resolution: Mapped[str | None] = mapped_column(Text)
    resolution_type: Mapped[str | None] = mapped_column(
        String(30),
        comment=(
            "claim_accepted | claim_rejected | claims_merged | "
            "source_corrected | not_applicable | manual_override"
        ),
    )
    accepted_claim_id: Mapped[str | None] = mapped_column(
        String(36),
        comment="The claim whose value was accepted as authoritative for this field",
    )
    rejected_claim_ids: Mapped[list[str] | None] = mapped_column(
        ARRAY(String(36)),
        comment="Claims explicitly rejected during resolution",
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[str | None] = mapped_column(String(100))

    # Obsolescence fields — populated when status = "obsolete"
    obsolete_reason: Mapped[str | None] = mapped_column(Text)
    obsolete_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Optimistic-lock counter — incremented on every state transition.
    # The resolve endpoint reads this value, checks it hasn't changed, then
    # increments it.  If two reviewers race on the same conflict the second
    # request will find status='resolved' (not 'open') and receive a 409.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    event: Mapped[AccidentEvent] = relationship(back_populates="conflicts")


# ── Accident records (read projection) ───────────────────────────────────────

class AccidentRecord(Base):
    """
    Denormalized read-projection rebuilt from winning claims by ProjectionService.
    NEVER write to this table directly. Always rebuild via ProjectionService.

    confidence_breakdown stores the full factor list so the API can expose
    why a score is what it is — fixes the "confidence theater" problem.
    """
    __tablename__ = "accident_records"
    __table_args__ = (
        Index("ix_record_occurred_at", "occurred_at"),
        Index("ix_record_location", "location_point", postgresql_using="gist"),
        Index("ix_record_severity", "injury_severity"),
        Index("ix_record_confidence", "confidence_score"),
    )

    id: Mapped[str] = mapped_column(String(36), ForeignKey("accident_events.id"), primary_key=True)

    # Temporal
    # occurred_at stores the local accident time as a timezone-naive datetime.
    # NTSB source times are local to the accident site; we do not know the UTC
    # offset at ingestion time.  DateTime(timezone=False) prevents the driver
    # from silently attaching a false UTC offset.
    # occurred_at_precision records how fine-grained the time value is:
    #   "exact" = date + HH:MM parsed from source
    #   "day"   = date only (no time component)
    #   "year"  = only the year is known
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    occurred_date: Mapped[date | None] = mapped_column(Date)
    occurred_year: Mapped[int | None] = mapped_column(Integer)
    occurred_at_precision: Mapped[str | None] = mapped_column(String(10))

    # Spatial — PostGIS point AND raw floats for API responses (fixes map breakage)
    location_point: Mapped[Any | None] = mapped_column(Geometry("POINT", srid=4326))
    location_lat: Mapped[float | None] = mapped_column(Numeric(9, 6))
    location_lon: Mapped[float | None] = mapped_column(Numeric(9, 6))
    location_text: Mapped[str | None] = mapped_column(Text)
    country_code: Mapped[str | None] = mapped_column(String(3))
    state_code: Mapped[str | None] = mapped_column(String(10))

    # Aircraft
    aircraft_make: Mapped[str | None] = mapped_column(String(200))
    aircraft_model: Mapped[str | None] = mapped_column(String(200))
    aircraft_registration: Mapped[str | None] = mapped_column(String(20))
    aircraft_amateur_built: Mapped[bool | None] = mapped_column(Boolean)

    # Operator
    operator_name: Mapped[str | None] = mapped_column(String(300))

    # Flight
    phase_of_flight: Mapped[str | None] = mapped_column(String(50))
    purpose_of_flight: Mapped[str | None] = mapped_column(String(100))
    weather_condition: Mapped[str | None] = mapped_column(String(20))

    # Outcome
    injury_severity: Mapped[str | None] = mapped_column(String(20))
    fatalities_total: Mapped[int | None] = mapped_column(Integer)
    fatalities_crew: Mapped[int | None] = mapped_column(Integer)
    fatalities_passengers: Mapped[int | None] = mapped_column(Integer)
    serious_injuries: Mapped[int | None] = mapped_column(Integer)
    serious_injuries_crew: Mapped[int | None] = mapped_column(Integer)
    serious_injuries_passengers: Mapped[int | None] = mapped_column(Integer)
    minor_injuries: Mapped[int | None] = mapped_column(Integer)
    minor_injuries_crew: Mapped[int | None] = mapped_column(Integer)
    minor_injuries_passengers: Mapped[int | None] = mapped_column(Integer)
    uninjured_crew: Mapped[int | None] = mapped_column(Integer)
    uninjured_passengers: Mapped[int | None] = mapped_column(Integer)
    aboard_total: Mapped[int | None] = mapped_column(Integer)
    aircraft_damage: Mapped[str | None] = mapped_column(String(20))

    # Investigation
    investigation_status: Mapped[str | None] = mapped_column(String(30))
    probable_cause: Mapped[str | None] = mapped_column(Text)
    contributing_factors: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    ntsb_report_number: Mapped[str | None] = mapped_column(String(50))

    # Provenance
    # source_ids: sources behind *winning* projected field values only.
    # claim_source_ids: all sources that contributed any non-superseded claim.
    source_ids: Mapped[list[str] | None] = mapped_column(ARRAY(String(36)))
    claim_source_ids: Mapped[list[str] | None] = mapped_column(
        ARRAY(String(36)), comment="All sources with non-superseded claims, including non-winning"
    )
    primary_source_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("sources.id"))
    confidence_score: Mapped[float | None] = mapped_column(Numeric(4, 3))
    confidence_breakdown: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, comment="Stored factor list so API can expose full score explanation"
    )
    has_conflicts: Mapped[bool] = mapped_column(Boolean, default=False)
    # v20: per-field projection rationale (selection_reason codes).
    # Persisted alongside the projection so the API can surface "why is
    # this value displayed?" without recomputing the rationale on each
    # request.  See migration 0009 for the row shape.
    projection_explanations: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB,
        comment="Per-field projection rationale list; see ProjectionService.",
    )
    # v20: aggregate label over SourceDocument rows for this event.
    # Backed by migration 0009.  Nullable while older rows wait for the
    # next projection rebuild to populate it.
    document_status: Mapped[str | None] = mapped_column(
        Text,
        comment="One of: none_linked | linked_unverified | verified | unavailable | mixed.",
    )
    last_projected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    event: Mapped[AccidentEvent] = relationship(back_populates="record")


# ── Source documents ──────────────────────────────────────────────────────────

class SourceDocument(Base):
    """
    Links to official source documents. URL health checked periodically.
    Only link to URLs that have been verified to exist — never fabricate.
    """
    __tablename__ = "source_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(36), ForeignKey("accident_events.id"), nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), ForeignKey("sources.id"), nullable=False)
    document_type: Mapped[str] = mapped_column(String(30), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    url_verified: Mapped[bool] = mapped_column(
        Boolean, default=False,
        comment="True only after a successful HTTP HEAD check — never set on construction"
    )
    title: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[date | None] = mapped_column(Date)
    is_available: Mapped[bool | None] = mapped_column(Boolean)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Verification metadata — populated by check-links command
    last_http_status: Mapped[int | None] = mapped_column(
        Integer, comment="HTTP status code from most recent check (e.g. 200, 404, 403)"
    )
    last_check_error: Mapped[str | None] = mapped_column(
        Text, comment="Error message or failure reason from most recent check"
    )
    last_check_method: Mapped[str | None] = mapped_column(
        String(10), comment="HTTP method used: HEAD or GET (HEAD→GET fallback)"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    event: Mapped[AccidentEvent] = relationship(back_populates="source_documents")
    source: Mapped[Source] = relationship()
    claim_links: Mapped[list[ClaimSourceDocument]] = relationship(
        back_populates="source_document", cascade="all, delete-orphan"
    )


class ClaimSourceDocument(Base):
    """Join table linking a field-level claim to a supporting document.

    The table is created by migration 0017.  The ORM model must exist because
    the source-document review endpoint inserts links for verified final reports.
    """
    __tablename__ = "claim_source_documents"
    __table_args__ = (
        UniqueConstraint(
            "claim_id", "source_document_id",
            name="uq_claim_source_document_pair",
        ),
        Index("ix_claim_source_documents_claim", "claim_id"),
        Index("ix_claim_source_documents_doc", "source_document_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    claim_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    source_document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("source_documents.id", ondelete="CASCADE"), nullable=False
    )
    link_reason: Mapped[str] = mapped_column(
        String(80), nullable=False, default="source_event_final_report"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    claim: Mapped[Claim] = relationship(back_populates="source_document_links")
    source_document: Mapped[SourceDocument] = relationship(back_populates="claim_links")


# ── Event revisions (timeline) ────────────────────────────────────────────────

class EventRevision(Base):
    """
    Append-only human-readable timeline of changes for a single accident
    event.  Distinct from claim_history (per-claim, structured) — this
    table feeds the "How this record evolved" UI strip.

    See migration 0008 for the documented revision_type values.

    Each row is the result of one observable change: a snapshot first
    seen, a snapshot's content changing, a claim being superseded, a
    conflict opening, a document becoming unavailable, etc.  The
    description column is what the UI displays; old/new value plus
    field_names give consumers enough structure to render their own
    formatting if desired.
    """
    __tablename__ = "event_revisions"
    __table_args__ = (
        Index("ix_event_revisions_event_at", "event_id", "occurred_at"),
        Index("ix_event_revisions_run", "ingestion_run_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("accident_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Loose string discriminator — values documented in migration 0008.
    revision_type: Mapped[str] = mapped_column(String(50), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    source_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("sources.id"), nullable=True
    )
    source_record_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    snapshot_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("raw_snapshots.id"), nullable=True
    )
    claim_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("claims.id"), nullable=True
    )
    conflict_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("claim_conflicts.id"), nullable=True
    )
    source_document_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("source_documents.id"), nullable=True
    )
    ingestion_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("ingestion_runs.id"), nullable=True
    )
    field_names: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    old_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    new_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


# ── Source-record rolling state ───────────────────────────────────────────────

class SourceRecordState(Base):
    """
    Rolling per-source-record state derived from raw_snapshots.

    raw_snapshots is the immutable archive: one row per unique
    payload_hash for a given source.  This table is the aggregate that
    answers "what did source X's record Y look like the last time we
    fetched it, and when did that content actually change?"

    On every ingest we update last_seen_at; we update last_changed_at and
    current_payload_hash only when the new payload differs.  The
    previous_payload_hash + current_field_names columns let the
    revision-builder diff the prior canonical field set against the new
    one to detect added / removed / changed fields without re-reading
    the raw payload.
    """
    __tablename__ = "source_record_state"
    __table_args__ = (
        Index("ix_source_record_state_event", "event_id"),
    )

    source_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("sources.id"), primary_key=True
    )
    source_record_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    event_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("accident_events.id"), nullable=True
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    current_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    current_snapshot_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("raw_snapshots.id"), nullable=True
    )
    previous_payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parser_version: Mapped[str] = mapped_column(
        String(40), nullable=False, server_default="1"
    )
    current_field_names: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text), nullable=True
    )


# ── Duplicate review workflow ─────────────────────────────────────────────────

class DuplicateCandidateReview(Base):
    """Reviewable candidate saying two events may describe the same accident.

    Medium-confidence cross-source matches are intentionally not merged
    automatically. They are stored here so reviewers can confirm or reject the
    relationship and so rejected pairs are not suggested repeatedly.
    """
    __tablename__ = "duplicate_candidates"
    __table_args__ = (
        Index("ix_duplicate_candidates_status", "status", "created_at"),
        Index("ix_duplicate_candidates_events", "source_event_id", "candidate_event_id"),
        UniqueConstraint(
            "source_event_id", "candidate_event_id",
            name="uq_duplicate_candidate_event_pair",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_event_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("accident_events.id"), nullable=True,
        comment="New/incoming event that may be duplicate; nullable for parser-only candidates",
    )
    candidate_event_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accident_events.id"), nullable=False,
        comment="Existing event suggested as the likely duplicate target",
    )
    source_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("sources.id"), nullable=True)
    source_record_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    ingestion_run_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("ingestion_runs.id"), nullable=True)
    match_type: Mapped[str] = mapped_column(String(40), nullable=False, default="fuzzy")
    match_score: Mapped[float] = mapped_column(Numeric(5, 3), nullable=False)
    match_reasons: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending",
        comment="pending | confirmed | rejected | obsolete",
    )
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DuplicateMergeOperation(Base):
    """Audit record for a confirmed duplicate merge that can be undone."""
    __tablename__ = "duplicate_merge_operations"
    __table_args__ = (
        Index("ix_duplicate_merge_operations_candidate", "duplicate_candidate_id"),
        Index("ix_duplicate_merge_operations_events", "source_event_id", "target_event_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    duplicate_candidate_id: Mapped[str] = mapped_column(String(36), ForeignKey("duplicate_candidates.id"), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(36), ForeignKey("accident_events.id"), nullable=False)
    target_event_id: Mapped[str] = mapped_column(String(36), ForeignKey("accident_events.id"), nullable=False)
    moved_claim_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    moved_document_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    moved_revision_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    moved_conflict_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    moved_issue_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    undone_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    undo_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class EventExternalId(Base):
    """Stable source-specific IDs linked to a canonical event."""
    __tablename__ = "event_external_ids"
    __table_args__ = (
        UniqueConstraint(
            "source_id", "external_id_type", "external_id",
            name="uq_event_external_id_source_type_value",
        ),
        Index("ix_event_external_ids_event", "event_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(36), ForeignKey("accident_events.id"), nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), ForeignKey("sources.id"), nullable=False)
    external_id_type: Mapped[str] = mapped_column(String(50), nullable=False, default="source_record_id")
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DataQualityIssue(Base):
    """Reviewable data-quality warning derived from claims or projections."""
    __tablename__ = "data_quality_issues"
    __table_args__ = (
        Index("ix_data_quality_event_status", "event_id", "status"),
        Index("ix_data_quality_code", "issue_code"),
        UniqueConstraint(
            "event_id", "issue_code", "field_name", "status",
            name="uq_open_data_quality_issue",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(36), ForeignKey("accident_events.id"), nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("sources.id"), nullable=True)
    issue_code: Mapped[str] = mapped_column(String(80), nullable=False)
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="warning")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class ArchiveManifest(Base):
    """Manifest row for a retention/archive export run."""
    __tablename__ = "archive_manifests"
    __table_args__ = (
        Index("ix_archive_manifests_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    archive_type: Mapped[str] = mapped_column(String(40), nullable=False, default="retention")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="created")
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    output_uri: Mapped[str] = mapped_column(Text, nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)


# ── Accident timeline events ───────────────────────────────────────────────────

class TimePrecision(enum.StrEnum):
    EXACT         = "exact"
    APPROXIMATE   = "approximate"
    RELATIVE      = "relative"
    SEQUENCE_ONLY = "sequence_only"
    UNKNOWN       = "unknown"


class AccidentTimelineEvent(Base):
    """
    A structured event in the reconstructed timeline of an accident.

    Design principles:
    - Claims remain the source of truth; timeline events are derived / curated.
    - Supports exact UTC time, local time, relative offsets, and sequence-only ordering.
    - is_disputed signals events where supporting claims contradict each other.
    - confidence_score is a 0–1 float calculated by TimelineReconstructionService.

    Time ordering strategy (applied by TimelineReconstructionService):
      1. event_time_utc   — absolute UTC timestamp (most reliable)
      2. relative_offset_seconds — signed seconds relative to impact (negative = before)
      3. sequence_index   — editorial ordering when no times are available
      4. created_at       — last-resort stable sort key
    """
    __tablename__ = "accident_timeline_events"
    __table_args__ = (
        Index("ix_timeline_event_accident", "accident_event_id"),
        Index("ix_timeline_event_time_utc", "event_time_utc"),
        Index("ix_timeline_event_seq", "accident_event_id", "sequence_index"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    accident_event_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accident_events.id", ondelete="CASCADE"), nullable=False,
        comment="The parent AccidentEvent this timeline belongs to",
    )

    # Event classification
    event_type: Mapped[str] = mapped_column(
        String(60), nullable=False,
        comment=(
            "departure | takeoff | climb_anomaly | weather_deterioration | "
            "crew_communication | atc_communication | system_warning | "
            "mechanical_failure | altitude_deviation | speed_anomaly | "
            "emergency_declaration | loss_of_control | terrain_proximity_warning | "
            "impact | fire | rescue_response | other"
        ),
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(
        String(60),
        comment="pre_accident | in_flight | impact | post_accident | investigation",
    )
    phase_of_flight: Mapped[str | None] = mapped_column(String(60))

    # Temporal placement — at most one time strategy should be populated per event.
    # The API never presents approximate/relative times as exact.
    event_time_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="Absolute UTC timestamp when available",
    )
    event_time_local: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True,
        comment="Local accident-site time (timezone-naive, same convention as AccidentRecord.occurred_at)",
    )
    relative_offset_seconds: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
        comment="Signed seconds relative to impact (negative = before impact)",
    )
    sequence_index: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
        comment="Editorial sequence position when no timestamps are available",
    )
    time_precision: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TimePrecision.UNKNOWN.value,
        comment="exact | approximate | relative | sequence_only | unknown",
    )

    # Quality metadata
    severity: Mapped[str | None] = mapped_column(
        String(20), comment="critical | high | medium | low | informational"
    )
    confidence_score: Mapped[float | None] = mapped_column(
        Numeric(4, 3), comment="0.0–1.0 computed by TimelineReconstructionService"
    )
    is_disputed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    dispute_summary: Mapped[str | None] = mapped_column(
        Text, comment="Human-readable summary of what is disputed"
    )
    source_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False,
        comment="Number of distinct sources supporting this event",
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    accident_event: Mapped[AccidentEvent] = relationship(
        "AccidentEvent", foreign_keys=[accident_event_id]
    )
    claim_links: Mapped[list[TimelineEventClaim]] = relationship(
        back_populates="timeline_event", cascade="all, delete-orphan"
    )


class TimelineEventClaim(Base):
    """
    Join table: links a timeline event to the claims that support it.

    Follows the same pattern as ClaimSourceDocument — a thin many-to-many
    with a link_reason discriminator so the relationship can be annotated.
    """
    __tablename__ = "timeline_event_claims"
    __table_args__ = (
        UniqueConstraint("timeline_event_id", "claim_id", name="uq_timeline_event_claim"),
        Index("ix_timeline_event_claims_event", "timeline_event_id"),
        Index("ix_timeline_event_claims_claim", "claim_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    timeline_event_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accident_timeline_events.id", ondelete="CASCADE"), nullable=False
    )
    claim_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    link_reason: Mapped[str] = mapped_column(
        String(80), nullable=False, default="supporting_claim",
        comment="supporting_claim | primary_claim | disputed_claim",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    timeline_event: Mapped[AccidentTimelineEvent] = relationship(back_populates="claim_links")
    claim: Mapped[Claim] = relationship()


# ── Weather context ────────────────────────────────────────────────────────────

class IcingRisk(enum.StrEnum):
    NONE     = "none"
    POSSIBLE = "possible"
    LIKELY   = "likely"
    SEVERE   = "severe"
    UNKNOWN  = "unknown"


class TurbulenceRisk(enum.StrEnum):
    NONE     = "none"
    POSSIBLE = "possible"
    LIKELY   = "likely"
    SEVERE   = "severe"
    UNKNOWN  = "unknown"


class FlightRules(enum.StrEnum):
    VFR     = "vfr"
    MVFR    = "mvfr"
    IFR     = "ifr"
    LIFR    = "lifr"
    UNKNOWN = "unknown"


class WeatherReportType(enum.StrEnum):
    METAR           = "metar"
    TAF             = "taf"
    PIREP           = "pirep"
    RADAR           = "radar"
    SATELLITE       = "satellite"
    REPORT_SUMMARY  = "report_summary"
    MANUAL          = "manual"


class AccidentWeatherObservation(Base):
    """
    A weather observation associated with an aviation accident.

    Design principles:
    - raw_report_text is always preserved verbatim.
    - parsed_data holds structured fields extracted from the raw report.
    - Canonical numeric fields (temperature_c, wind_speed_kt, etc.) hold
      normalized values so queries and UI don't need to re-parse.
    - confidence_score reflects source reliability, time proximity, and
      station distance — NOT whether weather caused the accident.
    - is_disputed flags conflicting observations from different sources.
    - This model does NOT assert causation; that comes from claims.

    Provenance linkage mirrors ClaimSourceDocument / TimelineEventClaim:
      WeatherObservationClaim (join table) → Claim rows.

    TODO (future):
      - link to raw_snapshots for ingested METAR archives
      - add weather_observation_id FK on AccidentTimelineEvent
        for explicit timeline ↔ weather join
    """
    __tablename__ = "accident_weather_observations"
    __table_args__ = (
        Index("ix_weather_obs_accident", "accident_event_id"),
        Index("ix_weather_obs_time", "observation_time_utc"),
        Index("ix_weather_obs_station", "station_identifier"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    accident_event_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accident_events.id", ondelete="CASCADE"), nullable=False,
        comment="Parent AccidentEvent this observation belongs to",
    )
    source_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("sources.id"), nullable=True,
        comment="Source that supplied this observation (NULL for manual entry)",
    )

    # Station metadata
    station_identifier: Mapped[str | None] = mapped_column(
        String(10), comment="ICAO/IATA station code, e.g. KJFK"
    )
    station_name: Mapped[str | None] = mapped_column(String(200))
    station_latitude: Mapped[float | None] = mapped_column(Numeric(9, 6))
    station_longitude: Mapped[float | None] = mapped_column(Numeric(9, 6))
    distance_to_accident_km: Mapped[float | None] = mapped_column(
        Numeric(8, 3),
        comment="Great-circle distance from station to accident site in km",
    )

    # Temporal context
    observation_time_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    accident_time_delta_minutes: Mapped[float | None] = mapped_column(
        Numeric(8, 2),
        comment=(
            "Signed minutes between observation and accident time. "
            "Negative = observation before accident."
        ),
    )

    # Report metadata
    report_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default=WeatherReportType.METAR.value
    )
    raw_report_text: Mapped[str | None] = mapped_column(
        Text, comment="Verbatim original report text, never mutated after insert"
    )
    parsed_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, comment="Structured parse output; schema varies by report_type"
    )

    # Canonical parsed fields — normalized units
    temperature_c: Mapped[float | None] = mapped_column(Numeric(5, 2))
    dew_point_c: Mapped[float | None] = mapped_column(Numeric(5, 2))
    wind_direction_degrees: Mapped[int | None] = mapped_column(Integer)
    wind_speed_kt: Mapped[float | None] = mapped_column(Numeric(6, 2))
    wind_gust_kt: Mapped[float | None] = mapped_column(Numeric(6, 2))
    visibility_m: Mapped[float | None] = mapped_column(
        Numeric(8, 1), comment="Visibility in metres (statute miles × 1609.34)"
    )
    ceiling_ft: Mapped[int | None] = mapped_column(
        Integer, comment="Lowest broken/overcast layer in feet AGL"
    )
    altimeter_hpa: Mapped[float | None] = mapped_column(
        Numeric(7, 2), comment="Altimeter setting in hPa (inHg × 33.8639)"
    )
    precipitation_type: Mapped[str | None] = mapped_column(
        String(50), comment="rain | snow | freezing_rain | drizzle | hail | none"
    )
    thunderstorm_present: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    icing_risk: Mapped[str | None] = mapped_column(
        String(20), comment="none | possible | likely | severe | unknown"
    )
    turbulence_risk: Mapped[str | None] = mapped_column(
        String(20), comment="none | possible | likely | severe | unknown"
    )
    flight_rules: Mapped[str | None] = mapped_column(
        String(10), comment="vfr | mvfr | ifr | lifr | unknown"
    )

    # Quality
    confidence_score: Mapped[float | None] = mapped_column(
        Numeric(4, 3), comment="0.0–1.0 from WeatherContextService.compute_confidence()"
    )
    is_disputed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    dispute_summary: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    accident_event: Mapped[AccidentEvent] = relationship(
        "AccidentEvent", foreign_keys=[accident_event_id]
    )
    source: Mapped[Source | None] = relationship("Source", foreign_keys=[source_id])
    claim_links: Mapped[list[WeatherObservationClaim]] = relationship(
        back_populates="observation", cascade="all, delete-orphan"
    )


class WeatherObservationClaim(Base):
    """
    Join table: links a weather observation to the Claim rows that support it.

    Follows the same pattern as TimelineEventClaim and ClaimSourceDocument.
    link_reason distinguishes how the claim relates to the observation:
      supporting_claim — claim contains weather data backing this observation
      contributing_factor_claim — claim explicitly says weather contributed to the accident
      disputed_claim — claim contradicts another observation
    """
    __tablename__ = "weather_observation_claims"
    __table_args__ = (
        UniqueConstraint(
            "weather_observation_id", "claim_id",
            name="uq_weather_obs_claim",
        ),
        Index("ix_weather_obs_claims_obs", "weather_observation_id"),
        Index("ix_weather_obs_claims_claim", "claim_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    weather_observation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("accident_weather_observations.id", ondelete="CASCADE"),
        nullable=False,
    )
    claim_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    link_reason: Mapped[str] = mapped_column(
        String(80), nullable=False, default="supporting_claim",
        comment="supporting_claim | contributing_factor_claim | disputed_claim",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    observation: Mapped[AccidentWeatherObservation] = relationship(back_populates="claim_links")
    claim: Mapped[Claim] = relationship()


# ── Mechanical / System Failure Tracking ──────────────────────────────────────

class FailureCategory(enum.StrEnum):
    ENGINE        = "engine"
    FUEL          = "fuel"
    HYDRAULIC     = "hydraulic"
    ELECTRICAL    = "electrical"
    AVIONICS      = "avionics"
    FLIGHT_CONTROLS = "flight_controls"
    LANDING_GEAR  = "landing_gear"
    BRAKES        = "brakes"
    TIRES         = "tires"
    STRUCTURE     = "structure"
    PRESSURIZATION = "pressurization"
    NAVIGATION    = "navigation"
    AUTOPILOT     = "autopilot"
    ROTOR_SYSTEM  = "rotor_system"
    PROPELLER     = "propeller"
    MAINTENANCE   = "maintenance"
    OTHER         = "other"
    UNKNOWN       = "unknown"


class FailureStatus(enum.StrEnum):
    SUSPECTED  = "suspected"
    REPORTED   = "reported"
    CONFIRMED  = "confirmed"
    DISPUTED   = "disputed"
    RULED_OUT  = "ruled_out"
    UNKNOWN    = "unknown"


class FailureSeverity(enum.StrEnum):
    MINOR        = "minor"
    MAJOR        = "major"
    HAZARDOUS    = "hazardous"
    CATASTROPHIC = "catastrophic"
    UNKNOWN      = "unknown"


class FailureMode(enum.StrEnum):
    FRACTURE       = "fracture"
    FATIGUE        = "fatigue"
    OVERHEATING    = "overheating"
    FIRE           = "fire"
    SEIZURE        = "seizure"
    LEAK           = "leak"
    BLOCKAGE       = "blockage"
    CONTAMINATION  = "contamination"
    SOFTWARE_FAULT = "software_fault"
    SENSOR_ERROR   = "sensor_error"
    LOSS_OF_POWER  = "loss_of_power"
    JAMMED_CONTROL = "jammed_control"
    UNKNOWN        = "unknown"


class AccidentSystemFailure(Base):
    """
    A mechanical or system failure record associated with an aviation accident.

    Design principles:
    - Claims are the source of truth; this row is a curated/derived projection.
    - status reflects the current consensus across linked claims; never assert
      causation unless a source explicitly supports it.
    - is_disputed is set when linked claims contradict each other on this failure.
    - Old or contradicted claims are NEVER deleted — see SystemFailureClaim join table.
    - confidence_score is computed by SystemFailureTrackingService, not stored as fact.

    Status lifecycle:
      suspected → reported → confirmed
      suspected/reported → ruled_out     (e.g. final report clears the issue)
      any → disputed                     (when claims conflict)

    Causation note: a system failure may be present and confirmed without being
    the accident cause. is_causal_factor must only be True when a source claim
    explicitly asserts causation.

    TODO (future extension points):
      - Link to FAA Airworthiness Directives via ad_number field
      - Link to EASA Safety Publications
      - Link to manufacturer service bulletins
      - Link to AccidentTimelineEvent for in-flight failure timeline events
      - Import from maintenance logs / engine monitoring data
      - AI-assisted extraction from NTSB narrative text
    """
    __tablename__ = "accident_system_failures"
    __table_args__ = (
        Index("ix_system_failure_accident", "accident_event_id"),
        Index("ix_system_failure_category", "failure_category"),
        Index("ix_system_failure_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    accident_event_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accident_events.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("sources.id"), nullable=True,
        comment="Primary source that first reported this failure (NULL for manual entry)"
    )

    # Classification
    failure_category: Mapped[str] = mapped_column(
        String(30), nullable=False, default=FailureCategory.UNKNOWN.value
    )
    subsystem: Mapped[str | None] = mapped_column(
        String(100), comment="e.g. 'left engine', 'main rotor gearbox', 'nose gear'"
    )

    # Component-level detail (all optional — enter what is known)
    component_name: Mapped[str | None] = mapped_column(String(200))
    manufacturer: Mapped[str | None] = mapped_column(String(200))
    model_number: Mapped[str | None] = mapped_column(String(100))
    part_number: Mapped[str | None] = mapped_column(String(100))
    serial_number: Mapped[str | None] = mapped_column(String(100))

    # Failure characterization
    failure_mode: Mapped[str | None] = mapped_column(
        String(30),
        comment="fracture | fatigue | overheating | fire | seizure | leak | "
                "blockage | contamination | software_fault | sensor_error | "
                "loss_of_power | jammed_control | unknown"
    )

    # Status — never infer certainty beyond what sources support
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=FailureStatus.UNKNOWN.value,
        comment="suspected | reported | confirmed | disputed | ruled_out | unknown"
    )
    severity: Mapped[str | None] = mapped_column(
        String(20), comment="minor | major | hazardous | catastrophic | unknown"
    )

    # Causal flag — only True when a source explicitly asserts causation
    is_causal_factor: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False,
        comment="True ONLY when a source claim explicitly asserts this failure caused the accident"
    )

    # Detection timing flags
    occurred_in_flight: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    detected_before_accident: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    detected_during_flight: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    detected_post_accident: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Maintenance context
    maintenance_related: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    inspection_finding: Mapped[str | None] = mapped_column(
        Text, comment="Summary of inspection or post-accident teardown findings"
    )

    description: Mapped[str | None] = mapped_column(Text)

    # Quality metadata
    confidence_score: Mapped[float | None] = mapped_column(Numeric(4, 3))
    is_disputed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    dispute_summary: Mapped[str | None] = mapped_column(Text)
    source_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    accident_event: Mapped[AccidentEvent] = relationship(
        "AccidentEvent", foreign_keys=[accident_event_id]
    )
    source: Mapped[Source | None] = relationship("Source", foreign_keys=[source_id])
    claim_links: Mapped[list[SystemFailureClaim]] = relationship(
        back_populates="system_failure", cascade="all, delete-orphan"
    )


class SystemFailureClaim(Base):
    """
    Join table: links a system failure record to the Claim rows that support it.

    Follows the same pattern as TimelineEventClaim / WeatherObservationClaim.

    link_reason values:
      supporting_claim       — claim documents this failure
      ruling_out_claim       — claim explicitly rules this failure out
      disputed_claim         — claim contradicts another claim about this failure
      causal_assertion_claim — claim explicitly asserts this failure caused the accident
    """
    __tablename__ = "system_failure_claims"
    __table_args__ = (
        UniqueConstraint("system_failure_id", "claim_id", name="uq_system_failure_claim"),
        Index("ix_system_failure_claims_failure", "system_failure_id"),
        Index("ix_system_failure_claims_claim", "claim_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    system_failure_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accident_system_failures.id", ondelete="CASCADE"), nullable=False
    )
    claim_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    link_reason: Mapped[str] = mapped_column(
        String(80), nullable=False, default="supporting_claim",
        comment="supporting_claim | ruling_out_claim | disputed_claim | causal_assertion_claim"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    system_failure: Mapped[AccidentSystemFailure] = relationship(back_populates="claim_links")
    claim: Mapped[Claim] = relationship()


# ── Advanced Analytics & Pattern Detection ────────────────────────────────────

class SnapshotType(enum.StrEnum):
    ACCIDENT_SUMMARY       = "accident_summary"
    FACTOR_TRENDS          = "factor_trends"
    AIRCRAFT_MODEL_PATTERNS = "aircraft_model_patterns"
    WEATHER_PATTERNS       = "weather_patterns"
    SYSTEM_FAILURE_PATTERNS = "system_failure_patterns"
    TIMELINE_PATTERNS      = "timeline_patterns"
    SIMILAR_ACCIDENTS      = "similar_accidents"
    DATA_QUALITY           = "data_quality"


class PatternTagType(enum.StrEnum):
    WEATHER             = "weather"
    MECHANICAL          = "mechanical"
    HUMAN_FACTOR        = "human_factor"
    PHASE_OF_FLIGHT     = "phase_of_flight"
    TIMELINE_SEQUENCE   = "timeline_sequence"
    DATA_QUALITY        = "data_quality"
    INVESTIGATION_STATUS = "investigation_status"
    CAUSAL_FACTOR       = "causal_factor"
    CONTEXTUAL_FACTOR   = "contextual_factor"


class PatternTagStatus(enum.StrEnum):
    CONFIRMED  = "confirmed"
    SUSPECTED  = "suspected"
    DISPUTED   = "disputed"
    RULED_OUT  = "ruled_out"
    UNKNOWN    = "unknown"


class AnalyticsSnapshot(Base):
    """
    Cached result of an analytics computation.

    Snapshots are write-once after generation; re-running an analytics query
    creates a new row rather than mutating the old one, so the history of
    what the platform believed at a given moment is preserved.

    result is a JSONB blob whose schema varies by snapshot_type — callers
    must inspect snapshot_type before interpreting result.

    TODO: add snapshot expiry / TTL; add snapshot comparison (diff over time).
    """
    __tablename__ = "analytics_snapshots"
    __table_args__ = (
        Index("ix_analytics_snapshot_type", "snapshot_type"),
        Index("ix_analytics_snapshot_generated", "generated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    snapshot_type: Mapped[str] = mapped_column(String(40), nullable=False)
    parameters: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, comment="Filter/grouping parameters used to generate this snapshot"
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, comment="Computed analytics result; schema varies by snapshot_type"
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    generated_by: Mapped[str | None] = mapped_column(String(100))
    data_version: Mapped[str | None] = mapped_column(
        String(50), comment="Optional version tag for result schema changes"
    )
    source_record_count: Mapped[int | None] = mapped_column(Integer)
    low_confidence_count: Mapped[int | None] = mapped_column(Integer)
    disputed_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AccidentPatternTag(Base):
    """
    A deterministic, structured tag attached to an accident.

    Tags are derived from structured data — never inferred from free text.
    Each tag carries a status so "suspected:ifr" is distinct from "confirmed:ifr".
    A confirmed weather:ifr tag means a source confirmed IFR conditions.
    A suspected weather:ifr tag means IFR was suspected but not confirmed.

    Tags are rebuilt deterministically from existing structured fields
    (AccidentRecord, AccidentWeatherObservation, AccidentSystemFailure, etc.)
    by AdvancedAnalyticsService.rebuild_pattern_tags().

    Caution: tags NEVER infer causation. A causal_factor tag must be backed
    by an explicit source claim asserting causation.
    """
    __tablename__ = "accident_pattern_tags"
    __table_args__ = (
        Index("ix_pattern_tag_accident", "accident_event_id"),
        Index("ix_pattern_tag_type", "tag_type"),
        Index("ix_pattern_tag_value", "tag_value"),
        UniqueConstraint(
            "accident_event_id", "tag_type", "tag_value", "status",
            name="uq_pattern_tag",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    accident_event_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accident_events.id", ondelete="CASCADE"), nullable=False
    )
    tag_type: Mapped[str] = mapped_column(
        String(30), nullable=False,
        comment="weather | mechanical | human_factor | phase_of_flight | "
                "timeline_sequence | data_quality | investigation_status | "
                "causal_factor | contextual_factor"
    )
    tag_value: Mapped[str] = mapped_column(
        String(100), nullable=False,
        comment="e.g. 'ifr', 'engine_failure', 'approach', 'low_confidence_time'"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=PatternTagStatus.UNKNOWN.value,
        comment="confirmed | suspected | disputed | ruled_out | unknown"
    )
    confidence_score: Mapped[float | None] = mapped_column(Numeric(4, 3))
    source_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_disputed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    accident_event: Mapped[AccidentEvent] = relationship(
        "AccidentEvent", foreign_keys=[accident_event_id]
    )


class AccidentSimilarityScore(Base):
    """
    Explainable similarity score between two accidents.

    Scoring is deterministic and feature-based (not ML). The similarity_reasons
    JSONB stores factor-level weights so the score can be fully explained.
    shared_factors and differing_factors drive the UI display.

    Scores are directional: (A, B) and (B, A) may differ if one accident has
    richer data than the other. Both rows are typically written.

    Caution: high similarity score does NOT imply shared cause. The display
    must show "similar context" language, not "same cause" language.
    """
    __tablename__ = "accident_similarity_scores"
    __table_args__ = (
        Index("ix_similarity_accident", "accident_event_id"),
        Index("ix_similarity_score", "similarity_score"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    accident_event_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accident_events.id", ondelete="CASCADE"), nullable=False
    )
    similar_accident_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accident_events.id", ondelete="CASCADE"), nullable=False
    )
    similarity_score: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    similarity_reasons: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, comment="Factor-level weight breakdown for the score"
    )
    shared_factors: Mapped[list[str] | None] = mapped_column(
        JSONB, comment="Human-readable list of matching factors"
    )
    differing_factors: Mapped[list[str] | None] = mapped_column(
        JSONB, comment="Human-readable list of notable differences"
    )
    confidence_score: Mapped[float | None] = mapped_column(
        Numeric(4, 3),
        comment="Lower when similarity is based on low-confidence or disputed data"
    )
    low_confidence_warning: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False,
        comment="True when one or more shared factors come from disputed or low-confidence data"
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    accident_event: Mapped[AccidentEvent] = relationship(
        "AccidentEvent", foreign_keys=[accident_event_id]
    )
    similar_accident: Mapped[AccidentEvent] = relationship(
        "AccidentEvent", foreign_keys=[similar_accident_id]
    )


# ── Flight Path Reconstruction ─────────────────────────────────────────────────

class PathPointType(enum.StrEnum):
    DEPARTURE         = "departure"
    ENROUTE           = "enroute"
    RADAR             = "radar"
    ADSB              = "adsb"
    FDR               = "fdr"
    CVR_REFERENCE     = "cvr_reference"
    WITNESS_REPORT    = "witness_report"
    REPORT_ESTIMATE   = "report_estimate"
    LAST_KNOWN_POSITION = "last_known_position"
    FINAL_APPROACH    = "final_approach"
    IMPACT            = "impact"
    WRECKAGE_LOCATION = "wreckage_location"
    SEARCH_AREA       = "search_area"
    INFERRED          = "inferred"
    ESTIMATED         = "estimated"
    PLANNED_ROUTE     = "planned_route"
    UNKNOWN           = "unknown"


class SourceMethod(enum.StrEnum):
    ADSB                = "adsb"
    RADAR               = "radar"
    FDR                 = "fdr"
    CVR                 = "cvr"
    ATC_TRANSCRIPT      = "atc_transcript"
    INVESTIGATION_REPORT = "investigation_report"
    WITNESS             = "witness"
    MANUAL              = "manual"
    INFERRED            = "inferred"
    ESTIMATED           = "estimated"
    UNKNOWN             = "unknown"


class AltitudeReference(enum.StrEnum):
    MSL              = "msl"
    AGL              = "agl"
    FLIGHT_LEVEL     = "flight_level"
    PRESSURE_ALTITUDE = "pressure_altitude"
    RADIO_ALTITUDE   = "radio_altitude"
    UNKNOWN          = "unknown"


class PathSegmentType(enum.StrEnum):
    RECORDED      = "recorded"
    OBSERVED      = "observed"
    INTERPOLATED  = "interpolated"
    INFERRED      = "inferred"
    ESTIMATED     = "estimated"
    PLANNED_ROUTE = "planned_route"
    DISPUTED      = "disputed"
    UNKNOWN       = "unknown"


class AnnotationType(enum.StrEnum):
    GPWS_SINK_RATE       = "gpws_sink_rate"
    GPWS_PULL_UP         = "gpws_pull_up"
    TERRAIN_WARNING      = "terrain_warning"
    STALL_WARNING        = "stall_warning"
    OVERSPEED_WARNING    = "overspeed_warning"
    FLAP_CHANGE          = "flap_change"
    GEAR_CHANGE          = "gear_change"
    AUTOPILOT_DISCONNECT = "autopilot_disconnect"
    EMERGENCY_DECLARATION = "emergency_declaration"
    ATC_COMMUNICATION    = "atc_communication"
    CREW_COMMUNICATION   = "crew_communication"
    LOSS_OF_CONTACT      = "loss_of_contact"
    ALTITUDE_DEVIATION   = "altitude_deviation"
    SPEED_DEVIATION      = "speed_deviation"
    ROUTE_DEVIATION      = "route_deviation"
    RAPID_DESCENT        = "rapid_descent"
    IMPACT               = "impact"
    OTHER                = "other"


# ── Estimated / inferred points are NEVER rendered as recorded fact ────────────
# PathPointType.INFERRED, ESTIMATED, REPORT_ESTIMATE → always shown as dashed /
# low-opacity on the map; their time_precision is never "exact" unless a source
# explicitly confirms otherwise.

class AccidentFlightPathPoint(Base):
    """
    A single point in the reconstructed flight path of an accident.

    Design principles
    -----------------
    - Claims are the source of truth.  This row is a curated/derived record.
    - raw_data preserves original ADS-B/radar/FDR source values verbatim.
      It is NOT returned by the reconstruction payload by default (use /points
      for the raw detail) to keep response sizes manageable.
    - coordinates may be NULL; points without lat/lon are still valid for the
      time/altitude/speed profile — they just won't render on the map.
    - time_precision mirrors the same enum used by AccidentTimelineEvent.
    - is_disputed is set when multiple sources disagree on this position.

    Ordering strategy (mirrors timeline):
      1. recorded_time_utc  — absolute UTC time (most authoritative)
      2. relative_offset_seconds — signed seconds relative to impact
      3. sequence_index     — editorial ordering when no times available
      4. created_at         — last-resort stable tiebreak

    Extension points
    ----------------
    - Add adsb_hex / mode_s_code for ADS-B receiver chain linkage
    - Add fdr_frame_number for FDR data alignment
    - Link to terrain elevation via DEM lookup (radio_altitude vs terrain)
    - Add point_quality enum from ADS-B NACp/NACv fields
    """
    __tablename__ = "accident_flight_path_points"
    __table_args__ = (
        Index("ix_fp_point_accident", "accident_event_id"),
        Index("ix_fp_point_time", "recorded_time_utc"),
        Index("ix_fp_point_type", "point_type"),
        Index("ix_fp_point_seq", "accident_event_id", "sequence_index"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    accident_event_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accident_events.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("sources.id"), nullable=True
    )

    # Temporal placement
    sequence_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recorded_time_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="Absolute UTC time of this position fix, when known"
    )
    relative_offset_seconds: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
        comment="Signed seconds relative to impact (negative = before)"
    )
    time_precision: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TimePrecision.UNKNOWN.value
    )

    # Position — WGS84 decimal degrees; NULL when position is unknown
    latitude: Mapped[float | None] = mapped_column(
        Numeric(10, 7), nullable=True,
        comment="WGS84 latitude; NULL when position is unknown or disputed"
    )
    longitude: Mapped[float | None] = mapped_column(
        Numeric(10, 7), nullable=True
    )

    # Altitude
    altitude_ft: Mapped[float | None] = mapped_column(Numeric(8, 1))
    altitude_reference: Mapped[str | None] = mapped_column(String(20))
    radio_altitude_ft: Mapped[float | None] = mapped_column(
        Numeric(8, 1), nullable=True,
        comment="Height above terrain/ground from radio altimeter (AGL)"
    )

    # Speed / motion
    ground_speed_kt: Mapped[float | None] = mapped_column(Numeric(6, 1))
    indicated_airspeed_kt: Mapped[float | None] = mapped_column(Numeric(6, 1))
    vertical_speed_fpm: Mapped[float | None] = mapped_column(
        Numeric(7, 1),
        comment="Positive = climb, negative = descent; feet per minute"
    )
    heading_degrees: Mapped[float | None] = mapped_column(Numeric(5, 2))
    track_degrees: Mapped[float | None] = mapped_column(
        Numeric(5, 2),
        comment="Ground track (direction of movement); may differ from heading in crosswind"
    )

    # Derived geometry
    distance_to_impact_km: Mapped[float | None] = mapped_column(
        Numeric(8, 3),
        comment="Great-circle distance to accident impact site; calculated by service"
    )
    uncertainty_radius_m: Mapped[float | None] = mapped_column(
        Numeric(9, 1),
        comment="Estimated position uncertainty radius in metres (e.g. from ADS-B NACp)"
    )

    # Classification
    point_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default=PathPointType.UNKNOWN.value
    )
    source_method: Mapped[str | None] = mapped_column(String(30))

    # Quality
    confidence_score: Mapped[float | None] = mapped_column(Numeric(4, 3))
    is_disputed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    dispute_summary: Mapped[str | None] = mapped_column(Text)

    # Raw source data — preserved verbatim; not returned in default payloads
    raw_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, comment="Original source values (ADS-B frame, radar return, etc.)"
    )
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    accident_event: Mapped[AccidentEvent] = relationship("AccidentEvent", foreign_keys=[accident_event_id])
    source: Mapped[Source | None] = relationship("Source", foreign_keys=[source_id])
    claim_links: Mapped[list[FlightPathPointClaim]] = relationship(
        back_populates="point", cascade="all, delete-orphan"
    )


class FlightPathPointClaim(Base):
    """Join table: links a flight path point to supporting Claim rows."""
    __tablename__ = "flight_path_point_claims"
    __table_args__ = (
        UniqueConstraint("flight_path_point_id", "claim_id", name="uq_fp_point_claim"),
        Index("ix_fp_point_claims_point", "flight_path_point_id"),
        Index("ix_fp_point_claims_claim", "claim_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    flight_path_point_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accident_flight_path_points.id", ondelete="CASCADE"), nullable=False
    )
    claim_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    link_reason: Mapped[str] = mapped_column(
        String(80), nullable=False, default="supporting_claim"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    point: Mapped[AccidentFlightPathPoint] = relationship(back_populates="claim_links")
    claim: Mapped[Claim] = relationship()


class AccidentFlightPathSegment(Base):
    """
    A segment connecting two consecutive flight path points.

    Segments are auto-generated by FlightPathReconstructionService.rebuild().
    They encode the rendering style (solid vs dashed) for map display.

    segment_type is derived from the endpoint point_types:
      - Both endpoints are non-estimated → recorded/observed
      - Either endpoint is estimated/inferred/report_estimate → estimated
      - Endpoints disagree (disputed) → disputed
    """
    __tablename__ = "accident_flight_path_segments"
    __table_args__ = (
        Index("ix_fp_segment_accident", "accident_event_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    accident_event_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accident_events.id", ondelete="CASCADE"), nullable=False
    )
    start_point_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("accident_flight_path_points.id", ondelete="SET NULL"), nullable=True
    )
    end_point_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("accident_flight_path_points.id", ondelete="SET NULL"), nullable=True
    )
    segment_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default=PathSegmentType.UNKNOWN.value
    )
    length_km: Mapped[float | None] = mapped_column(Numeric(8, 3))
    bearing_degrees: Mapped[float | None] = mapped_column(Numeric(5, 2))
    confidence_score: Mapped[float | None] = mapped_column(Numeric(4, 3))
    is_disputed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    uncertainty_summary: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    accident_event: Mapped[AccidentEvent] = relationship("AccidentEvent", foreign_keys=[accident_event_id])
    start_point: Mapped[AccidentFlightPathPoint | None] = relationship(
        "AccidentFlightPathPoint", foreign_keys=[start_point_id]
    )
    end_point: Mapped[AccidentFlightPathPoint | None] = relationship(
        "AccidentFlightPathPoint", foreign_keys=[end_point_id]
    )


class AccidentFlightPathAnnotation(Base):
    """
    A flight-phase event annotation attached to the reconstructed path.

    Annotations may be linked to:
    - A specific AccidentFlightPathPoint (position on the path)
    - An AccidentTimelineEvent (from the timeline reconstruction feature)
    - A source Claim (provenance)

    Temporal ordering mirrors AccidentFlightPathPoint:
      1. annotation_time_utc
      2. relative_offset_seconds
      3. created_at fallback

    Caution: annotation presence does NOT assert causation. A GPWS warning
    annotation means the warning occurred — not that it caused the accident.
    """
    __tablename__ = "accident_flight_path_annotations"
    __table_args__ = (
        Index("ix_fp_annotation_accident", "accident_event_id"),
        Index("ix_fp_annotation_type", "annotation_type"),
        Index("ix_fp_annotation_time", "annotation_time_utc"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    accident_event_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accident_events.id", ondelete="CASCADE"), nullable=False
    )
    flight_path_point_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("accident_flight_path_points.id", ondelete="SET NULL"), nullable=True,
        comment="Path point this annotation is co-located with"
    )
    # Timeline event link (Phase 7 integration)
    timeline_event_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("accident_timeline_events.id", ondelete="SET NULL"), nullable=True,
        comment="Linked AccidentTimelineEvent if available"
    )
    source_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("sources.id"), nullable=True
    )

    # Temporal
    annotation_time_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    relative_offset_seconds: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
        comment="Signed seconds relative to impact (negative = before)"
    )
    time_precision: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TimePrecision.UNKNOWN.value
    )

    # Content
    annotation_type: Mapped[str] = mapped_column(String(30), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    # Position snapshot at annotation time (may differ from linked point)
    altitude_ft: Mapped[float | None] = mapped_column(Numeric(8, 1))
    radio_altitude_ft: Mapped[float | None] = mapped_column(Numeric(8, 1))

    # Quality
    confidence_score: Mapped[float | None] = mapped_column(Numeric(4, 3))
    is_disputed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    dispute_summary: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    accident_event: Mapped[AccidentEvent] = relationship("AccidentEvent", foreign_keys=[accident_event_id])
    flight_path_point: Mapped[AccidentFlightPathPoint | None] = relationship(
        "AccidentFlightPathPoint", foreign_keys=[flight_path_point_id]
    )
    timeline_event: Mapped[AccidentTimelineEvent | None] = relationship(
        "AccidentTimelineEvent", foreign_keys=[timeline_event_id]
    )
    source: Mapped[Source | None] = relationship("Source", foreign_keys=[source_id])
    claim_links: Mapped[list[FlightPathAnnotationClaim]] = relationship(
        back_populates="annotation", cascade="all, delete-orphan"
    )


class FlightPathAnnotationClaim(Base):
    """Join table: links a flight path annotation to supporting Claim rows."""
    __tablename__ = "flight_path_annotation_claims"
    __table_args__ = (
        UniqueConstraint("annotation_id", "claim_id", name="uq_fp_annotation_claim"),
        Index("ix_fp_annotation_claims_ann", "annotation_id"),
        Index("ix_fp_annotation_claims_claim", "claim_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    annotation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accident_flight_path_annotations.id", ondelete="CASCADE"), nullable=False
    )
    claim_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    link_reason: Mapped[str] = mapped_column(
        String(80), nullable=False, default="supporting_claim"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    annotation: Mapped[AccidentFlightPathAnnotation] = relationship(back_populates="claim_links")
    claim: Mapped[Claim] = relationship()
