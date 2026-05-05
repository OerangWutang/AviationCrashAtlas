"""
Mechanical / System Failure Tracking API endpoints.

Routes
------
GET  /api/v1/accidents/{event_id}/system-failures
     All failure records for an accident, with optional filters.

GET  /api/v1/accidents/{event_id}/system-failures/claims
     Raw claims linked to any system failure for this accident.

POST /api/v1/accidents/{event_id}/system-failures
     Reviewer/admin — create a system failure record.

PATCH /api/v1/system-failures/{failure_id}
     Reviewer/admin — partial update a failure record.

DELETE /api/v1/system-failures/{failure_id}
     Reviewer/admin — hard-delete (cascades claim links).

POST /api/v1/accidents/{event_id}/system-failures/rebuild
     Reviewer/admin — recompute confidence, dispute state, and source counts.

GET /api/v1/analytics/system-failures
     Platform-wide aggregates by category, status, severity.
     Scoped to one accident when ?accident_id= provided.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from atlas.api.auth import OperatorContext, require_reviewer
from atlas.db.engine import get_db, get_read_db
from atlas.models.orm import (
    AccidentEvent,
    AccidentSystemFailure,
    FailureCategory,
    FailureMode,
    FailureSeverity,
    FailureStatus,
)
from atlas.system_failures.service import SystemFailureTrackingService

log = structlog.get_logger(__name__)
router = APIRouter(tags=["system-failures"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class FailureClaimSummary(BaseModel):
    claim_id: str
    field_name: str
    claim_type: str
    source_id: str
    link_reason: str


class SystemFailureOut(BaseModel):
    id: str
    accident_event_id: str
    source_id: str | None

    failure_category: str
    subsystem: str | None
    component_name: str | None
    manufacturer: str | None
    model_number: str | None
    part_number: str | None
    serial_number: str | None
    failure_mode: str | None

    status: str
    severity: str | None
    is_causal_factor: bool

    occurred_in_flight: bool | None
    detected_before_accident: bool | None
    detected_during_flight: bool | None
    detected_post_accident: bool | None
    maintenance_related: bool | None
    inspection_finding: str | None
    description: str | None

    confidence_score: float | None
    is_disputed: bool
    dispute_summary: str | None
    source_count: int

    created_at: datetime
    updated_at: datetime

    supporting_claims: list[FailureClaimSummary]

    # Display guidance — never present disputed/suspected failures as confirmed
    display_note: str

    model_config = {"from_attributes": True}


class SystemFailuresOut(BaseModel):
    accident_event_id: str
    failure_count: int
    failures: list[SystemFailureOut]


class CreateSystemFailureRequest(BaseModel):
    failure_category: str = Field(
        default="unknown",
        description=(
            "engine | fuel | hydraulic | electrical | avionics | flight_controls | "
            "landing_gear | brakes | tires | structure | pressurization | navigation | "
            "autopilot | rotor_system | propeller | maintenance | other | unknown"
        ),
    )
    subsystem: str | None = None
    component_name: str | None = None
    manufacturer: str | None = None
    model_number: str | None = None
    part_number: str | None = None
    serial_number: str | None = None
    failure_mode: str | None = Field(
        default=None,
        description=(
            "fracture | fatigue | overheating | fire | seizure | leak | blockage | "
            "contamination | software_fault | sensor_error | loss_of_power | "
            "jammed_control | unknown"
        ),
    )
    status: str = Field(default="unknown", description="suspected | reported | confirmed | disputed | ruled_out | unknown")
    severity: str | None = None
    is_causal_factor: bool = Field(
        default=False,
        description="Set True ONLY when a source claim explicitly asserts this failure caused the accident.",
    )
    occurred_in_flight: bool | None = None
    detected_before_accident: bool | None = None
    detected_during_flight: bool | None = None
    detected_post_accident: bool | None = None
    maintenance_related: bool | None = None
    inspection_finding: str | None = None
    description: str | None = None
    is_disputed: bool = False
    dispute_summary: str | None = None
    source_id: str | None = None
    # claim_ids to link (default link_reason = supporting_claim)
    claim_ids: list[str] | None = None
    # Optional per-claim link reasons: {claim_id: link_reason}
    claim_link_reasons: dict[str, str] | None = None


class PatchSystemFailureRequest(BaseModel):
    failure_category: str | None = None
    subsystem: str | None = None
    component_name: str | None = None
    manufacturer: str | None = None
    model_number: str | None = None
    part_number: str | None = None
    serial_number: str | None = None
    failure_mode: str | None = None
    status: str | None = None
    severity: str | None = None
    is_causal_factor: bool | None = None
    occurred_in_flight: bool | None = None
    detected_before_accident: bool | None = None
    detected_during_flight: bool | None = None
    detected_post_accident: bool | None = None
    maintenance_related: bool | None = None
    inspection_finding: str | None = None
    description: str | None = None
    is_disputed: bool | None = None
    dispute_summary: str | None = None


class RawFailureClaimOut(BaseModel):
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
# Display note logic
# ---------------------------------------------------------------------------

def _display_note(failure: AccidentSystemFailure) -> str:
    """
    Return a human-readable note clarifying the epistemic status.
    The UI must never present suspected or disputed failures as confirmed.
    """
    s = failure.status
    if s == FailureStatus.CONFIRMED and failure.is_causal_factor:
        return "Confirmed by a source as a contributing cause of the accident."
    if s == FailureStatus.CONFIRMED:
        return "Confirmed by a source as occurring, but not necessarily the cause."
    if s == FailureStatus.REPORTED:
        return "Reported by at least one source; not yet independently confirmed."
    if s == FailureStatus.SUSPECTED:
        return "Suspected based on available evidence; not yet confirmed or ruled out."
    if s == FailureStatus.RULED_OUT:
        return "A source or investigation explicitly ruled this failure out."
    if s == FailureStatus.DISPUTED:
        return "Sources disagree about whether this failure occurred."
    return "Status unknown; treat as unverified."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _failure_to_out(f: AccidentSystemFailure) -> SystemFailureOut:
    claims = [
        FailureClaimSummary(
            claim_id=lnk.claim_id,
            field_name=lnk.claim.field_name if lnk.claim else "",
            claim_type=lnk.claim.claim_type if lnk.claim else "unknown",
            source_id=lnk.claim.source_id if lnk.claim else "",
            link_reason=lnk.link_reason,
        )
        for lnk in f.claim_links
    ]
    return SystemFailureOut(
        id=f.id,
        accident_event_id=f.accident_event_id,
        source_id=f.source_id,
        failure_category=f.failure_category,
        subsystem=f.subsystem,
        component_name=f.component_name,
        manufacturer=f.manufacturer,
        model_number=f.model_number,
        part_number=f.part_number,
        serial_number=f.serial_number,
        failure_mode=f.failure_mode,
        status=f.status,
        severity=f.severity,
        is_causal_factor=f.is_causal_factor,
        occurred_in_flight=f.occurred_in_flight,
        detected_before_accident=f.detected_before_accident,
        detected_during_flight=f.detected_during_flight,
        detected_post_accident=f.detected_post_accident,
        maintenance_related=f.maintenance_related,
        inspection_finding=f.inspection_finding,
        description=f.description,
        confidence_score=float(f.confidence_score) if f.confidence_score is not None else None,
        is_disputed=f.is_disputed,
        dispute_summary=f.dispute_summary,
        source_count=f.source_count,
        created_at=f.created_at,
        updated_at=f.updated_at,
        supporting_claims=claims,
        display_note=_display_note(f),
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
    "/api/v1/accidents/{event_id}/system-failures",
    response_model=SystemFailuresOut,
    summary="Mechanical / system failure records for an accident",
    description=(
        "Returns all system failure records for an accident. "
        "Each record includes category, subsystem, failure mode, status, severity, "
        "confidence, dispute state, source count, and a display_note that clarifies "
        "the epistemic status (suspected / confirmed / disputed / ruled-out). "
        "The UI must never present suspected or disputed failures as confirmed causes."
    ),
)
async def get_system_failures(
    event_id: str,
    category: str | None = Query(None),
    status: str | None = Query(None),
    severity: str | None = Query(None),
    disputed_only: bool = Query(False),
    maintenance_only: bool = Query(False),
    confirmed_only: bool = Query(False),
    include_ruled_out: bool = Query(True),
    db: AsyncSession = Depends(get_read_db),
) -> SystemFailuresOut:
    await _require_accident(db, event_id)
    failures = await SystemFailureTrackingService.get_failures(
        db, event_id,
        category=category,
        status=status,
        severity=severity,
        disputed_only=disputed_only,
        maintenance_only=maintenance_only,
        confirmed_only=confirmed_only,
        include_ruled_out=include_ruled_out,
    )
    return SystemFailuresOut(
        accident_event_id=event_id,
        failure_count=len(failures),
        failures=[_failure_to_out(f) for f in failures],
    )


@router.get(
    "/api/v1/accidents/{event_id}/system-failures/claims",
    response_model=list[RawFailureClaimOut],
    summary="Raw claims linked to system failures",
)
async def get_system_failure_claims(
    event_id: str,
    db: AsyncSession = Depends(get_read_db),
) -> list[RawFailureClaimOut]:
    await _require_accident(db, event_id)
    claims = await SystemFailureTrackingService.get_supporting_claims(db, event_id)
    return [RawFailureClaimOut.model_validate(c) for c in claims]


@router.post(
    "/api/v1/accidents/{event_id}/system-failures",
    response_model=SystemFailureOut,
    status_code=201,
    summary="Create a system failure record (reviewer only)",
)
async def create_system_failure(
    event_id: str,
    body: CreateSystemFailureRequest,
    db: AsyncSession = Depends(get_db),
    operator: OperatorContext = Depends(require_reviewer),
) -> SystemFailureOut:
    await _require_accident(db, event_id)
    failure = await SystemFailureTrackingService.create_failure(
        db,
        accident_event_id=event_id,
        failure_category=body.failure_category,
        subsystem=body.subsystem,
        component_name=body.component_name,
        manufacturer=body.manufacturer,
        model_number=body.model_number,
        part_number=body.part_number,
        serial_number=body.serial_number,
        failure_mode=body.failure_mode,
        status=body.status,
        severity=body.severity,
        is_causal_factor=body.is_causal_factor,
        occurred_in_flight=body.occurred_in_flight,
        detected_before_accident=body.detected_before_accident,
        detected_during_flight=body.detected_during_flight,
        detected_post_accident=body.detected_post_accident,
        maintenance_related=body.maintenance_related,
        inspection_finding=body.inspection_finding,
        description=body.description,
        is_disputed=body.is_disputed,
        dispute_summary=body.dispute_summary,
        source_id=body.source_id,
        claim_ids=body.claim_ids,
        claim_link_reasons=body.claim_link_reasons,
    )
    return _failure_to_out(failure)


@router.patch(
    "/api/v1/system-failures/{failure_id}",
    response_model=SystemFailureOut,
    summary="Update a system failure record (reviewer only)",
)
async def update_system_failure(
    failure_id: str,
    body: PatchSystemFailureRequest,
    db: AsyncSession = Depends(get_db),
    operator: OperatorContext = Depends(require_reviewer),
) -> SystemFailureOut:
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    failure = await SystemFailureTrackingService.update_failure(
        db, failure_id=failure_id, updates=updates
    )
    if failure is None:
        raise HTTPException(
            status_code=404, detail=f"System failure {failure_id!r} not found."
        )
    return _failure_to_out(failure)


@router.delete(
    "/api/v1/system-failures/{failure_id}",
    status_code=204,
    summary="Delete a system failure record (reviewer only)",
)
async def delete_system_failure(
    failure_id: str,
    db: AsyncSession = Depends(get_db),
    operator: OperatorContext = Depends(require_reviewer),
) -> None:
    deleted = await SystemFailureTrackingService.delete_failure(db, failure_id=failure_id)
    if not deleted:
        raise HTTPException(
            status_code=404, detail=f"System failure {failure_id!r} not found."
        )


@router.post(
    "/api/v1/accidents/{event_id}/system-failures/rebuild",
    response_model=SystemFailuresOut,
    summary="Rebuild system failure records (reviewer only)",
)
async def rebuild_system_failures(
    event_id: str,
    db: AsyncSession = Depends(get_db),
    operator: OperatorContext = Depends(require_reviewer),
) -> SystemFailuresOut:
    await _require_accident(db, event_id)
    failures = await SystemFailureTrackingService.rebuild_failures(
        db, accident_event_id=event_id, operator_id=operator.id or "system"
    )
    return SystemFailuresOut(
        accident_event_id=event_id,
        failure_count=len(failures),
        failures=[_failure_to_out(f) for f in failures],
    )


@router.get(
    "/api/v1/analytics/system-failures",
    summary="Platform-wide or accident-scoped system failure analytics",
)
async def system_failure_analytics(
    accident_id: str | None = Query(None, description="Scope to one accident"),
    db: AsyncSession = Depends(get_read_db),
) -> dict[str, Any]:
    return await SystemFailureTrackingService.get_analytics(
        db, accident_event_id=accident_id
    )
