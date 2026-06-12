"""Report value object — the in-memory representation of a generated report.

This is a pure data structure with no I/O.  The renderer turns it into HTML/PDF.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID


@dataclass
class ReportSourceRef:
    """A source referenced in the report — appears in footnotes."""
    source_id: UUID | None
    source_name: str
    source_kind: str
    reliability_tier: int


@dataclass
class ReportFact:
    """A single projected field value with its source reference."""
    field_name: str
    field_value: object
    claim_type: str
    is_disputed: bool
    is_manually_overridden: bool
    source_ref: ReportSourceRef | None
    footnote_number: int | None = None


@dataclass
class ReportConflictResolution:
    """One resolved conflict in the resolution history appendix."""
    field_name: str
    resolved_at: datetime | None
    resolved_by: str | None  # user ID truncated for display
    rationale: str
    winning_value: object
    superseded_values: list[object] = field(default_factory=list)


@dataclass
class ReportAuditEntry:
    """One audit hash entry in the audit appendix."""
    description: str
    hash_value: str
    timestamp: datetime | None


@dataclass
class ReportModel:
    """Complete in-memory representation of one versioned report.

    All facts carry footnote references.  All curator decisions are recorded.
    The disclaimer is mandatory and must appear on every rendered output.
    """

    # ── Case metadata ──────────────────────────────────────────────────────
    event_id: UUID
    case_title: str
    case_slug: str
    occurrence_date: str | None
    location: str | None
    operator: str | None
    aircraft_type: str | None

    # ── Report metadata ────────────────────────────────────────────────────
    report_version: int
    projection_version: int
    generated_at: datetime
    generated_by: str | None  # atlas_user_id truncated

    # ── Body sections ──────────────────────────────────────────────────────
    facts: list[ReportFact]
    unresolved_conflict_fields: list[str]
    completeness_score: float

    # ── Appendices ─────────────────────────────────────────────────────────
    resolution_history: list[ReportConflictResolution]
    sources: list[ReportSourceRef]
    audit_entries: list[ReportAuditEntry]

    # ── Mandatory disclaimer ───────────────────────────────────────────────
    disclaimer: str = (
        "Atlas is an evidence-organization tool, not a provider of legal advice. "
        "This report organizes and reconciles source evidence. "
        "It does not establish fault or liability, and its contents do not "
        "constitute expert opinion or legal advice. "
        "All projected facts are traceable to their source evidence chains; "
        "conflicts and curator decisions are recorded in the appendices."
    )

    @property
    def has_unresolved_conflicts(self) -> bool:
        return len(self.unresolved_conflict_fields) > 0

    @property
    def completeness_pct(self) -> int:
        return round(self.completeness_score * 100)
