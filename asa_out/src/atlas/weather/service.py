"""
WeatherContextService
======================
Manages AccidentWeatherObservation records for aviation accidents.

Design principles
-----------------
- Claims remain the source of truth. Weather observations are contextual
  evidence, not causal assertions.
- raw_report_text is never mutated after creation.
- confidence_score factors: source tier, time proximity, station distance,
  report_type quality, dispute penalty.
- Station distance and time delta are computed on creation when coordinates
  and accident time are available.
- METAR parsing is opt-in: supply raw_report_text with report_type="metar"
  and parsed fields are populated automatically.
- The service is stateless — all methods accept AsyncSession; callers commit.

Confidence scoring
------------------
  source_tier_factor   = 1.0 for tier-1 source, 0.7 tier-2, 0.4 tier-3+, 0.5 unknown
  time_proximity_factor = 1.0 if |delta| ≤ 30 min, linear decay to 0 at 180 min
  distance_factor       = 1.0 if dist ≤ 10 km, linear decay to 0 at 100 km
  report_type_factor    = metar/pirep: 1.0 | taf: 0.8 | radar/satellite: 0.7 | manual: 0.4 | other: 0.5
  dispute_penalty       = −0.3 if is_disputed

Extension points
----------------
- Override _extract_candidates() to inject observations from ingestion
  pipeline (NOAA ASOS, AviationWeather.gov METAR archive, etc.)
- Override compute_confidence() to plug in a richer model.

TODO:
- Link AccidentWeatherObservation to AccidentTimelineEvent via a FK or
  junction table for explicit weather ↔ timeline event joins.
- Source-tier lookup via DB query when source_id is present.
"""
from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from atlas.models.orm import (
    AccidentEvent,
    AccidentRecord,
    AccidentWeatherObservation,
    Claim,
    Source,
    WeatherObservationClaim,
    WeatherReportType,
)
from atlas.weather.metar_parser import ParsedMetar, parse_metar

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Geo helper
# ---------------------------------------------------------------------------

_EARTH_RADIUS_KM = 6371.0


def haversine_km(
    lat1: float, lon1: float,
    lat2: float, lon2: float,
) -> float:
    """Great-circle distance in km between two WGS-84 coordinate pairs."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi  = math.radians(lat2 - lat1)
    dlam  = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Confidence helpers
# ---------------------------------------------------------------------------

_REPORT_TYPE_QUALITY: dict[str, float] = {
    WeatherReportType.METAR:          1.00,
    WeatherReportType.PIREP:          1.00,
    WeatherReportType.TAF:            0.80,
    WeatherReportType.RADAR:          0.70,
    WeatherReportType.SATELLITE:      0.70,
    WeatherReportType.REPORT_SUMMARY: 0.50,
    WeatherReportType.MANUAL:         0.40,
}


def compute_confidence(
    *,
    source_tier: int | None,
    time_delta_minutes: float | None,
    distance_km: float | None,
    report_type: str,
    is_disputed: bool,
) -> float:
    """
    Return a 0.0–1.0 confidence score for a weather observation.

    All four factors are averaged, then a dispute penalty is applied.
    Any factor that cannot be computed (missing data) defaults to 0.5
    so it neither inflates nor destroys the overall score.
    """
    # Factor 1: source tier (1 = most authoritative)
    if source_tier == 1:
        src = 1.00
    elif source_tier == 2:
        src = 0.70
    elif source_tier is not None:
        src = 0.40
    else:
        src = 0.50

    # Factor 2: time proximity (decay 30 min → 180 min)
    if time_delta_minutes is None:
        time_f = 0.50
    else:
        abs_delta = abs(time_delta_minutes)
        if abs_delta <= 30:
            time_f = 1.00
        elif abs_delta >= 180:
            time_f = 0.00
        else:
            time_f = 1.0 - (abs_delta - 30) / 150.0

    # Factor 3: station distance (decay 10 km → 100 km)
    if distance_km is None:
        dist_f = 0.50
    elif distance_km <= 10:
        dist_f = 1.00
    elif distance_km >= 100:
        dist_f = 0.00
    else:
        dist_f = 1.0 - (distance_km - 10) / 90.0

    # Factor 4: report type quality
    type_f = _REPORT_TYPE_QUALITY.get(report_type, 0.50)

    score = (src + time_f + dist_f + type_f) / 4.0
    if is_disputed:
        score = max(0.0, score - 0.30)

    return round(score, 3)


# ---------------------------------------------------------------------------
# ParsedMetar → canonical field mapping
# ---------------------------------------------------------------------------

def _apply_parsed_metar(obs: AccidentWeatherObservation, pm: ParsedMetar) -> None:
    """Write ParsedMetar fields into canonical ORM columns."""
    if pm.temperature_c is not None:
        obs.temperature_c = pm.temperature_c
    if pm.dew_point_c is not None:
        obs.dew_point_c = pm.dew_point_c
    if pm.wind_direction_degrees is not None:
        obs.wind_direction_degrees = pm.wind_direction_degrees
    if pm.wind_speed_kt is not None:
        obs.wind_speed_kt = pm.wind_speed_kt
    if pm.wind_gust_kt is not None:
        obs.wind_gust_kt = pm.wind_gust_kt
    if pm.visibility_m is not None:
        obs.visibility_m = pm.visibility_m
    if pm.ceiling_ft is not None:
        obs.ceiling_ft = pm.ceiling_ft
    if pm.altimeter_hpa is not None:
        obs.altimeter_hpa = pm.altimeter_hpa
    if pm.precipitation_type is not None:
        obs.precipitation_type = pm.precipitation_type
    obs.thunderstorm_present = pm.thunderstorm_present
    obs.flight_rules = pm.flight_rules
    # Preserve full parse as structured JSON
    obs.parsed_data = {
        "station": pm.station,
        "observation_time_raw": pm.observation_time_raw,
        "cloud_layers": pm.cloud_layers,
        "altimeter_raw": pm.altimeter_raw,
        "visibility_raw": pm.visibility_raw,
        "wind_variable": pm.wind_variable,
        "remarks": pm.remarks,
        "flight_rules": pm.flight_rules,
    }


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class WeatherContextService:
    """Stateless service — callers own the session and commit."""

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    @staticmethod
    async def get_observations(
        db: AsyncSession,
        accident_event_id: str,
    ) -> list[AccidentWeatherObservation]:
        """Return all weather observations for an accident, oldest first."""
        result = await db.execute(
            select(AccidentWeatherObservation)
            .where(AccidentWeatherObservation.accident_event_id == accident_event_id)
            .options(
                selectinload(AccidentWeatherObservation.claim_links).selectinload(
                    WeatherObservationClaim.claim
                ),
                selectinload(AccidentWeatherObservation.source),
            )
            .order_by(AccidentWeatherObservation.observation_time_utc.asc().nulls_last())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_observation_by_id(
        db: AsyncSession,
        obs_id: str,
    ) -> AccidentWeatherObservation | None:
        result = await db.execute(
            select(AccidentWeatherObservation)
            .where(AccidentWeatherObservation.id == obs_id)
            .options(
                selectinload(AccidentWeatherObservation.claim_links).selectinload(
                    WeatherObservationClaim.claim
                ),
                selectinload(AccidentWeatherObservation.source),
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_supporting_claims(
        db: AsyncSession,
        accident_event_id: str,
    ) -> list[Claim]:
        """Return all claims linked to any weather observation for this accident."""
        obs_subq = (
            select(AccidentWeatherObservation.id)
            .where(AccidentWeatherObservation.accident_event_id == accident_event_id)
            .scalar_subquery()
        )
        result = await db.execute(
            select(Claim)
            .join(WeatherObservationClaim, WeatherObservationClaim.claim_id == Claim.id)
            .where(WeatherObservationClaim.weather_observation_id.in_(obs_subq))
            .distinct()
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    @staticmethod
    async def create_observation(
        db: AsyncSession,
        *,
        accident_event_id: str,
        report_type: str = WeatherReportType.METAR,
        raw_report_text: str | None = None,
        source_id: str | None = None,
        station_identifier: str | None = None,
        station_name: str | None = None,
        station_latitude: float | None = None,
        station_longitude: float | None = None,
        observation_time_utc: datetime | None = None,
        # Canonical fields (caller may pre-populate; METAR parse overrides if raw text given)
        temperature_c: float | None = None,
        dew_point_c: float | None = None,
        wind_direction_degrees: int | None = None,
        wind_speed_kt: float | None = None,
        wind_gust_kt: float | None = None,
        visibility_m: float | None = None,
        ceiling_ft: int | None = None,
        altimeter_hpa: float | None = None,
        precipitation_type: str | None = None,
        thunderstorm_present: bool | None = None,
        icing_risk: str | None = None,
        turbulence_risk: str | None = None,
        flight_rules: str | None = None,
        is_disputed: bool = False,
        dispute_summary: str | None = None,
        claim_ids: list[str] | None = None,
        # Accident coordinates for distance calculation
        accident_lat: float | None = None,
        accident_lon: float | None = None,
        # Accident time for delta calculation
        accident_time_utc: datetime | None = None,
        # Source tier for confidence (caller resolves; avoids extra DB round trip)
        source_tier: int | None = None,
    ) -> AccidentWeatherObservation:
        obs_id = str(uuid.uuid4())

        # Compute station distance
        distance_km: float | None = None
        if all(v is not None for v in (station_latitude, station_longitude, accident_lat, accident_lon)):
            distance_km = round(
                haversine_km(accident_lat, accident_lon, station_latitude, station_longitude), 3  # type: ignore[arg-type]
            )

        # Compute time delta
        delta_minutes: float | None = None
        if observation_time_utc is not None and accident_time_utc is not None:
            obs_ts = observation_time_utc.replace(tzinfo=UTC) if observation_time_utc.tzinfo is None else observation_time_utc
            acc_ts = accident_time_utc.replace(tzinfo=UTC) if accident_time_utc.tzinfo is None else accident_time_utc
            delta_minutes = round((obs_ts - acc_ts).total_seconds() / 60.0, 2)

        obs = AccidentWeatherObservation(
            id=obs_id,
            accident_event_id=accident_event_id,
            source_id=source_id,
            station_identifier=station_identifier,
            station_name=station_name,
            station_latitude=station_latitude,
            station_longitude=station_longitude,
            distance_to_accident_km=distance_km,
            observation_time_utc=observation_time_utc,
            accident_time_delta_minutes=delta_minutes,
            report_type=report_type,
            raw_report_text=raw_report_text,
            temperature_c=temperature_c,
            dew_point_c=dew_point_c,
            wind_direction_degrees=wind_direction_degrees,
            wind_speed_kt=wind_speed_kt,
            wind_gust_kt=wind_gust_kt,
            visibility_m=visibility_m,
            ceiling_ft=ceiling_ft,
            altimeter_hpa=altimeter_hpa,
            precipitation_type=precipitation_type,
            thunderstorm_present=thunderstorm_present,
            icing_risk=icing_risk,
            turbulence_risk=turbulence_risk,
            flight_rules=flight_rules,
            is_disputed=is_disputed,
            dispute_summary=dispute_summary,
        )

        # Auto-parse METAR if raw text supplied
        if raw_report_text and report_type == WeatherReportType.METAR:
            pm = parse_metar(raw_report_text)
            _apply_parsed_metar(obs, pm)
            # Auto-fill station identifier from METAR if not provided
            if not obs.station_identifier and pm.station:
                obs.station_identifier = pm.station

        db.add(obs)
        await db.flush()

        # Attach claim links
        for cid in (claim_ids or []):
            lnk = WeatherObservationClaim(
                id=str(uuid.uuid4()),
                weather_observation_id=obs_id,
                claim_id=cid,
            )
            db.add(lnk)

        # Compute confidence score
        obs.confidence_score = compute_confidence(
            source_tier=source_tier,
            time_delta_minutes=float(delta_minutes) if delta_minutes is not None else None,
            distance_km=float(distance_km) if distance_km is not None else None,
            report_type=report_type,
            is_disputed=is_disputed,
        )

        log.info(
            "weather.observation.created",
            obs_id=obs_id,
            station=obs.station_identifier,
            report_type=report_type,
            confidence=obs.confidence_score,
        )
        return obs

    @staticmethod
    async def update_observation(
        db: AsyncSession,
        *,
        obs_id: str,
        updates: dict[str, Any],
    ) -> AccidentWeatherObservation | None:
        """Partial update; re-parses METAR if raw_report_text is in updates."""
        obs = await WeatherContextService.get_observation_by_id(db, obs_id)
        if obs is None:
            return None

        allowed = {
            "station_identifier", "station_name", "station_latitude", "station_longitude",
            "observation_time_utc", "report_type", "raw_report_text",
            "temperature_c", "dew_point_c", "wind_direction_degrees",
            "wind_speed_kt", "wind_gust_kt", "visibility_m", "ceiling_ft",
            "altimeter_hpa", "precipitation_type", "thunderstorm_present",
            "icing_risk", "turbulence_risk", "flight_rules",
            "is_disputed", "dispute_summary",
        }
        for k, v in updates.items():
            if k in allowed:
                setattr(obs, k, v)

        # Re-parse if raw text changed
        if "raw_report_text" in updates and updates["raw_report_text"] and obs.report_type == WeatherReportType.METAR:
            pm = parse_metar(updates["raw_report_text"])
            _apply_parsed_metar(obs, pm)

        # Recompute confidence from current state
        claim_count = len(obs.claim_links)
        obs.confidence_score = compute_confidence(
            source_tier=None,
            time_delta_minutes=float(obs.accident_time_delta_minutes) if obs.accident_time_delta_minutes is not None else None,
            distance_km=float(obs.distance_to_accident_km) if obs.distance_to_accident_km is not None else None,
            report_type=obs.report_type,
            is_disputed=obs.is_disputed,
        )
        log.info("weather.observation.updated", obs_id=obs_id)
        return obs

    @staticmethod
    async def delete_observation(db: AsyncSession, *, obs_id: str) -> bool:
        obs = await db.get(AccidentWeatherObservation, obs_id)
        if obs is None:
            return False
        await db.delete(obs)
        log.info("weather.observation.deleted", obs_id=obs_id)
        return True

    @staticmethod
    async def rebuild_weather(
        db: AsyncSession,
        *,
        accident_event_id: str,
        operator_id: str,
    ) -> list[AccidentWeatherObservation]:
        """
        Recompute confidence scores for all weather observations of an accident.

        Extension point: inject observations from external weather APIs or
        archival METAR databases here by creating new observations before
        the confidence-refresh loop.
        """
        observations = await WeatherContextService.get_observations(db, accident_event_id)
        for obs in observations:
            # Re-parse if raw METAR text is available and parsed_data is stale
            if obs.raw_report_text and obs.report_type == WeatherReportType.METAR and not obs.parsed_data:
                pm = parse_metar(obs.raw_report_text)
                _apply_parsed_metar(obs, pm)

            obs.confidence_score = compute_confidence(
                source_tier=None,
                time_delta_minutes=float(obs.accident_time_delta_minutes) if obs.accident_time_delta_minutes is not None else None,
                distance_km=float(obs.distance_to_accident_km) if obs.distance_to_accident_km is not None else None,
                report_type=obs.report_type,
                is_disputed=obs.is_disputed,
            )

        log.info(
            "weather.rebuilt",
            accident_event_id=accident_event_id,
            count=len(observations),
            operator_id=operator_id,
        )
        return observations
