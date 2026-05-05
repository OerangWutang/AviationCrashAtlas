"""
Source completeness scoring engine.

Fixes from review:
- confidence_breakdown is now stored in accident_records so the API can
  expose it (fixes "confidence theater" — score was unexplained in API)
- Label thresholds: 0.90+ = Well sourced, 0.70+ = Mostly sourced,
  0.50+ = Partially sourced, <0.50 = Weakly sourced.
  Mock data had 0.88 labeled "High" (old vocabulary) — that was wrong.
  The score measures source completeness, not factual certainty.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from atlas.config import get_settings
from atlas.models.orm import Claim, ClaimConflict, ClaimType, Source, SourceDocument

log = structlog.get_logger(__name__)
settings = get_settings()

_TIER_WEIGHT = {
    1: settings.conf_weight_tier1,
    2: settings.conf_weight_tier2,
    3: settings.conf_weight_tier3,
    4: settings.conf_weight_tier4,
}

_CRITICAL_FIELDS = {
    "occurred_at", "location_coordinates", "injury_severity",
    "fatalities_total", "aircraft_make", "investigation_status",
}

# Claim types that are eligible to contribute to scoring.  PENDING/DISPUTED/
# REJECTED/SUPERSEDED claims must not increase confidence — projection won't
# show them, so confidence must not claim they make the record more complete.
_ELIGIBLE_CLAIM_TYPES = frozenset({
    ClaimType.CONFIRMED.value,
    ClaimType.INFERRED.value,
})

# Canonical source-completeness label thresholds — used here AND in frontend utils.ts
# so both always agree:
#   ≥0.90 → Well sourced | ≥0.70 → Mostly sourced | ≥0.50 → Partially sourced | <0.50 → Weakly sourced
THRESHOLD_HIGH    = 0.90
THRESHOLD_GOOD    = 0.70
THRESHOLD_PARTIAL = 0.50


@dataclass
class ConfidenceFactor:
    name: str
    delta: float
    reason: str


@dataclass
class ConfidenceBreakdown:
    event_id: str
    base_score: float
    final_score: float
    factors: list[ConfidenceFactor] = field(default_factory=list)
    source_tiers: list[int] = field(default_factory=list)
    claim_count: int = 0
    # Conflict counts split by lifecycle status so consumers can distinguish
    # actionable disputes from settled or obsolete ones.
    open_conflict_count: int = 0
    resolved_conflict_count: int = 0
    obsolete_conflict_count: int = 0

    @property
    def conflict_count(self) -> int:
        """Total conflicts (all statuses). Use open_conflict_count for actionable disputes."""
        return self.open_conflict_count + self.resolved_conflict_count + self.obsolete_conflict_count

    def label(self) -> str:
        # Labels convey source completeness, not statistical truth-confidence.
        # "Well sourced" is more honest than "High confidence" for this model.
        s = self.final_score
        if s >= THRESHOLD_HIGH:    return "Well sourced"
        if s >= THRESHOLD_GOOD:    return "Mostly sourced"
        if s >= THRESHOLD_PARTIAL: return "Partially sourced"
        return "Weakly sourced"

    def to_dict(self) -> dict[str, Any]:
        """Stored in accident_records.confidence_breakdown for API exposure."""
        return {
            "event_id": self.event_id,
            "base_score": round(self.base_score, 3),
            "final_score": round(self.final_score, 3),
            "label": self.label(),
            "claim_count": self.claim_count,
            # Split counts so UI can show "2 open, 1 resolved, 3 obsolete"
            "open_conflict_count": self.open_conflict_count,
            "resolved_conflict_count": self.resolved_conflict_count,
            "obsolete_conflict_count": self.obsolete_conflict_count,
            "conflict_count": self.conflict_count,
            "source_tiers": self.source_tiers,
            "factors": [
                {"name": f.name, "delta": round(f.delta, 3), "reason": f.reason}
                for f in self.factors
            ],
        }


class ConfidenceEngine:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def score_event(self, event_id: str) -> tuple[float, ConfidenceBreakdown]:
        claims = await self._load_claims(event_id)
        conflicts = await self._load_conflicts(event_id)
        sources = await self._load_sources(claims)
        documents = await self._load_documents(event_id)

        bd = ConfidenceBreakdown(
            event_id=event_id,
            base_score=0.0,
            final_score=0.0,
            claim_count=len(claims),
            open_conflict_count=sum(1 for c in conflicts if getattr(c, "status", None) == "open" or (getattr(c, "status", None) is None and c.resolution is None)),
            resolved_conflict_count=sum(1 for c in conflicts if getattr(c, "status", None) == "resolved"),
            obsolete_conflict_count=sum(1 for c in conflicts if getattr(c, "status", None) == "obsolete"),
            source_tiers=sorted({sources[c.source_id].tier for c in claims if c.source_id in sources and c.claim_type in _ELIGIBLE_CLAIM_TYPES}),
        )
        score = self._compute(claims, conflicts, sources, documents, bd)
        bd.base_score = score
        bd.final_score = max(0.0, min(1.0, score))
        return bd.final_score, bd

    def _compute(self, claims, conflicts, sources, documents, bd) -> float:
        score = 0.0

        # Only eligible claims (confirmed/inferred) count for tier scoring.
        # A tier-1 source whose every claim is pending or disputed must not
        # give the event a tier-1 boost — projection won't show that data.
        tiers = {
            sources[c.source_id].tier
            for c in claims
            if c.source_id in sources and c.claim_type in _ELIGIBLE_CLAIM_TYPES
        }
        if tiers:
            best_tier = min(tiers)
            w = _TIER_WEIGHT.get(best_tier, 0.5)
            delta = w * 0.40
            score += delta
            bd.factors.append(ConfidenceFactor(
                "source_tier", delta,
                f"tier {best_tier} source (weight {w:.2f})"
            ))

        inv_claim = _get_winning_claim(claims, "investigation_status", sources)
        if inv_claim:
            inv_val = inv_claim.field_value.get("v", "")
            if inv_val in ("final", "closed"):
                delta = 0.25
            elif inv_val in ("probable_cause", "factual"):
                delta = 0.15
            else:
                delta = -settings.conf_penalty_preliminary
            score += delta
            bd.factors.append(ConfidenceFactor(
                "investigation_status", delta, f"investigation at '{inv_val}' stage"
            ))
        else:
            score -= settings.conf_penalty_preliminary
            bd.factors.append(ConfidenceFactor(
                "investigation_unknown", -settings.conf_penalty_preliminary,
                "investigation status unknown"
            ))

        # Multi-source coverage bonus: award when multiple sources contributed
        # eligible (confirmed/inferred) claims.  Pending and disputed claims
        # must not count — using them here would let confidence score a record
        # as "well covered" using data the projection refuses to show.
        non_disputed_source_ids = {
            c.source_id for c in claims
            if c.claim_type in _ELIGIBLE_CLAIM_TYPES
        }
        if len(non_disputed_source_ids) > 1:
            score += settings.conf_bonus_multi_source
            bd.factors.append(ConfidenceFactor(
                "multi_source_coverage", settings.conf_bonus_multi_source,
                f"{len(non_disputed_source_ids)} sources with confirmed/inferred claims"
            ))

        # Only eligible claims count as active fields.  Pending claims are
        # unreviewed; disputed claims are contested.  Neither should contribute
        # to completeness scoring — projection won't show them, so confidence
        # must not claim they make the record more complete.
        active_fields = {
            c.field_name for c in claims
            if c.claim_type in _ELIGIBLE_CLAIM_TYPES
        }
        missing = _CRITICAL_FIELDS - active_fields
        if not missing:
            score += 0.10
            bd.factors.append(ConfidenceFactor("critical_fields_complete", 0.10, "all critical fields present"))
        else:
            penalty = min(len(missing) * 0.04, 0.20)
            score -= penalty
            bd.factors.append(ConfidenceFactor(
                "missing_critical_fields", -penalty,
                f"missing: {', '.join(sorted(missing))}"
            ))

        if "location_coordinates" not in active_fields:
            score -= settings.conf_penalty_missing_location
            bd.factors.append(ConfidenceFactor(
                "missing_location", -settings.conf_penalty_missing_location,
                "no geographic coordinates"
            ))

        if "occurred_at" not in active_fields:
            score -= settings.conf_penalty_missing_date
            bd.factors.append(ConfidenceFactor(
                "missing_date", -settings.conf_penalty_missing_date, "event date unknown"
            ))

        # Only open conflicts penalize score. Resolved conflicts were explicitly
        # settled; obsolete conflicts involve superseded claims and are no longer
        # actionable.  Counting them would double-penalize events that have
        # been actively cleaned up.
        unresolved = [
            c for c in conflicts
            if getattr(c, "status", None) == "open"
            or (getattr(c, "status", None) is None and c.resolution is None)
        ]
        if unresolved:
            penalty = min(len(unresolved) * settings.conf_penalty_unresolved_conflict, 0.30)
            score -= penalty
            bd.factors.append(ConfidenceFactor(
                "unresolved_conflicts", -penalty,
                f"{len(unresolved)} unresolved source conflicts"
            ))

        final_docs = [
            d for d in documents
            if d.document_type in ("final", "final_report", "probable_cause")
            and d.url_verified is True
            and d.is_available is True
        ]
        if final_docs:
            score += 0.05
            bd.factors.append(ConfidenceFactor("final_report_linked", 0.05, "final report linked and url-verified"))

        return score

    async def _load_claims(self, event_id: str) -> list[Claim]:
        r = await self._session.execute(select(Claim).where(Claim.event_id == event_id))
        return list(r.scalars().all())

    async def _load_conflicts(self, event_id: str) -> list[ClaimConflict]:
        r = await self._session.execute(select(ClaimConflict).where(ClaimConflict.event_id == event_id))
        return list(r.scalars().all())

    async def _load_sources(self, claims: list[Claim]) -> dict[str, Source]:
        ids = list({c.source_id for c in claims})
        if not ids:
            return {}
        r = await self._session.execute(select(Source).where(Source.id.in_(ids)))
        return {s.id: s for s in r.scalars().all()}

    async def _load_documents(self, event_id: str) -> list[SourceDocument]:
        r = await self._session.execute(select(SourceDocument).where(SourceDocument.event_id == event_id))
        return list(r.scalars().all())


def _get_winning_claim(
    claims: list[Claim],
    field_name: str,
    sources: dict[str, Source] | None = None,
) -> Claim | None:
    """
    Pick the highest-priority claim for field_name using the same sort key
    as ProjectionService._select_winners so confidence scoring reasons about
    the same winning claim that will actually be projected.

    Priority: claim_type → source_tier → recency (newest first).
    """
    candidates = [
        c for c in claims
        if c.field_name == field_name
        and c.claim_type in _ELIGIBLE_CLAIM_TYPES
    ]
    if not candidates:
        return None
    sources = sources or {}
    return sorted(
        candidates,
        key=lambda c: (
            0 if c.claim_type == ClaimType.CONFIRMED.value else 1,
            sources[c.source_id].tier if c.source_id in sources else 99,
            -(c.created_at.timestamp() if c.created_at else 0),
        ),
    )[0]


def confidence_label(score: float) -> tuple[str, str]:
    """Returns (label, css_class). Labels describe source completeness, not truth-confidence."""
    if score >= THRESHOLD_HIGH:    return "Well sourced",       "conf-high"
    if score >= THRESHOLD_GOOD:    return "Mostly sourced",     "conf-good"
    if score >= THRESHOLD_PARTIAL: return "Partially sourced",  "conf-partial"
    return "Weakly sourced", "conf-low"
