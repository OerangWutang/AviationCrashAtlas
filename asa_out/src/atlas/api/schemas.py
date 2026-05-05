"""
API response and request schemas for the Atlas FastAPI application.

This module exists to keep ``api/app.py`` focused on routing and dependency
wiring rather than schema definition.  No business logic lives here — these
classes describe the shapes that cross the HTTP boundary, nothing more.

Backwards compatibility
-----------------------
``app.py`` re-exports every public name in this module so external code that
does ``from atlas.api.app import ConflictOut`` keeps working.  Add new
schemas here and re-export them from ``app.py`` if route handlers need them.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, computed_field, field_validator, model_validator

# Public claim-type contract. Keep this in lockstep with atlas.models.orm.ClaimType
# and web/types/index.ts::CLAIM_TYPES. FastAPI exposes this Literal as an
# OpenAPI enum, so frontend/SDK consumers see rejected claims as first-class.
ClaimTypeValue = Literal[
    "confirmed",
    "inferred",
    "disputed",
    "rejected",
    "superseded",
    "pending",
]

# ── Common building blocks ────────────────────────────────────────────────────


class ConfidenceOut(BaseModel):
    score: float
    label: str
    css_class: str
    breakdown: dict[str, Any] | None = None


class SourceOut(BaseModel):
    id: str
    short_name: str
    display_name: str
    tier: int
    license_type: str
    base_url: str | None
    description: str | None
    model_config = {"from_attributes": True}


# ── Provenance: claim/conflict/document/projection/revision ──────────────────


class ClaimOut(BaseModel):
    id: str
    field_name: str
    field_value: dict[str, Any]
    display_value: str  # decoded human-readable value
    claim_type: ClaimTypeValue
    confidence: float | None
    source_id: str
    source_short_name: str | None
    snapshot_id: str | None
    effective_at: datetime | None
    is_winning: bool
    notes: str | None


class ConflictOut(BaseModel):
    id: str
    field_name: str
    claim_a_id: str
    claim_b_id: str
    # Lifecycle: "open" | "resolved" | "obsolete"
    status: str
    # Structured resolution — allows consumers to distinguish accepted/rejected claims.
    resolution: str | None
    resolved_at: datetime | None
    resolution_type: str | None  # claim_accepted | claim_rejected | ... (see ORM)
    accepted_claim_id: str | None  # which claim was accepted as authoritative
    rejected_claim_ids: list[str] | None  # which claims were explicitly rejected
    obsolete_reason: str | None
    resolved_by: str | None = None  # operator who resolved (None for open/obsolete)


class SourceDocumentOut(BaseModel):
    id: str
    event_id: str
    source_id: str
    document_type: str
    url: str
    url_verified: bool
    title: str | None
    published_at: date | None
    is_available: bool | None
    last_checked_at: datetime | None
    # Verification metadata — populated by check-links command.
    last_http_status: int | None
    last_check_error: str | None
    last_check_method: str | None


class ProjectionExplanationOut(BaseModel):
    """Per-field projection rationale.

    See ``atlas.claims.projection.SelectionReason`` for the documented machine
    codes; the frontend maps these to short human strings.

    ``displayed_value`` is the decoded value the projection would render —
    ``None`` if the projection is withholding the field (e.g. open dispute).
    """

    field_name: str
    displayed_value: Any | None = None
    selected_claim_id: str | None = None
    selected_source_id: str | None = None
    source_rank: int | None = None
    selection_reason: str | None = None
    has_open_conflict: bool = False
    supporting_claim_count: int = 0
    disputed_claim_count: int = 0


class EventRevisionOut(BaseModel):
    """Single timeline entry from the ``event_revisions`` table.

    See migration 0008 for the documented ``revision_type`` values.
    """

    id: str
    event_id: str
    revision_type: str
    occurred_at: datetime
    source_id: str | None = None
    source_short_name: str | None = None
    field_names: list[str] | None = None
    description: str | None = None


# ── Accident summary and detail ──────────────────────────────────────────────


class AccidentSummary(BaseModel):
    id: str
    canonical_id: str
    occurred_at: datetime | None
    occurred_date: date | None
    occurred_year: int | None
    occurred_at_precision: str | None  # "exact" | "day" | "year"
    location_text: str | None
    country_code: str | None
    location_lat: float | None
    location_lon: float | None  # for map
    aircraft_make: str | None
    aircraft_model: str | None
    operator_name: str | None
    phase_of_flight: str | None
    injury_severity: str | None
    fatalities_total: int | None
    fatalities_crew: int | None = None
    fatalities_passengers: int | None = None
    serious_injuries_crew: int | None = None
    serious_injuries_passengers: int | None = None
    minor_injuries_crew: int | None = None
    minor_injuries_passengers: int | None = None
    uninjured_crew: int | None = None
    uninjured_passengers: int | None = None
    aboard_total: int | None
    aircraft_damage: str | None
    investigation_status: str | None
    confidence: ConfidenceOut
    has_conflicts: bool
    # winning_source_count: sources behind projected (winning) field values only.
    # claim_source_count: all sources that contributed any non-superseded claim.
    # These diverge when a source contributes only losing or disputed claims.
    winning_source_count: int
    claim_source_count: int
    primary_source_id: str | None


class AccidentDetail(AccidentSummary):
    probable_cause: str | None
    contributing_factors: list[str] | None
    ntsb_report_number: str | None
    weather_condition: str | None
    purpose_of_flight: str | None
    aircraft_registration: str | None
    aircraft_amateur_built: bool | None
    serious_injuries: int | None
    minor_injuries: int | None
    state_code: str | None
    last_projected_at: datetime
    # Aggregate document state computed by ProjectionService.
    # One of: none_linked | linked_unverified | verified | unavailable | mixed.
    # Nullable for older records where the projection hasn't been rebuilt under v20.
    # The frontend treats null as none_linked.
    document_status: str | None = None


class ProvenanceTruncationOut(BaseModel):
    """
    Signals which provenance sub-sections were capped before being returned.
    Present on every provenance response; all boolean fields False when nothing
    was cut.  Callers should surface a warning when any boolean field is True.
    """
    claims: bool = False
    conflicts: bool = False
    source_documents: bool = False
    claims_limit: int
    conflicts_limit: int
    source_documents_limit: int


# ── Operational workflows: duplicates, data quality, archive ────────────────

class DuplicateCandidateOut(BaseModel):
    id: str
    source_event_id: str | None
    candidate_event_id: str
    source_id: str | None
    source_record_id: str | None
    match_type: str
    match_score: float
    match_reasons: list[str] | None = None
    status: str
    decision_note: str | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    created_at: datetime


class DuplicateDecisionIn(BaseModel):
    note: str | None = None


class DataQualityIssueOut(BaseModel):
    id: str
    event_id: str
    source_id: str | None = None
    issue_code: str
    field_name: str
    severity: str
    status: str
    details: dict[str, Any] | None = None
    created_at: datetime
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    resolution_note: str | None = None


class DataQualityResolveIn(BaseModel):
    note: str | None = None


class ArchiveManifestOut(BaseModel):
    id: str
    archive_type: str
    status: str
    cutoff_at: datetime
    output_uri: str
    manifest: dict[str, Any]
    created_at: datetime
    completed_at: datetime | None = None
    created_by: str | None = None




class IngestionRunOut(BaseModel):
    id: str
    source_id: str | None = None
    source_name: str
    status: str
    started_at: datetime
    completed_at: datetime | None = None
    records_fetched: int = 0
    snapshots_new: int = 0
    snapshots_skipped: int = 0
    events_created: int = 0
    events_updated: int = 0
    claims_written: int = 0
    projection_errors: int = 0
    ingestion_errors: int = 0
    errors: list[str] | None = None


class SourceStatusOut(BaseModel):
    id: str
    short_name: str
    display_name: str
    tier: int
    license_type: str
    ingestion_enabled: bool
    last_ingested_at: datetime | None = None
    latest_run_status: str | None = None
    latest_run_completed_at: datetime | None = None
    latest_run_errors: int | None = None
    freshness_age_seconds: float | None = None


class AuditLogItemOut(BaseModel):
    kind: str
    id: str
    occurred_at: datetime | None = None
    actor: str | None = None
    event_id: str | None = None
    claim_id: str | None = None
    candidate_event_id: str | None = None
    action: str | None = None
    description: str | None = None


class ApiKeyOut(BaseModel):
    id: str
    operator_id: str
    role: str
    is_active: bool
    created_at: datetime
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    description: str | None = None


class ApiKeyCreateIn(BaseModel):
    operator_id: str
    role: Literal["reviewer", "admin"] = "reviewer"
    description: str | None = None
    expires_at: datetime | None = None


class ApiKeyCreateOut(ApiKeyOut):
    raw_key: str


class SourceDocumentReviewIn(BaseModel):
    document_type: str | None = None
    url_verified: bool | None = None
    is_available: bool | None = None
    note: str | None = None

class AccidentProvenance(BaseModel):
    event_id: str
    claims: list[ClaimOut]
    conflicts: list[ConflictOut]
    source_documents: list[SourceDocumentOut]
    sources: list[SourceOut]
    # v20 additions — both default to empty lists for forward-compat
    # with frontends that still use the older response shape.
    projections: list[ProjectionExplanationOut] = []
    revisions: list[EventRevisionOut] = []
    data_quality_issues: list[DataQualityIssueOut] = []
    # v28.3: structured truncation metadata — always present.
    # When any boolean sub-field is True the user is seeing a capped subset.
    # Optional so frontends that predate v28.3 still deserialise cleanly.
    truncation: ProvenanceTruncationOut | None = None


# ── Map and analytics ─────────────────────────────────────────────────────────


class MapAccident(BaseModel):
    id: str
    canonical_id: str
    location_lat: float
    location_lon: float
    location_text: str | None
    injury_severity: str | None
    fatalities_total: int | None
    aircraft_make: str | None
    aircraft_model: str | None
    occurred_date: date | None
    occurred_year: int | None
    phase_of_flight: str | None
    source_completeness_score: float | None  # was confidence_score (legacy name)


class MapCluster(BaseModel):
    cluster_id: str
    location_lat: float
    location_lon: float
    count: int
    fatalities_total: int
    latest_occurred_year: int | None = None
    cell_degrees: float


class AnalyticsSummary(BaseModel):
    total_accidents: int
    total_fatalities: int
    fatal_count: int
    # Legacy names kept for backwards compatibility.
    # avg_confidence / confidence_bins mean source completeness, not factual certainty.
    avg_confidence: float
    by_severity: dict[str, int]
    by_phase: dict[str, int]
    by_year: dict[int, int]
    # Keys: weakly_sourced | partially_sourced | mostly_sourced | well_sourced
    confidence_bins: dict[str, int]

    # Preferred aliases — included in serialized output via @computed_field.
    # A plain @property is NOT serialized by Pydantic v2; @computed_field is.
    @computed_field
    @property
    def avg_source_completeness(self) -> float:
        """Preferred name for avg_confidence. avg_confidence is a legacy alias."""
        return self.avg_confidence

    @computed_field
    @property
    def source_completeness_bins(self) -> dict[str, int]:
        """Preferred name for confidence_bins. confidence_bins is a legacy alias."""
        return self.confidence_bins


# ── Pagination ────────────────────────────────────────────────────────────────


class PaginatedAccidents(BaseModel):
    items: list[AccidentSummary]
    total: int
    page: int
    page_size: int
    has_next: bool
    next_cursor: str | None = None  # keyset cursor for stable pagination


class CursorPaginatedAccidents(BaseModel):
    """Keyset-paginated result — stable under concurrent inserts."""

    items: list[AccidentSummary]
    next_cursor: str | None  # opaque base64 token; None when no more pages
    page_size: int


# ── Conflict review queue ────────────────────────────────────────────────────


class ConflictQueueItem(BaseModel):
    """One open conflict row for the global review queue."""

    conflict_id: str
    event_id: str
    canonical_id: str | None
    field_name: str
    claim_a_id: str
    claim_b_id: str
    claim_a_value: str | None  # display string
    claim_b_value: str | None
    claim_a_source: str | None  # short_name
    claim_b_source: str | None
    created_at: datetime
    # Event context for triage prioritisation.
    occurred_date: date | None
    location_text: str | None
    injury_severity: str | None


# ── Conflict resolution request body ─────────────────────────────────────────

# Resolution types recognised by the data model (see ORM comment + migration 0006).
ResolutionType = Literal[
    "claim_accepted",
    "claim_rejected",
    "claims_merged",
    "source_corrected",
    "not_applicable",
    "manual_override",
]


class ConflictResolveIn(BaseModel):
    """Request body for ``POST /api/v1/conflicts/{conflict_id}/resolve``.

    Strict per-resolution_type rules
    --------------------------------
    claim_accepted
        ``accepted_claim_id`` REQUIRED, must belong to the conflict.
        ``accepted_claim_id`` must NOT appear in ``rejected_claim_ids``.

    claim_rejected
        ``rejected_claim_ids`` REQUIRED, must belong to the conflict.
        ``accepted_claim_id`` must be ``None`` (the survivor is derived
        automatically by the endpoint if exactly one non-rejected claim remains).

    claims_merged
        ``accepted_claim_id`` and ``rejected_claim_ids`` are both optional
        (use them to record which claims were folded into the merge, but neither
        is required).

    source_corrected / not_applicable / manual_override
        Neither ``accepted_claim_id`` nor ``rejected_claim_ids`` is needed; the
        conflict is closed without designating a winner.  The field remains
        withheld until a follow-up claim or ingestion run produces a non-disputed
        value.

    ``resolved_by`` identifies the operator (username, email, …) and is required
    when auth is disabled — the audit trail is useless without an actor identity.
    When ``API_AUTH_ENABLED=true`` the route overrides this with the authenticated
    operator and ignores the body value.
    """

    resolution_type: ResolutionType
    accepted_claim_id: str | None = None
    rejected_claim_ids: list[str] | None = None
    resolution: str | None = None  # optional free-text rationale
    resolved_by: str | None = None

    @field_validator("accepted_claim_id")
    @classmethod
    def accepted_only_for_claim_accepted(
        cls, v: str | None, info: Any
    ) -> str | None:
        rt = (info.data or {}).get("resolution_type")
        if v is not None and rt not in ("claim_accepted", "claims_merged"):
            raise ValueError(
                f"accepted_claim_id must be None when resolution_type is {rt!r}. "
                "Use 'claim_accepted' to designate a winner."
            )
        return v

    @field_validator("rejected_claim_ids")
    @classmethod
    def rejected_required_for_claim_rejected(
        cls, v: list[str] | None, info: Any
    ) -> list[str] | None:
        rt = (info.data or {}).get("resolution_type")
        if rt == "claim_rejected" and not v:
            raise ValueError(
                "rejected_claim_ids is required when resolution_type is "
                "'claim_rejected'. Specify which claim(s) are rejected."
            )
        return v

    @model_validator(mode="after")
    def no_overlap_between_accepted_and_rejected(self) -> ConflictResolveIn:
        if (
            self.accepted_claim_id
            and self.rejected_claim_ids
            and self.accepted_claim_id in self.rejected_claim_ids
        ):
            raise ValueError(
                f"accepted_claim_id {self.accepted_claim_id!r} also appears in "
                "rejected_claim_ids. A claim cannot be both accepted and rejected."
            )
        return self


__all__ = [
    "AccidentDetail",
    "AccidentProvenance",
    "AccidentSummary",
    "AnalyticsSummary",
    "ClaimOut",
    "ClaimTypeValue",
    "ConfidenceOut",
    "ConflictOut",
    "ConflictQueueItem",
    "ConflictResolveIn",
    "CursorPaginatedAccidents",
    "EventRevisionOut",
    "MapAccident",
    "PaginatedAccidents",
    "ProjectionExplanationOut",
    "ResolutionType",
    "SourceDocumentOut",
    "SourceOut",

    "IngestionRunOut",
    "SourceStatusOut",
    "AuditLogItemOut",
    "ApiKeyOut",
    "ApiKeyCreateIn",
    "ApiKeyCreateOut",
    "ArchiveManifestOut",
    "DataQualityIssueOut",
    "DataQualityResolveIn",
    "DuplicateCandidateOut",
    "DuplicateDecisionIn",
    "SourceDocumentReviewIn",
    "ProvenanceTruncationOut",
]
