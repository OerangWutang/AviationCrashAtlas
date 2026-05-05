"""
Flight Path Reconstruction API endpoints.

GET  /api/v1/accidents/{id}/flight-path             — full reconstruction payload
GET  /api/v1/accidents/{id}/flight-path/points      — raw ordered points
GET  /api/v1/accidents/{id}/flight-path/profile     — chart-ready profile arrays
GET  /api/v1/accidents/{id}/flight-path/claims      — raw provenance claims

POST /api/v1/accidents/{id}/flight-path/points      — create point (reviewer)
PATCH /api/v1/flight-path/points/{point_id}         — update point (reviewer)
DELETE /api/v1/flight-path/points/{point_id}        — delete point (reviewer)

POST /api/v1/accidents/{id}/flight-path/annotations — create annotation (reviewer)
PATCH /api/v1/flight-path/annotations/{ann_id}      — update annotation (reviewer)
DELETE /api/v1/flight-path/annotations/{ann_id}     — delete annotation (reviewer)

POST /api/v1/accidents/{id}/flight-path/rebuild     — rebuild derived fields (reviewer)
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
from atlas.flight_path.service import (
    FlightPathReconstructionService,
    _annotation_to_dict,
    _point_to_dict,
)
from atlas.models.orm import AccidentEvent, PathPointType, TimePrecision

log = structlog.get_logger(__name__)
router = APIRouter(tags=["flight-path"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class CreatePointRequest(BaseModel):
    point_type: str = Field(default="unknown", description=(
        "departure | enroute | radar | adsb | fdr | cvr_reference | "
        "witness_report | report_estimate | last_known_position | "
        "final_approach | impact | wreckage_location | search_area | "
        "inferred | estimated | planned_route | unknown"
    ))
    source_method: str | None = None
    sequence_index: int | None = None
    recorded_time_utc: datetime | None = None
    relative_offset_seconds: int | None = None
    time_precision: str = TimePrecision.UNKNOWN
    latitude: float | None = None
    longitude: float | None = None
    altitude_ft: float | None = None
    altitude_reference: str | None = None
    radio_altitude_ft: float | None = None
    ground_speed_kt: float | None = None
    indicated_airspeed_kt: float | None = None
    vertical_speed_fpm: float | None = None
    heading_degrees: float | None = None
    track_degrees: float | None = None
    uncertainty_radius_m: float | None = None
    is_disputed: bool = False
    dispute_summary: str | None = None
    notes: str | None = None
    source_id: str | None = None
    claim_ids: list[str] | None = None
    accident_lat: float | None = None
    accident_lon: float | None = None


class PatchPointRequest(BaseModel):
    point_type: str | None = None
    source_method: str | None = None
    sequence_index: int | None = None
    recorded_time_utc: datetime | None = None
    relative_offset_seconds: int | None = None
    time_precision: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    altitude_ft: float | None = None
    altitude_reference: str | None = None
    radio_altitude_ft: float | None = None
    ground_speed_kt: float | None = None
    indicated_airspeed_kt: float | None = None
    vertical_speed_fpm: float | None = None
    heading_degrees: float | None = None
    track_degrees: float | None = None
    uncertainty_radius_m: float | None = None
    is_disputed: bool | None = None
    dispute_summary: str | None = None
    notes: str | None = None


class CreateAnnotationRequest(BaseModel):
    annotation_type: str = Field(..., description=(
        "gpws_sink_rate | gpws_pull_up | terrain_warning | stall_warning | "
        "overspeed_warning | flap_change | gear_change | autopilot_disconnect | "
        "emergency_declaration | atc_communication | crew_communication | "
        "loss_of_contact | altitude_deviation | speed_deviation | route_deviation | "
        "rapid_descent | impact | other"
    ))
    title: str = Field(..., max_length=300)
    description: str | None = None
    flight_path_point_id: str | None = None
    timeline_event_id: str | None = None
    source_id: str | None = None
    annotation_time_utc: datetime | None = None
    relative_offset_seconds: int | None = None
    time_precision: str = TimePrecision.UNKNOWN
    altitude_ft: float | None = None
    radio_altitude_ft: float | None = None
    is_disputed: bool = False
    dispute_summary: str | None = None
    claim_ids: list[str] | None = None


class PatchAnnotationRequest(BaseModel):
    annotation_type: str | None = None
    title: str | None = None
    description: str | None = None
    annotation_time_utc: datetime | None = None
    relative_offset_seconds: int | None = None
    time_precision: str | None = None
    altitude_ft: float | None = None
    radio_altitude_ft: float | None = None
    is_disputed: bool | None = None
    dispute_summary: str | None = None
    flight_path_point_id: str | None = None
    timeline_event_id: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _require_accident(db: AsyncSession, event_id: str) -> AccidentEvent:
    row = await db.get(AccidentEvent, event_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Accident {event_id!r} not found.")
    return row


# ---------------------------------------------------------------------------
# Routes — read
# ---------------------------------------------------------------------------

@router.get(
    "/api/v1/accidents/{event_id}/flight-path",
    summary="Full flight path reconstruction payload",
    description=(
        "Returns ordered path points, segments, annotations, accident site marker, "
        "bounding box, profile data, confidence summary, and provenance. "
        "Raw source data is excluded from this endpoint — use /flight-path/points. "
        "Estimated/inferred points carry is_estimated=true and must not be rendered "
        "as confirmed recorded positions. Disputed points carry is_disputed=true."
    ),
)
async def get_flight_path(
    event_id: str,
    db: AsyncSession = Depends(get_read_db),
) -> dict[str, Any]:
    await _require_accident(db, event_id)
    return await FlightPathReconstructionService.get_reconstruction(db, event_id)


@router.get(
    "/api/v1/accidents/{event_id}/flight-path/points",
    summary="Raw ordered flight path points",
)
async def get_flight_path_points(
    event_id: str,
    db: AsyncSession = Depends(get_read_db),
) -> dict[str, Any]:
    await _require_accident(db, event_id)
    points = await FlightPathReconstructionService.get_points(db, event_id)
    return {
        "accident_event_id": event_id,
        "point_count": len(points),
        "points": [_point_to_dict(p) for p in points],
    }


@router.get(
    "/api/v1/accidents/{event_id}/flight-path/profile",
    summary="Chart-ready altitude/speed/vertical-speed profile",
    description=(
        "Returns arrays of altitude, speed, vertical speed, and distance-to-impact "
        "for chart rendering.  Each element includes is_estimated and time_precision "
        "so charts can render inferred values differently from recorded values."
    ),
)
async def get_flight_path_profile(
    event_id: str,
    db: AsyncSession = Depends(get_read_db),
) -> dict[str, Any]:
    await _require_accident(db, event_id)
    return await FlightPathReconstructionService.get_profile(db, event_id)


@router.get(
    "/api/v1/accidents/{event_id}/flight-path/claims",
    summary="Raw claims linked to flight path reconstruction",
)
async def get_flight_path_claims(
    event_id: str,
    db: AsyncSession = Depends(get_read_db),
) -> dict[str, Any]:
    await _require_accident(db, event_id)
    claims = await FlightPathReconstructionService.get_supporting_claims(db, event_id)
    return {
        "accident_event_id": event_id,
        "claim_count": len(claims),
        "claims": [
            {
                "id": c.id,
                "field_name": c.field_name,
                "claim_type": c.claim_type,
                "source_id": c.source_id,
                "is_winning": c.is_winning,
            }
            for c in claims
        ],
    }


# ---------------------------------------------------------------------------
# Routes — points write
# ---------------------------------------------------------------------------

@router.post(
    "/api/v1/accidents/{event_id}/flight-path/points",
    status_code=201,
    summary="Create a flight path point (reviewer only)",
)
async def create_flight_path_point(
    event_id: str,
    body: CreatePointRequest,
    db: AsyncSession = Depends(get_db),
    operator: OperatorContext = Depends(require_reviewer),
) -> dict[str, Any]:
    await _require_accident(db, event_id)
    point = await FlightPathReconstructionService.create_point(
        db,
        accident_event_id=event_id,
        point_type=body.point_type,
        source_method=body.source_method,
        sequence_index=body.sequence_index,
        recorded_time_utc=body.recorded_time_utc,
        relative_offset_seconds=body.relative_offset_seconds,
        time_precision=body.time_precision,
        latitude=body.latitude,
        longitude=body.longitude,
        altitude_ft=body.altitude_ft,
        altitude_reference=body.altitude_reference,
        radio_altitude_ft=body.radio_altitude_ft,
        ground_speed_kt=body.ground_speed_kt,
        indicated_airspeed_kt=body.indicated_airspeed_kt,
        vertical_speed_fpm=body.vertical_speed_fpm,
        heading_degrees=body.heading_degrees,
        track_degrees=body.track_degrees,
        uncertainty_radius_m=body.uncertainty_radius_m,
        is_disputed=body.is_disputed,
        dispute_summary=body.dispute_summary,
        notes=body.notes,
        source_id=body.source_id,
        claim_ids=body.claim_ids,
        accident_lat=body.accident_lat,
        accident_lon=body.accident_lon,
    )
    return _point_to_dict(point)


@router.patch(
    "/api/v1/flight-path/points/{point_id}",
    summary="Update a flight path point (reviewer only)",
)
async def update_flight_path_point(
    point_id: str,
    body: PatchPointRequest,
    db: AsyncSession = Depends(get_db),
    operator: OperatorContext = Depends(require_reviewer),
) -> dict[str, Any]:
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    point = await FlightPathReconstructionService.update_point(db, point_id=point_id, updates=updates)
    if point is None:
        raise HTTPException(status_code=404, detail=f"Point {point_id!r} not found.")
    return _point_to_dict(point)


@router.delete(
    "/api/v1/flight-path/points/{point_id}",
    status_code=204,
    summary="Delete a flight path point (reviewer only)",
)
async def delete_flight_path_point(
    point_id: str,
    db: AsyncSession = Depends(get_db),
    operator: OperatorContext = Depends(require_reviewer),
) -> None:
    deleted = await FlightPathReconstructionService.delete_point(db, point_id=point_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Point {point_id!r} not found.")


# ---------------------------------------------------------------------------
# Routes — annotations write
# ---------------------------------------------------------------------------

@router.post(
    "/api/v1/accidents/{event_id}/flight-path/annotations",
    status_code=201,
    summary="Create a flight path annotation (reviewer only)",
)
async def create_flight_path_annotation(
    event_id: str,
    body: CreateAnnotationRequest,
    db: AsyncSession = Depends(get_db),
    operator: OperatorContext = Depends(require_reviewer),
) -> dict[str, Any]:
    await _require_accident(db, event_id)
    ann = await FlightPathReconstructionService.create_annotation(
        db,
        accident_event_id=event_id,
        annotation_type=body.annotation_type,
        title=body.title,
        description=body.description,
        flight_path_point_id=body.flight_path_point_id,
        timeline_event_id=body.timeline_event_id,
        source_id=body.source_id,
        annotation_time_utc=body.annotation_time_utc,
        relative_offset_seconds=body.relative_offset_seconds,
        time_precision=body.time_precision,
        altitude_ft=body.altitude_ft,
        radio_altitude_ft=body.radio_altitude_ft,
        is_disputed=body.is_disputed,
        dispute_summary=body.dispute_summary,
        claim_ids=body.claim_ids,
    )
    return _annotation_to_dict(ann)


@router.patch(
    "/api/v1/flight-path/annotations/{annotation_id}",
    summary="Update a flight path annotation (reviewer only)",
)
async def update_flight_path_annotation(
    annotation_id: str,
    body: PatchAnnotationRequest,
    db: AsyncSession = Depends(get_db),
    operator: OperatorContext = Depends(require_reviewer),
) -> dict[str, Any]:
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    ann = await FlightPathReconstructionService.update_annotation(
        db, annotation_id=annotation_id, updates=updates
    )
    if ann is None:
        raise HTTPException(status_code=404, detail=f"Annotation {annotation_id!r} not found.")
    return _annotation_to_dict(ann)


@router.delete(
    "/api/v1/flight-path/annotations/{annotation_id}",
    status_code=204,
    summary="Delete a flight path annotation (reviewer only)",
)
async def delete_flight_path_annotation(
    annotation_id: str,
    db: AsyncSession = Depends(get_db),
    operator: OperatorContext = Depends(require_reviewer),
) -> None:
    deleted = await FlightPathReconstructionService.delete_annotation(
        db, annotation_id=annotation_id
    )
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Annotation {annotation_id!r} not found.")


# ---------------------------------------------------------------------------
# Rebuild
# ---------------------------------------------------------------------------

@router.post(
    "/api/v1/accidents/{event_id}/flight-path/rebuild",
    summary="Rebuild derived flight path fields (reviewer only)",
    description=(
        "Recalculates distance_to_impact for all points, regenerates segments, "
        "and refreshes confidence scores. Idempotent — safe to call multiple times."
    ),
)
async def rebuild_flight_path(
    event_id: str,
    db: AsyncSession = Depends(get_db),
    operator: OperatorContext = Depends(require_reviewer),
) -> dict[str, Any]:
    await _require_accident(db, event_id)
    result = await FlightPathReconstructionService.rebuild(
        db, accident_event_id=event_id, operator_id=operator.id or "system"
    )
    return {"accident_event_id": event_id, **result}
