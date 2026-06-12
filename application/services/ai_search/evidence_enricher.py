"""Stage 4 — Evidence enricher.

File location: src/atlas/application/services/ai_search/evidence_enricher.py

For each SearchHit returned from the SQL search, attach the top
supporting claims, HFACS attributions, and active conflicts. This
gives the LLM enough evidence to write a defensible answer without
needing DB access.

Read-only — no mutations. Bounded reads only: top-5 claims per hit,
top-3 attributions per hit. The result set is already bounded by
the search limit (typically 20), so total reads stay small.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from atlas.application.unit_of_work import UnitOfWork
from atlas.domain.search.entities import SearchHit

logger = logging.getLogger(__name__)


# Per-hit caps — keep prompts small to control LLM cost and context drift.
_MAX_CLAIMS_PER_HIT = 5
_MAX_ATTRIBUTIONS_PER_HIT = 3
_MAX_CONFLICTS_PER_HIT = 3


@dataclass(frozen=True)
class EnrichedClaim:
    field_name: str
    field_value: Any
    source_name: str
    source_reliability_tier: int
    claim_type: str
    is_winning: bool


@dataclass(frozen=True)
class EnrichedAttribution:
    code: str
    name: str
    confidence: float
    rationale: str | None


@dataclass(frozen=True)
class EnrichedConflictRef:
    field_name: str
    status: str
    candidate_count: int


@dataclass(frozen=True)
class EnrichedHit:
    """A SearchHit plus the evidence the LLM needs to cite it."""

    slug: str
    title: str
    short_summary: str | None
    operator: str | None
    aircraft_type: str | None
    country: str | None
    event_date: str | None       # ISO 8601
    day_of_week: str | None      # lowercase, e.g. "friday"
    fatalities_total: int | None
    confidence_band: str
    claims: list[EnrichedClaim]
    attributions: list[EnrichedAttribution]
    open_conflicts: list[EnrichedConflictRef]


# ── Public API ────────────────────────────────────────────────────────────────


async def enrich_hits(
    uow: UnitOfWork,
    hits: list[SearchHit],
) -> list[EnrichedHit]:
    """Attach evidence to every hit. Bounded reads only."""
    enriched: list[EnrichedHit] = []
    for hit in hits:
        try:
            enriched.append(await _enrich_one(uow, hit))
        except Exception as exc:
            # If enrichment fails for one hit, log and continue with
            # a thin row rather than failing the whole answer.
            logger.warning(
                "Evidence enrichment failed for slug=%s: %s", hit.slug, exc
            )
            enriched.append(_thin_hit(hit))
    return enriched


# ── Per-hit enrichment ────────────────────────────────────────────────────────


async def _enrich_one(uow: UnitOfWork, hit: SearchHit) -> EnrichedHit:
    page = await uow.public_event_pages.get_by_id(hit.page_id)
    if page is None:
        return _thin_hit(hit)
    event_id = page.event_id

    claims = await _fetch_claims(uow, event_id)
    attributions = await _fetch_attributions(uow, event_id)
    conflicts = await _fetch_conflicts(uow, event_id)

    return EnrichedHit(
        slug=hit.slug,
        title=hit.title,
        short_summary=hit.short_summary,
        operator=hit.operator,
        aircraft_type=hit.aircraft_type,
        country=hit.country,
        event_date=hit.event_date.isoformat() if hit.event_date else None,
        day_of_week=hit.event_date.strftime("%A").lower() if hit.event_date else None,
        fatalities_total=hit.fatalities_total,
        confidence_band=hit.confidence_band,
        claims=claims,
        attributions=attributions,
        open_conflicts=conflicts,
    )


def _thin_hit(hit: SearchHit) -> EnrichedHit:
    """Return a hit with empty evidence sections when enrichment fails."""
    return EnrichedHit(
        slug=hit.slug,
        title=hit.title,
        short_summary=hit.short_summary,
        operator=hit.operator,
        aircraft_type=hit.aircraft_type,
        country=hit.country,
        event_date=hit.event_date.isoformat() if hit.event_date else None,
        day_of_week=hit.event_date.strftime("%A").lower() if hit.event_date else None,
        fatalities_total=hit.fatalities_total,
        confidence_band=hit.confidence_band,
        claims=[],
        attributions=[],
        open_conflicts=[],
    )


# ── Per-section fetchers ──────────────────────────────────────────────────────


async def _fetch_claims(uow: UnitOfWork, event_id: Any) -> list[EnrichedClaim]:
    """Top-5 active winning claims, sorted by source reliability tier."""
    try:
        all_claims = await uow.claims.find_active_by_event(event_id)
    except AttributeError:
        # Repository method varies by implementation — try alternates.
        try:
            all_claims = await uow.claims.find_all_by_event(event_id)
        except AttributeError:
            return []
    except Exception:
        return []

    # Resolve source names — bounded by claim count
    out: list[EnrichedClaim] = []
    seen_fields: set[str] = set()
    for claim in sorted(all_claims, key=lambda c: (not c.is_winning if hasattr(c, "is_winning") else False)):
        if claim.field_name in seen_fields:
            continue
        seen_fields.add(claim.field_name)
        try:
            source = await uow.sources.get(claim.source_id)
            source_name = source.name if source else "Unknown source"
            tier = source.reliability_tier if source else 5
        except Exception:
            source_name = "Unknown source"
            tier = 5
        out.append(
            EnrichedClaim(
                field_name=claim.field_name,
                field_value=claim.field_value,
                source_name=source_name,
                source_reliability_tier=tier,
                claim_type=getattr(claim, "claim_type", "RAW"),
                is_winning=getattr(claim, "is_winning", False),
            )
        )
        if len(out) >= _MAX_CLAIMS_PER_HIT:
            break
    return out


async def _fetch_attributions(uow: UnitOfWork, event_id: Any) -> list[EnrichedAttribution]:
    """Top-3 HFACS causal factor attributions for this event."""
    try:
        attrs = await uow.event_hfacs_attributions.list_for_event(event_id)
    except (AttributeError, Exception):
        return []

    cats = await uow.hfacs_categories.list_all()
    cat_by_id = {c.id: c for c in cats}

    out: list[EnrichedAttribution] = []
    for a in attrs[:_MAX_ATTRIBUTIONS_PER_HIT]:
        cat = cat_by_id.get(a.category_id)
        if cat is None:
            continue
        out.append(
            EnrichedAttribution(
                code=cat.code,
                name=cat.name,
                confidence=getattr(a, "confidence", 1.0),
                rationale=getattr(a, "rationale", None),
            )
        )
    return out


async def _fetch_conflicts(uow: UnitOfWork, event_id: Any) -> list[EnrichedConflictRef]:
    """OPEN conflicts on this event — disputed fields the LLM should flag."""
    try:
        conflicts = await uow.conflicts.find_by_event(event_id)
    except (AttributeError, Exception):
        return []

    out: list[EnrichedConflictRef] = []
    for c in conflicts:
        if getattr(c, "status", None) != "OPEN":
            continue
        out.append(
            EnrichedConflictRef(
                field_name=c.field_name,
                status=c.status,
                candidate_count=len(getattr(c, "claim_ids", [])),
            )
        )
        if len(out) >= _MAX_CONFLICTS_PER_HIT:
            break
    return out


__all__ = [
    "EnrichedAttribution",
    "EnrichedClaim",
    "EnrichedConflictRef",
    "EnrichedHit",
    "enrich_hits",
]
