"""
Timeline API endpoints — mounted on the main api_router in atlas.api.app.

Routes
------
GET  /api/v1/accidents/{event_id}/timeline
     Return ordered timeline events with provenance summaries.

GET  /api/v1/accidents/{event_id}/timeline/claims
     Return raw claims linked to any timeline event for this accident.

POST /api/v1/accidents/{event_id}/timeline/rebuild
     Reviewer/admin — refresh confidence scores from current claims.

POST /api/v1/accidents/{event_id}/timeline/events
     Reviewer/admin — create a manually curated timeline event.

PATCH /api/v1/timeline/events/{timeline_event_id}
     Reviewer/admin — partial update a timeline event.

DELETE /api/v1/timeline/events/{timeline_event_id}
     Reviewer/admin — hard-delete a timeline event.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from atlas.api.auth import OperatorContext, require_reviewer
from atlas.db.engine import get_db, get_read_db
from atlas.models.orm import AccidentEvent, AccidentTimelineEvent, TimePrecision
from atlas.timeline.service import TimelineReconstructionService

log = structlog.get_logger(__name__)

router = APIRouter(tags=["timeline"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class TimelineClaimSummary(BaseModel):
    claim_id: str
    field_name: str
    claim_type: str
    source_id: str
    link_reason: str


class TimelineEventOut(BaseModel):
    id: str
    accident_event_id: str
    event_type: str
    title: str
    description: str | None
    category: str | None
    phase_of_flight: str | None

    # Temporal — never presented as exact when time_precision is not "exact"
    event_time_utc: datetime | None
    event_time_local: datetime | None
    relative_offset_seconds: int | None
    sequence_index: int | None
    time_precision: str

    severity: str | None
    confidence_score: float | None
    is_disputed: bool
    dispute_summary: str | None
    source_count: int
    created_at: datetime
    updated_at: datetime

    # Provenance summary (inline — avoids a second round trip)
    supporting_claims: list[TimelineClaimSummary]

    model_config = {"from_attributes": True}


class TimelineOut(BaseModel):
    accident_event_id: str
    event_count: int
    events: list[TimelineEventOut]


class CreateTimelineEventRequest(BaseModel):
    event_type: str = Field(..., description=(
        "departure | takeoff | climb_anomaly | weather_deterioration | "
        "crew_communication | atc_communication | system_warning | "
        "mechanical_failure | altitude_deviation | speed_anomaly | "
        "emergency_declaration | loss_of_control | terrain_proximity_warning | "
        "impact | fire | rescue_response | other"
    ))
    title: str = Field(..., max_length=300)
    description: str | None = None
    category: str | None = None
    phase_of_flight: str | None = None
    event_time_utc: datetime | None = None
    event_time_local: datetime | None = None
    relative_offset_seconds: int | None = None
    sequence_index: int | None = None
    time_precision: str = TimePrecision.UNKNOWN
    severity: str | None = None
    is_disputed: bool = False
    dispute_summary: str | None = None
    # IDs of existing Claim rows that support this timeline event
    claim_ids: list[str] | None = None


class PatchTimelineEventRequest(BaseModel):
    event_type: str | None = None
    title: str | None = None
    description: str | None = None
    category: str | None = None
    phase_of_flight: str | None = None
    event_time_utc: datetime | None = None
    event_time_local: datetime | None = None
    relative_offset_seconds: int | None = None
    sequence_index: int | None = None
    time_precision: str | None = None
    severity: str | None = None
    is_disputed: bool | None = None
    dispute_summary: str | None = None


class RawClaimOut(BaseModel):
    id: str
    field_name: str
    claim_type: str
    confidence: float | None
    source_id: str
    is_winning: bool
    notes: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _event_to_out(event: AccidentTimelineEvent) -> TimelineEventOut:
    claims = [
        TimelineClaimSummary(
            claim_id=lnk.claim_id,
            field_name=lnk.claim.field_name if lnk.claim else "",
            claim_type=lnk.claim.claim_type if lnk.claim else "unknown",
            source_id=lnk.claim.source_id if lnk.claim else "",
            link_reason=lnk.link_reason,
        )
        for lnk in event.claim_links
    ]
    return TimelineEventOut(
        id=event.id,
        accident_event_id=event.accident_event_id,
        event_type=event.event_type,
        title=event.title,
        description=event.description,
        category=event.category,
        phase_of_flight=event.phase_of_flight,
        event_time_utc=event.event_time_utc,
        event_time_local=event.event_time_local,
        relative_offset_seconds=event.relative_offset_seconds,
        sequence_index=event.sequence_index,
        time_precision=event.time_precision,
        severity=event.severity,
        confidence_score=float(event.confidence_score) if event.confidence_score is not None else None,
        is_disputed=event.is_disputed,
        dispute_summary=event.dispute_summary,
        source_count=event.source_count,
        created_at=event.created_at,
        updated_at=event.updated_at,
        supporting_claims=claims,
    )


async def _require_accident(db: AsyncSession, event_id: str) -> AccidentEvent:
    row = await db.get(AccidentEvent, event_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Accident {event_id!r} not found.")
    return row


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/accidents/{event_id}/timeline",
    response_model=TimelineOut,
    summary="Reconstructed accident timeline",
    description=(
        "Returns all timeline events for an accident in chronological order, "
        "along with confidence, dispute state, time precision, category, "
        "phase_of_flight, source_count, and inline claim/provenance summaries. "
        "An empty list is returned (not a 404) when no events have been recorded yet."
    ),
)
async def get_timeline(
    event_id: str,
    db: AsyncSession = Depends(get_read_db),
) -> TimelineOut:
    await _require_accident(db, event_id)
    events = await TimelineReconstructionService.get_ordered_timeline(db, event_id)
    return TimelineOut(
        accident_event_id=event_id,
        event_count=len(events),
        events=[_event_to_out(e) for e in events],
    )


@router.get(
    "/api/v1/accidents/{event_id}/timeline/claims",
    response_model=list[RawClaimOut],
    summary="Raw claims used for timeline reconstruction",
    description=(
        "Returns the raw Claim rows that are linked to any timeline event for this "
        "accident.  Useful for provenance deep-dives and building custom timeline views."
    ),
)
async def get_timeline_claims(
    event_id: str,
    db: AsyncSession = Depends(get_read_db),
) -> list[RawClaimOut]:
    await _require_accident(db, event_id)
    claims = await TimelineReconstructionService.get_supporting_claims(db, event_id)
    return [RawClaimOut.model_validate(c) for c in claims]


@router.post(
    "/api/v1/accidents/{event_id}/timeline/rebuild",
    response_model=TimelineOut,
    summary="Rebuild / refresh the accident timeline (reviewer only)",
    description=(
        "Recomputes confidence scores and source counts for all existing timeline events "
        "from their linked claims.  Does NOT auto-extract new events from raw data — "
        "that is reserved for a future extraction pipeline."
    ),
)
async def rebuild_timeline(
    event_id: str,
    db: AsyncSession = Depends(get_db),
    operator: OperatorContext = Depends(require_reviewer),
) -> TimelineOut:
    await _require_accident(db, event_id)
    events = await TimelineReconstructionService.rebuild_timeline(
        db, accident_event_id=event_id, operator_id=operator.id or "system"
    )
    return TimelineOut(
        accident_event_id=event_id,
        event_count=len(events),
        events=[_event_to_out(e) for e in events],
    )


@router.post(
    "/api/v1/accidents/{event_id}/timeline/events",
    response_model=TimelineEventOut,
    status_code=201,
    summary="Create a manually curated timeline event (reviewer only)",
)
async def create_timeline_event(
    event_id: str,
    body: CreateTimelineEventRequest,
    db: AsyncSession = Depends(get_db),
    operator: OperatorContext = Depends(require_reviewer),
) -> TimelineEventOut:
    await _require_accident(db, event_id)
    event = await TimelineReconstructionService.create_event(
        db,
        accident_event_id=event_id,
        event_type=body.event_type,
        title=body.title,
        description=body.description,
        category=body.category,
        phase_of_flight=body.phase_of_flight,
        event_time_utc=body.event_time_utc,
        event_time_local=body.event_time_local,
        relative_offset_seconds=body.relative_offset_seconds,
        sequence_index=body.sequence_index,
        time_precision=body.time_precision,
        severity=body.severity,
        is_disputed=body.is_disputed,
        dispute_summary=body.dispute_summary,
        claim_ids=body.claim_ids,
    )
    return _event_to_out(event)


@router.patch(
    "/api/v1/timeline/events/{timeline_event_id}",
    response_model=TimelineEventOut,
    summary="Update a timeline event (reviewer only)",
)
async def update_timeline_event(
    timeline_event_id: str,
    body: PatchTimelineEventRequest,
    db: AsyncSession = Depends(get_db),
    operator: OperatorContext = Depends(require_reviewer),
) -> TimelineEventOut:
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    event = await TimelineReconstructionService.update_event(
        db, event_id=timeline_event_id, updates=updates
    )
    if event is None:
        raise HTTPException(
            status_code=404, detail=f"Timeline event {timeline_event_id!r} not found."
        )
    return _event_to_out(event)


@router.delete(
    "/api/v1/timeline/events/{timeline_event_id}",
    status_code=204,
    summary="Delete a timeline event (reviewer only)",
    description="Hard-deletes the timeline event and its claim links (cascade).",
)
async def delete_timeline_event(
    timeline_event_id: str,
    db: AsyncSession = Depends(get_db),
    operator: OperatorContext = Depends(require_reviewer),
) -> None:
    deleted = await TimelineReconstructionService.delete_event(
        db, event_id=timeline_event_id
    )
    if not deleted:
        raise HTTPException(
            status_code=404, detail=f"Timeline event {timeline_event_id!r} not found."
        )
