"""
TimelineReconstructionService
==============================
Gathers and orders AccidentTimelineEvent rows for a given accident.

Design decisions
----------------
- Claims remain the source of truth; this service reads, never writes, claim data
  except via explicit build/rebuild calls.
- Time ordering:  event_time_utc → relative_offset_seconds → sequence_index → created_at.
- confidence_score is recomputed on each rebuild; the persisted value is a cache.
- Disputed events are flagged but NOT removed from the timeline.
- Automatic extraction from raw text is explicitly out of scope for v1.
  Extension point: inject event *candidates* (from CVR/FDR parsers or AI) and
  call _persist_events(); the service handles deduplication and ordering.

Confidence scoring factors (each 0-1, averaged):
  - source_count_factor   = min(source_count / 3, 1.0)
  - claim_type_factor     = fraction of supporting claims that are 'confirmed'
  - time_precision_factor = 1.0 exact, 0.75 approximate, 0.5 relative/sequence, 0.0 unknown
  - dispute_penalty       = -0.3 if is_disputed else 0.0
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Sequence

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from atlas.models.orm import (
    AccidentEvent,
    AccidentTimelineEvent,
    Claim,
    ClaimType,
    TimelineEventClaim,
    TimePrecision,
)

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Ordering helpers
# ---------------------------------------------------------------------------

_PRECISION_SCORE: dict[str, float] = {
    TimePrecision.EXACT:         1.00,
    TimePrecision.APPROXIMATE:   0.75,
    TimePrecision.RELATIVE:      0.50,
    TimePrecision.SEQUENCE_ONLY: 0.50,
    TimePrecision.UNKNOWN:       0.00,
}

_LARGE_INT = 10 ** 9  # fallback for sequence_index ordering


def _sort_key(ev: AccidentTimelineEvent) -> tuple:
    """
    Return a 4-tuple used to stably sort timeline events.
    None values are pushed to the end within each tier.
    """
    # Tier 1: UTC time (epoch seconds, None → pushed to end)
    t1 = ev.event_time_utc.timestamp() if ev.event_time_utc else float("inf")
    # Tier 2: relative offset (None → pushed to end)
    t2 = ev.relative_offset_seconds if ev.relative_offset_seconds is not None else _LARGE_INT
    # Tier 3: sequence index (None → pushed to end)
    t3 = ev.sequence_index if ev.sequence_index is not None else _LARGE_INT
    # Tier 4: created_at (always present)
    t4 = ev.created_at.timestamp() if ev.created_at else 0.0
    return (t1, t2, t3, t4)


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------

def _compute_confidence(
    event: AccidentTimelineEvent,
    claim_types: list[str],
) -> float:
    """Return a 0.0–1.0 confidence score for one timeline event."""
    # Factor 1: source count (saturates at 3 sources = 1.0)
    source_factor = min(event.source_count / 3.0, 1.0)

    # Factor 2: fraction of confirmed claims
    if claim_types:
        confirmed = sum(1 for ct in claim_types if ct == ClaimType.CONFIRMED)
        claim_factor = confirmed / len(claim_types)
    else:
        claim_factor = 0.5  # no claims attached yet — neutral

    # Factor 3: time precision quality
    precision_factor = _PRECISION_SCORE.get(event.time_precision, 0.0)

    # Base score
    score = (source_factor + claim_factor + precision_factor) / 3.0

    # Dispute penalty
    if event.is_disputed:
        score = max(0.0, score - 0.30)

    return round(score, 3)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class TimelineReconstructionService:
    """
    Stateless service — all methods accept an AsyncSession and return data
    or write to the DB.  Callers are responsible for committing the session.
    """

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    @staticmethod
    async def get_ordered_timeline(
        db: AsyncSession,
        accident_event_id: str,
    ) -> list[AccidentTimelineEvent]:
        """
        Return all timeline events for an accident, ordered by the 4-tier sort key.
        Eagerly loads claim_links → claim (for provenance responses).
        """
        result = await db.execute(
            select(AccidentTimelineEvent)
            .where(AccidentTimelineEvent.accident_event_id == accident_event_id)
            .options(
                selectinload(AccidentTimelineEvent.claim_links).selectinload(
                    TimelineEventClaim.claim
                )
            )
        )
        events: list[AccidentTimelineEvent] = list(result.scalars().all())
        return sorted(events, key=_sort_key)

    @staticmethod
    async def get_supporting_claims(
        db: AsyncSession,
        accident_event_id: str,
    ) -> list[Claim]:
        """
        Return all claims linked to any timeline event for this accident.
        Used by GET /accidents/{id}/timeline/claims.
        """
        # Subquery: timeline event IDs for this accident
        te_subq = (
            select(AccidentTimelineEvent.id)
            .where(AccidentTimelineEvent.accident_event_id == accident_event_id)
            .scalar_subquery()
        )
        result = await db.execute(
            select(Claim)
            .join(TimelineEventClaim, TimelineEventClaim.claim_id == Claim.id)
            .where(TimelineEventClaim.timeline_event_id.in_(te_subq))
            .distinct()
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_event_by_id(
        db: AsyncSession,
        event_id: str,
    ) -> AccidentTimelineEvent | None:
        result = await db.execute(
            select(AccidentTimelineEvent)
            .where(AccidentTimelineEvent.id == event_id)
            .options(
                selectinload(AccidentTimelineEvent.claim_links).selectinload(
                    TimelineEventClaim.claim
                )
            )
        )
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    @staticmethod
    async def create_event(
        db: AsyncSession,
        *,
        accident_event_id: str,
        event_type: str,
        title: str,
        description: str | None = None,
        category: str | None = None,
        phase_of_flight: str | None = None,
        event_time_utc: datetime | None = None,
        event_time_local: datetime | None = None,
        relative_offset_seconds: int | None = None,
        sequence_index: int | None = None,
        time_precision: str = TimePrecision.UNKNOWN,
        severity: str | None = None,
        is_disputed: bool = False,
        dispute_summary: str | None = None,
        claim_ids: list[str] | None = None,
    ) -> AccidentTimelineEvent:
        """
        Persist a manually curated timeline event and attach claim links.
        Confidence and source_count are computed after claim attachment.
        """
        ev_id = str(uuid.uuid4())
        event = AccidentTimelineEvent(
            id=ev_id,
            accident_event_id=accident_event_id,
            event_type=event_type,
            title=title,
            description=description,
            category=category,
            phase_of_flight=phase_of_flight,
            event_time_utc=event_time_utc,
            event_time_local=event_time_local,
            relative_offset_seconds=relative_offset_seconds,
            sequence_index=sequence_index,
            time_precision=time_precision,
            severity=severity,
            is_disputed=is_disputed,
            dispute_summary=dispute_summary,
            source_count=0,
        )
        db.add(event)
        await db.flush()  # get the row ID before linking claims

        claim_types: list[str] = []
        if claim_ids:
            source_ids: set[str] = set()
            for cid in claim_ids:
                link = TimelineEventClaim(
                    id=str(uuid.uuid4()),
                    timeline_event_id=ev_id,
                    claim_id=cid,
                )
                db.add(link)
                # Fetch claim for metadata
                claim_row = await db.get(Claim, cid)
                if claim_row:
                    claim_types.append(claim_row.claim_type)
                    source_ids.add(claim_row.source_id)
            event.source_count = len(source_ids)

        event.confidence_score = _compute_confidence(event, claim_types)
        log.info(
            "timeline.event.created",
            event_id=ev_id,
            accident_event_id=accident_event_id,
            confidence=event.confidence_score,
        )
        return event

    @staticmethod
    async def update_event(
        db: AsyncSession,
        *,
        event_id: str,
        updates: dict[str, Any],
    ) -> AccidentTimelineEvent | None:
        """
        Apply a partial update to an existing timeline event.
        confidence_score is recomputed from current claim links + new values.
        """
        event = await TimelineReconstructionService.get_event_by_id(db, event_id)
        if event is None:
            return None

        allowed_fields = {
            "event_type", "title", "description", "category", "phase_of_flight",
            "event_time_utc", "event_time_local", "relative_offset_seconds",
            "sequence_index", "time_precision", "severity", "is_disputed",
            "dispute_summary",
        }
        for field, value in updates.items():
            if field in allowed_fields:
                setattr(event, field, value)

        # Recompute confidence from existing claim links
        claim_types = [lnk.claim.claim_type for lnk in event.claim_links if lnk.claim]
        event.confidence_score = _compute_confidence(event, claim_types)
        log.info("timeline.event.updated", event_id=event_id)
        return event

    @staticmethod
    async def delete_event(db: AsyncSession, *, event_id: str) -> bool:
        """
        Hard-delete a timeline event and its claim links (cascade in DB).
        Returns True if the row existed, False if not found.
        """
        event = await db.get(AccidentTimelineEvent, event_id)
        if event is None:
            return False
        await db.delete(event)
        log.info("timeline.event.deleted", event_id=event_id)
        return True

    @staticmethod
    async def rebuild_timeline(
        db: AsyncSession,
        *,
        accident_event_id: str,
        operator_id: str,
    ) -> list[AccidentTimelineEvent]:
        """
        Refresh confidence scores and source_counts for all existing events.

        For v1 this does NOT auto-extract events from claims — that is an
        explicit future extension point.  It recalculates scores so that
        any claim mutations (new claims, supersessions) are reflected.

        Extension hook:
          Override or monkey-patch _extract_candidates(db, accident_event_id)
          to inject auto-extracted event candidates from CVR/FDR or AI parsers.
        """
        events = await TimelineReconstructionService.get_ordered_timeline(
            db, accident_event_id
        )
        for event in events:
            claim_types = [lnk.claim.claim_type for lnk in event.claim_links if lnk.claim]
            source_ids = {lnk.claim.source_id for lnk in event.claim_links if lnk.claim}
            event.source_count = len(source_ids)
            event.confidence_score = _compute_confidence(event, claim_types)

        log.info(
            "timeline.rebuilt",
            accident_event_id=accident_event_id,
            event_count=len(events),
            operator_id=operator_id,
        )
        return sorted(events, key=_sort_key)
