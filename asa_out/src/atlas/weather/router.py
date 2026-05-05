"""
Weather Context API endpoints.

Routes
------
GET  /api/v1/accidents/{event_id}/weather
     Return all weather observations with parsed fields, station info,
     distance, time delta, confidence, dispute state, and claim provenance.

POST /api/v1/accidents/{event_id}/weather
     Reviewer/admin — create a weather observation (raw METAR auto-parsed).

PATCH /api/v1/weather/observations/{obs_id}
     Reviewer/admin — partial update (re-parses METAR if raw text changes).

DELETE /api/v1/weather/observations/{obs_id}
     Reviewer/admin — hard-delete observation and its claim links (cascade).

POST /api/v1/accidents/{event_id}/weather/rebuild
     Reviewer/admin — recompute confidence scores from current claims/data.

GET /api/v1/accidents/{event_id}/weather/claims
     Return raw claims linked to any weather observation for this accident.
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
from atlas.models.orm import AccidentEvent, AccidentWeatherObservation, WeatherReportType
from atlas.weather.service import WeatherContextService

log = structlog.get_logger(__name__)
router = APIRouter(tags=["weather"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class WeatherClaimSummary(BaseModel):
    claim_id: str
    field_name: str
    claim_type: str
    source_id: str
    link_reason: str


class WeatherObservationOut(BaseModel):
    id: str
    accident_event_id: str
    source_id: str | None

    # Station
    station_identifier: str | None
    station_name: str | None
    station_latitude: float | None
    station_longitude: float | None
    distance_to_accident_km: float | None

    # Temporal
    observation_time_utc: datetime | None
    accident_time_delta_minutes: float | None

    # Report
    report_type: str
    raw_report_text: str | None
    parsed_data: dict[str, Any] | None

    # Canonical fields
    temperature_c: float | None
    dew_point_c: float | None
    wind_direction_degrees: int | None
    wind_speed_kt: float | None
    wind_gust_kt: float | None
    visibility_m: float | None
    ceiling_ft: int | None
    altimeter_hpa: float | None
    precipitation_type: str | None
    thunderstorm_present: bool | None
    icing_risk: str | None
    turbulence_risk: str | None
    flight_rules: str | None

    # Quality
    confidence_score: float | None
    is_disputed: bool
    dispute_summary: str | None

    created_at: datetime
    updated_at: datetime

    # Provenance
    supporting_claims: list[WeatherClaimSummary]

    # Contextual — weather does NOT imply causation
    causation_note: str = (
        "Weather data is contextual evidence only. Causation is not asserted "
        "unless explicitly supported by an official source claim."
    )

    model_config = {"from_attributes": True}


class WeatherContextOut(BaseModel):
    accident_event_id: str
    observation_count: int
    observations: list[WeatherObservationOut]


class CreateWeatherObservationRequest(BaseModel):
    report_type: str = Field(
        default="metar",
        description="metar | taf | pirep | radar | satellite | report_summary | manual",
    )
    raw_report_text: str | None = Field(
        None,
        description="Verbatim METAR/TAF string — auto-parsed when report_type=metar",
    )
    source_id: str | None = None
    station_identifier: str | None = None
    station_name: str | None = None
    station_latitude: float | None = None
    station_longitude: float | None = None
    observation_time_utc: datetime | None = None
    # Caller-supplied canonical fields (optional — METAR parse overrides these)
    temperature_c: float | None = None
    dew_point_c: float | None = None
    wind_direction_degrees: int | None = None
    wind_speed_kt: float | None = None
    wind_gust_kt: float | None = None
    visibility_m: float | None = None
    ceiling_ft: int | None = None
    altimeter_hpa: float | None = None
    precipitation_type: str | None = None
    thunderstorm_present: bool | None = None
    icing_risk: str | None = None
    turbulence_risk: str | None = None
    flight_rules: str | None = None
    is_disputed: bool = False
    dispute_summary: str | None = None
    claim_ids: list[str] | None = None
    # For distance/delta computation
    accident_lat: float | None = None
    accident_lon: float | None = None
    accident_time_utc: datetime | None = None
    source_tier: int | None = None


class PatchWeatherObservationRequest(BaseModel):
    station_identifier: str | None = None
    station_name: str | None = None
    station_latitude: float | None = None
    station_longitude: float | None = None
    observation_time_utc: datetime | None = None
    report_type: str | None = None
    raw_report_text: str | None = None
    temperature_c: float | None = None
    dew_point_c: float | None = None
    wind_direction_degrees: int | None = None
    wind_speed_kt: float | None = None
    wind_gust_kt: float | None = None
    visibility_m: float | None = None
    ceiling_ft: int | None = None
    altimeter_hpa: float | None = None
    precipitation_type: str | None = None
    thunderstorm_present: bool | None = None
    icing_risk: str | None = None
    turbulence_risk: str | None = None
    flight_rules: str | None = None
    is_disputed: bool | None = None
    dispute_summary: str | None = None


class RawWeatherClaimOut(BaseModel):
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


def _obs_to_out(obs: AccidentWeatherObservation) -> WeatherObservationOut:
    claims = [
        WeatherClaimSummary(
            claim_id=lnk.claim_id,
            field_name=lnk.claim.field_name if lnk.claim else "",
            claim_type=lnk.claim.claim_type if lnk.claim else "unknown",
            source_id=lnk.claim.source_id if lnk.claim else "",
            link_reason=lnk.link_reason,
        )
        for lnk in obs.claim_links
    ]
    return WeatherObservationOut(
        id=obs.id,
        accident_event_id=obs.accident_event_id,
        source_id=obs.source_id,
        station_identifier=obs.station_identifier,
        station_name=obs.station_name,
        station_latitude=float(obs.station_latitude) if obs.station_latitude is not None else None,
        station_longitude=float(obs.station_longitude) if obs.station_longitude is not None else None,
        distance_to_accident_km=float(obs.distance_to_accident_km) if obs.distance_to_accident_km is not None else None,
        observation_time_utc=obs.observation_time_utc,
        accident_time_delta_minutes=float(obs.accident_time_delta_minutes) if obs.accident_time_delta_minutes is not None else None,
        report_type=obs.report_type,
        raw_report_text=obs.raw_report_text,
        parsed_data=obs.parsed_data,
        temperature_c=float(obs.temperature_c) if obs.temperature_c is not None else None,
        dew_point_c=float(obs.dew_point_c) if obs.dew_point_c is not None else None,
        wind_direction_degrees=obs.wind_direction_degrees,
        wind_speed_kt=float(obs.wind_speed_kt) if obs.wind_speed_kt is not None else None,
        wind_gust_kt=float(obs.wind_gust_kt) if obs.wind_gust_kt is not None else None,
        visibility_m=float(obs.visibility_m) if obs.visibility_m is not None else None,
        ceiling_ft=obs.ceiling_ft,
        altimeter_hpa=float(obs.altimeter_hpa) if obs.altimeter_hpa is not None else None,
        precipitation_type=obs.precipitation_type,
        thunderstorm_present=obs.thunderstorm_present,
        icing_risk=obs.icing_risk,
        turbulence_risk=obs.turbulence_risk,
        flight_rules=obs.flight_rules,
        confidence_score=float(obs.confidence_score) if obs.confidence_score is not None else None,
        is_disputed=obs.is_disputed,
        dispute_summary=obs.dispute_summary,
        created_at=obs.created_at,
        updated_at=obs.updated_at,
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
    "/api/v1/accidents/{event_id}/weather",
    response_model=WeatherContextOut,
    summary="Weather context for an accident",
    description=(
        "Returns all weather observations for an accident, ordered by observation time. "
        "Each observation includes parsed fields, station metadata, distance and time-delta "
        "from the accident, confidence score, dispute state, and inline claim provenance. "
        "Weather is contextual evidence only — not automatically causal."
    ),
)
async def get_weather(
    event_id: str,
    db: AsyncSession = Depends(get_read_db),
) -> WeatherContextOut:
    await _require_accident(db, event_id)
    obs_list = await WeatherContextService.get_observations(db, event_id)
    return WeatherContextOut(
        accident_event_id=event_id,
        observation_count=len(obs_list),
        observations=[_obs_to_out(o) for o in obs_list],
    )


@router.post(
    "/api/v1/accidents/{event_id}/weather",
    response_model=WeatherObservationOut,
    status_code=201,
    summary="Add a weather observation (reviewer only)",
    description=(
        "Creates a weather observation for the accident. "
        "If report_type=metar and raw_report_text is provided, METAR fields are "
        "automatically parsed and normalized into canonical columns. "
        "Confidence is computed from source tier, time proximity, and station distance."
    ),
)
async def create_weather_observation(
    event_id: str,
    body: CreateWeatherObservationRequest,
    db: AsyncSession = Depends(get_db),
    operator: OperatorContext = Depends(require_reviewer),
) -> WeatherObservationOut:
    await _require_accident(db, event_id)
    obs = await WeatherContextService.create_observation(
        db,
        accident_event_id=event_id,
        report_type=body.report_type,
        raw_report_text=body.raw_report_text,
        source_id=body.source_id,
        station_identifier=body.station_identifier,
        station_name=body.station_name,
        station_latitude=body.station_latitude,
        station_longitude=body.station_longitude,
        observation_time_utc=body.observation_time_utc,
        temperature_c=body.temperature_c,
        dew_point_c=body.dew_point_c,
        wind_direction_degrees=body.wind_direction_degrees,
        wind_speed_kt=body.wind_speed_kt,
        wind_gust_kt=body.wind_gust_kt,
        visibility_m=body.visibility_m,
        ceiling_ft=body.ceiling_ft,
        altimeter_hpa=body.altimeter_hpa,
        precipitation_type=body.precipitation_type,
        thunderstorm_present=body.thunderstorm_present,
        icing_risk=body.icing_risk,
        turbulence_risk=body.turbulence_risk,
        flight_rules=body.flight_rules,
        is_disputed=body.is_disputed,
        dispute_summary=body.dispute_summary,
        claim_ids=body.claim_ids,
        accident_lat=body.accident_lat,
        accident_lon=body.accident_lon,
        accident_time_utc=body.accident_time_utc,
        source_tier=body.source_tier,
    )
    return _obs_to_out(obs)


@router.patch(
    "/api/v1/weather/observations/{obs_id}",
    response_model=WeatherObservationOut,
    summary="Update a weather observation (reviewer only)",
)
async def update_weather_observation(
    obs_id: str,
    body: PatchWeatherObservationRequest,
    db: AsyncSession = Depends(get_db),
    operator: OperatorContext = Depends(require_reviewer),
) -> WeatherObservationOut:
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    obs = await WeatherContextService.update_observation(db, obs_id=obs_id, updates=updates)
    if obs is None:
        raise HTTPException(status_code=404, detail=f"Observation {obs_id!r} not found.")
    return _obs_to_out(obs)


@router.delete(
    "/api/v1/weather/observations/{obs_id}",
    status_code=204,
    summary="Delete a weather observation (reviewer only)",
    description="Hard-deletes the observation and its claim links via cascade.",
)
async def delete_weather_observation(
    obs_id: str,
    db: AsyncSession = Depends(get_db),
    operator: OperatorContext = Depends(require_reviewer),
) -> None:
    deleted = await WeatherContextService.delete_observation(db, obs_id=obs_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Observation {obs_id!r} not found.")


@router.post(
    "/api/v1/accidents/{event_id}/weather/rebuild",
    response_model=WeatherContextOut,
    summary="Rebuild weather context (reviewer only)",
    description=(
        "Recomputes confidence scores for all existing observations from current data. "
        "Re-parses raw METAR text if parsed_data is absent. "
        "Does not fetch new observations from external APIs — that is a future extension."
    ),
)
async def rebuild_weather(
    event_id: str,
    db: AsyncSession = Depends(get_db),
    operator: OperatorContext = Depends(require_reviewer),
) -> WeatherContextOut:
    await _require_accident(db, event_id)
    obs_list = await WeatherContextService.rebuild_weather(
        db,
        accident_event_id=event_id,
        operator_id=operator.id or "system",
    )
    return WeatherContextOut(
        accident_event_id=event_id,
        observation_count=len(obs_list),
        observations=[_obs_to_out(o) for o in obs_list],
    )


@router.get(
    "/api/v1/accidents/{event_id}/weather/claims",
    response_model=list[RawWeatherClaimOut],
    summary="Raw claims linked to weather observations",
)
async def get_weather_claims(
    event_id: str,
    db: AsyncSession = Depends(get_read_db),
) -> list[RawWeatherClaimOut]:
    await _require_accident(db, event_id)
    claims = await WeatherContextService.get_supporting_claims(db, event_id)
    return [RawWeatherClaimOut.model_validate(c) for c in claims]
