from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from atlas.domain.utils import utc_now


class DomainModel(BaseModel):
    model_config = {"from_attributes": True, "extra": "ignore", "arbitrary_types_allowed": True}


class TenantRole(StrEnum):
    OWNER = "OWNER"
    MEMBER = "MEMBER"
    READ_ONLY = "READ_ONLY"


class TenantStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    ARCHIVED = "ARCHIVED"


class Tenant(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    slug: str
    name: str
    status: TenantStatus = TenantStatus.ACTIVE
    created_at: datetime = Field(default_factory=utc_now)


class TenantMembership(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    atlas_user_id: UUID
    tenant_role: TenantRole = TenantRole.MEMBER
    created_at: datetime = Field(default_factory=utc_now)


class TenantSource(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    name: str
    kind: str = "TENANT_EXTERNAL"
    reliability_tier: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)


class TenantIngestionRunStatus(StrEnum):
    """The three lifecycle states of a tenant ingestion run.

    - ``RUNNING`` — the run is open, claims can be appended.
    - ``SUCCEEDED`` — the operator closed the run with a successful
      finalisation.  Claims in this run are immutable after this
      point.
    - ``FAILED`` — the operator closed the run with a failure marker.
      Claims already submitted in the run remain stored (audit
      trail) but are flagged so the tenant's read paths can show
      "this batch was abandoned partway through".
    """

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class TenantIngestionRun(DomainModel):
    """A tenant-private ingestion run.

    Created when a tenant submits a batch of claims through the
    tenant-side ingestion path (deferred until Phase 6 / 8 wires the
    write paths).  Phase 5 ships the entity so the migration and
    repository surface are coherent.
    """

    id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    tenant_source_id: UUID
    status: str = "running"
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None


class TenantClaimKind(StrEnum):
    """Discriminator for the source-shape of a tenant claim.

    - ``FOQA`` — Flight Operations Quality Assurance.  Machine-
      generated exceedance events from the operator's FDM tooling.
      Typically high-volume, structured (flap setting, sink rate,
      timestamp).

    - ``ASAP`` — Aviation Safety Action Program.  Although ASAP
      narratives proper live in :class:`TenantSafetyReport`, an
      analyst may extract structured claims from a report
      (e.g. "fatigue mentioned") and store them as tenant claims
      with kind=ASAP for cross-reference.

    - ``OTHER`` — generic tenant-private structured claim.  Catch-
      all for tenant-defined claim types that don't fit the FOQA or
      ASAP buckets.

    Forward-compatibility: adding a new kind requires a migration
    to widen the CHECK constraint AND an update to this enum.  The
    schema test
    :func:`tests/domain/test_migration_orm_consistency` keeps these
    in sync at CI time.
    """

    FOQA = "FOQA"
    ASAP = "ASAP"
    OTHER = "OTHER"


class TenantClaim(DomainModel):
    """A tenant-private claim about a public event.

    Anchored to ``event_id`` (the public canonical event identity) so
    the tenant's private view aligns with public ground truth, but
    stored in the parallel ``tenant_claims`` table so it cannot leak
    into the public projection.

    Phase 6 added:

    - ``claim_kind`` — discriminates FOQA / ASAP-derived / OTHER
      structured claims so a single table can carry all three
      without per-kind subtables.
    - ``confidence`` — 0..1 float for the tenant's own confidence in
      the claim.  Deliberately separate from the public projection's
      completeness band so tenant editorial doesn't get pulled into
      public confidence math.
    """

    id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    event_id: UUID
    tenant_source_id: UUID
    tenant_ingestion_run_id: UUID | None = None
    field_name: str
    field_value: Any = None
    claim_kind: TenantClaimKind = TenantClaimKind.OTHER
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    created_at: datetime = Field(default_factory=utc_now)


class TenantEventOverlay(DomainModel):
    """A tenant's free-form overlay on a public event.

    One row per (tenant, event).  Carries notes (Markdown) and a
    free-form JSONB ``overlay_fields`` dict for tenant-private
    structured annotations.

    Phase 5 ships read + idempotent create-or-replace.  A richer
    versioned editorial workflow analogous to the public one
    (Phase 9) is reserved for a later phase if tenants ask for it.
    """

    id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    event_id: UUID
    notes_markdown: str | None = None
    overlay_fields: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class TenantSafetyReportKind(StrEnum):
    """Discriminator for the source of a tenant safety report."""

    FOQA = "FOQA"
    ASAP = "ASAP"
    OTHER = "OTHER"


class TenantSafetyReport(DomainModel):
    """A tenant-private narrative safety report (Phase 6).

    Phase 6's headline data shape: ASAP-style self-reports plus any
    narrative artifact a tenant wants to capture without forcing it
    into the structured-claim shape.

    Hard rule: this entity is **never** exposed on any public
    surface.  Atlas's job is to store it, version it, and make it
    available to authorised tenant readers only.  The router layer
    enforces this by routing every safety-report read through the
    tenant prefix; no public router ever touches this table.

    ``deidentified_attested`` is the operator's signed declaration
    that the narrative has been deidentified before submission.
    Atlas does best-effort PII stripping on top (see
    :mod:`atlas.application.services.deidentification`) but the
    operator is the authoritative deidentifier — this column is the
    operator's record.
    """

    id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    report_kind: TenantSafetyReportKind
    narrative_markdown: str
    deidentified_attested: bool = False
    external_report_ref: str | None = None
    submitter_user_id: UUID
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class TenantEventAssociationKind(StrEnum):
    """Discriminator for tenant-event association types."""

    RELATED = "RELATED"
    OWNED = "OWNED"
    REFERENCED = "REFERENCED"
    CONTRIBUTED_TO = "CONTRIBUTED_TO"


class TenantEventAssociation(DomainModel):
    """Links a tenant to a public accident event, optionally via a claim or
    safety report.

    Exactly one of ``claim_id`` / ``safety_report_id`` must be set.
    """

    id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    event_id: UUID
    claim_id: UUID | None = None
    safety_report_id: UUID | None = None
    association_kind: TenantEventAssociationKind = TenantEventAssociationKind.RELATED
    association_note: str | None = None
    created_by_user_id: UUID | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _exactly_one_target(self):
        claim = self.claim_id
        report = self.safety_report_id
        if not (claim or report):
            raise ValueError("Either claim_id or safety_report_id must be set")
        if claim and report:
            raise ValueError("Only one of claim_id or safety_report_id may be set")
        return self


class CrossrefResultStatus(StrEnum):
    """Lifecycle states for a TenantCrossrefResult."""

    PENDING = "PENDING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


class TenantCrossrefResult(DomainModel):
    """A single cross-reference result surfaced to a tenant.

    Phase 6: the AI cross-reference engine produces structured
    similarity observations between the tenant's private events and
    the public corpus.  Each result holds the match metadata.
    """

    id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    source_event_id: UUID | None = None
    target_public_event_id: UUID | None = None
    target_tenant_event_id: UUID | None = None
    safety_report_id: UUID | None = None
    claim_id: UUID | None = None
    status: CrossrefResultStatus = CrossrefResultStatus.PENDING
    similarity_score: float = Field(ge=0.0, le=1.0, default=0.0)
    matched_fields: list[str] = Field(default_factory=list)
    matches_json: list[dict[str, Any]] = Field(default_factory=list)
    matcher_config_json: dict[str, Any] = Field(default_factory=dict)
    match_count: int = 0
    summary_text: str | None = None
    error_detail: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    reviewed_at: datetime | None = None
    reviewed_by: UUID | None = None