"""
Advanced Analytics & Pattern Detection API endpoints.

Routes
------
GET  /api/v1/analytics/advanced/summary
     High-level accident statistics with optional filters.

GET  /api/v1/analytics/advanced/trends
     Accident counts by year (and optionally phase).

GET  /api/v1/analytics/advanced/phase-of-flight
     Distribution by phase of flight, broken down by injury severity.

GET  /api/v1/analytics/advanced/weather
     Weather pattern aggregates across accidents.

GET  /api/v1/analytics/advanced/system-failures
     System failure category aggregates, separated by status.

GET  /api/v1/analytics/advanced/data-quality
     Data quality metrics: missing fields, conflicts, low confidence.

GET  /api/v1/analytics/advanced/disputed
     Disputed data summary across records, failures, and weather.

GET  /api/v1/accidents/{event_id}/similar
     Explainable similar accidents with shared/differing factors.

POST /api/v1/analytics/advanced/rebuild
     Admin-only — rebuilds all pattern tags and persists a snapshot.

POST /api/v1/accidents/{event_id}/patterns/rebuild
     Reviewer/admin — rebuilds pattern tags for one accident.
"""
from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from atlas.api.auth import OperatorContext, require_admin, require_reviewer
from atlas.db.engine import get_db, get_read_db
from atlas.models.orm import AccidentEvent
from atlas.analytics.service import AdvancedAnalyticsService, AnalyticsFilters

log = structlog.get_logger(__name__)
router = APIRouter(tags=["advanced-analytics"])


# ---------------------------------------------------------------------------
# Shared filter extraction
# ---------------------------------------------------------------------------

def _filters_from_query(
    start_year: int | None = None,
    end_year: int | None = None,
    country_code: str | None = None,
    aircraft_make: str | None = None,
    aircraft_model: str | None = None,
    phase_of_flight: str | None = None,
    injury_severity: str | None = None,
    min_fatalities: int | None = None,
    max_fatalities: int | None = None,
    investigation_status: str | None = None,
    min_confidence: float | None = None,
    include_disputed: bool = True,
    include_suspected: bool = True,
    include_ruled_out: bool = True,
) -> AnalyticsFilters:
    return AnalyticsFilters(
        start_year=start_year,
        end_year=end_year,
        country_code=country_code,
        aircraft_make=aircraft_make,
        aircraft_model=aircraft_model,
        phase_of_flight=phase_of_flight,
        injury_severity=injury_severity,
        min_fatalities=min_fatalities,
        max_fatalities=max_fatalities,
        investigation_status=investigation_status,
        min_confidence=min_confidence,
        include_disputed=include_disputed,
        include_suspected=include_suspected,
        include_ruled_out=include_ruled_out,
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
    "/api/v1/analytics/advanced/summary",
    summary="Advanced accident summary analytics",
    description=(
        "Returns high-level statistics over accident records. "
        "Distinguishes disputed and low-confidence records explicitly. "
        "Use filters to scope by year, country, aircraft, phase, severity, confidence."
    ),
)
async def advanced_summary(
    filters: AnalyticsFilters = Depends(_filters_from_query),
    db: AsyncSession = Depends(get_read_db),
) -> dict[str, Any]:
    return await AdvancedAnalyticsService.get_accident_summary(db, filters)


@router.get(
    "/api/v1/analytics/advanced/trends",
    summary="Factor trends over time",
)
async def factor_trends(
    group_by: str = Query("year", description="year | year_phase"),
    filters: AnalyticsFilters = Depends(_filters_from_query),
    db: AsyncSession = Depends(get_read_db),
) -> dict[str, Any]:
    return await AdvancedAnalyticsService.get_factor_trends(db, filters, group_by=group_by)


@router.get(
    "/api/v1/analytics/advanced/phase-of-flight",
    summary="Phase of flight distribution",
)
async def phase_distribution(
    filters: AnalyticsFilters = Depends(_filters_from_query),
    db: AsyncSession = Depends(get_read_db),
) -> dict[str, Any]:
    return await AdvancedAnalyticsService.get_phase_of_flight_distribution(db, filters)


@router.get(
    "/api/v1/analytics/advanced/weather",
    summary="Weather pattern analytics",
    description=(
        "Aggregates weather observations across accidents. "
        "Weather context does NOT imply causation — see causation_note in response."
    ),
)
async def weather_patterns(
    filters: AnalyticsFilters = Depends(_filters_from_query),
    db: AsyncSession = Depends(get_read_db),
) -> dict[str, Any]:
    return await AdvancedAnalyticsService.get_weather_patterns(db, filters)


@router.get(
    "/api/v1/analytics/advanced/system-failures",
    summary="System failure pattern analytics",
    description=(
        "Aggregates system failure records by category and status. "
        "Confirmed, suspected, disputed, and ruled-out counts are shown separately."
    ),
)
async def system_failure_patterns(
    filters: AnalyticsFilters = Depends(_filters_from_query),
    db: AsyncSession = Depends(get_read_db),
) -> dict[str, Any]:
    return await AdvancedAnalyticsService.get_system_failure_patterns(db, filters)


@router.get(
    "/api/v1/analytics/advanced/data-quality",
    summary="Data quality analytics",
    description=(
        "Shows counts of records missing key fields, having conflicts, "
        "low confidence, or single-source data."
    ),
)
async def data_quality(
    filters: AnalyticsFilters = Depends(_filters_from_query),
    db: AsyncSession = Depends(get_read_db),
) -> dict[str, Any]:
    return await AdvancedAnalyticsService.get_data_quality_summary(db, filters)


@router.get(
    "/api/v1/analytics/advanced/disputed",
    summary="Disputed data summary",
)
async def disputed_summary(
    filters: AnalyticsFilters = Depends(_filters_from_query),
    db: AsyncSession = Depends(get_read_db),
) -> dict[str, Any]:
    return await AdvancedAnalyticsService.get_disputed_data_summary(db, filters)


@router.get(
    "/api/v1/accidents/{event_id}/similar",
    summary="Find similar accidents (explainable)",
    description=(
        "Returns accidents similar to the target based on weighted feature matching. "
        "Fatality alone is insufficient for high similarity — shared technical context is required. "
        "Each result includes shared_factors, differing_factors, and a low_confidence_warning."
    ),
)
async def similar_accidents(
    event_id: str,
    limit: int = Query(10, ge=1, le=50),
    min_score: float = Query(0.10, ge=0.0, le=1.0),
    filters: AnalyticsFilters = Depends(_filters_from_query),
    db: AsyncSession = Depends(get_read_db),
) -> dict[str, Any]:
    await _require_accident(db, event_id)
    results = await AdvancedAnalyticsService.find_similar_accidents(
        db, event_id, limit=limit, min_score=min_score, filters=filters
    )
    return {
        "accident_id": event_id,
        "similar_count": len(results),
        "similar_accidents": results,
        "similarity_note": (
            "Similarity is based on shared technical context (aircraft, phase, weather, "
            "failures) — not on shared cause. Each accident is independently investigated."
        ),
    }


@router.post(
    "/api/v1/analytics/advanced/rebuild",
    summary="Rebuild all analytics snapshots (admin only)",
    description="Rebuilds pattern tags for all accidents and persists analytics snapshots.",
)
async def rebuild_all_analytics(
    db: AsyncSession = Depends(get_db),
    operator: OperatorContext = Depends(require_admin),
) -> dict[str, Any]:
    # Fetch all active accident IDs
    from sqlalchemy import select
    from atlas.models.orm import AccidentEvent as AE
    result = await db.execute(
        select(AE.id).where(AE.record_status == "active")
    )
    accident_ids = [row[0] for row in result.all()]

    rebuilt = 0
    for aid in accident_ids:
        await AdvancedAnalyticsService.rebuild_pattern_tags(db, aid)
        rebuilt += 1

    log.info("analytics.rebuild_all", rebuilt=rebuilt, operator=operator.id)
    return {"rebuilt_accident_count": rebuilt, "operator": operator.id}


@router.post(
    "/api/v1/accidents/{event_id}/patterns/rebuild",
    summary="Rebuild pattern tags for one accident (reviewer only)",
)
async def rebuild_accident_patterns(
    event_id: str,
    db: AsyncSession = Depends(get_db),
    operator: OperatorContext = Depends(require_reviewer),
) -> dict[str, Any]:
    await _require_accident(db, event_id)
    tags = await AdvancedAnalyticsService.rebuild_pattern_tags(db, event_id)
    return {
        "accident_event_id": event_id,
        "tag_count": len(tags),
        "tags": [
            {
                "tag_type": t.tag_type,
                "tag_value": t.tag_value,
                "status": t.status,
                "confidence_score": float(t.confidence_score) if t.confidence_score else None,
                "is_disputed": t.is_disputed,
            }
            for t in tags
        ],
    }
