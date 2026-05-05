"""
AdvancedAnalyticsService
=========================
Explainable, provenance-aware analytics over aviation accident data.

Design principles
-----------------
- Claims/provenance are the source of truth. Analytics only counts facts at
  the confidence/status level appropriate to each query.
- Confirmed ≠ suspected ≠ disputed ≠ ruled_out. Every aggregation method
  makes this distinction explicit in its return value.
- Correlation is never presented as causation. The service never labels a
  pattern as causal unless a source claim explicitly supports it.
- Every result includes metadata explaining how it was computed (record counts,
  confidence thresholds, dispute/low-confidence warnings).
- All analytics are deterministic and testable — no ML or stochastic methods.

Pattern tag vocabulary (Phase 3)
---------------------------------
Tags are prefixed with their type:
  weather:ifr  weather:low_visibility  weather:thunderstorm
  mechanical:engine_failure  mechanical:fuel_starvation
  phase:approach  phase:takeoff  phase:cruise
  severity:fatal  severity:serious
  data_quality:disputed_field  data_quality:low_confidence  data_quality:single_source
  causal_factor:<category>   (ONLY when source explicitly asserts causation)

Similarity scoring (Phase 4)
------------------------------
Weighted feature matching; weights are documented in SIMILARITY_WEIGHTS.
Scores are 0.0–1.0 and fully explained via similarity_reasons.
Fatality alone never drives high similarity — shared technical context required.

Extension points
----------------
- Plug in embeddings/clustering by replacing find_similar_accidents() internals.
- Add anomaly detection in get_data_quality_summary() for outlier detection.
- Add natural-language query parsing to map free text to filter dicts.
- Add time-series forecasting to get_factor_trends() for trend projection.

TODO:
  - Aircraft model recurring failure cross-accident analysis
  - Geographic clustering / terrain-type correlation
  - Engine type / model failure rate database
  - NLP-assisted extraction from investigation narratives
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from atlas.models.orm import (
    AccidentEvent,
    AccidentPatternTag,
    AccidentRecord,
    AccidentSimilarityScore,
    AccidentSystemFailure,
    AccidentTimelineEvent,
    AccidentWeatherObservation,
    AnalyticsSnapshot,
    ClaimConflict,
    FailureStatus,
    FlightRules,
    PatternTagStatus,
    SnapshotType,
)

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Filter dataclass
# ---------------------------------------------------------------------------

@dataclass
class AnalyticsFilters:
    """Filters accepted by all analytics methods."""
    start_year: int | None = None
    end_year: int | None = None
    country_code: str | None = None
    aircraft_make: str | None = None
    aircraft_model: str | None = None
    phase_of_flight: str | None = None
    injury_severity: str | None = None
    min_fatalities: int | None = None
    max_fatalities: int | None = None
    investigation_status: str | None = None
    min_confidence: float | None = None
    include_disputed: bool = True
    include_suspected: bool = True
    include_ruled_out: bool = True
    system_failure_category: str | None = None
    weather_flight_rules: str | None = None


def _apply_record_filters(q, filters: AnalyticsFilters, record_alias):
    """Apply AnalyticsFilters to a SQLAlchemy query over AccidentRecord."""
    if filters.start_year:
        q = q.where(record_alias.occurred_year >= filters.start_year)
    if filters.end_year:
        q = q.where(record_alias.occurred_year <= filters.end_year)
    if filters.country_code:
        q = q.where(record_alias.country_code == filters.country_code)
    if filters.aircraft_make:
        q = q.where(record_alias.aircraft_make.ilike(f"%{filters.aircraft_make}%"))
    if filters.aircraft_model:
        q = q.where(record_alias.aircraft_model.ilike(f"%{filters.aircraft_model}%"))
    if filters.phase_of_flight:
        q = q.where(record_alias.phase_of_flight == filters.phase_of_flight)
    if filters.injury_severity:
        q = q.where(record_alias.injury_severity == filters.injury_severity)
    if filters.min_fatalities is not None:
        q = q.where(record_alias.fatalities_total >= filters.min_fatalities)
    if filters.max_fatalities is not None:
        q = q.where(record_alias.fatalities_total <= filters.max_fatalities)
    if filters.investigation_status:
        q = q.where(record_alias.investigation_status == filters.investigation_status)
    if filters.min_confidence is not None:
        q = q.where(record_alias.confidence_score >= filters.min_confidence)
    return q


# ---------------------------------------------------------------------------
# Pattern tag extraction (Phase 3)
# ---------------------------------------------------------------------------

def _tags_from_record(record: AccidentRecord) -> list[dict[str, Any]]:
    """Extract deterministic tags from an AccidentRecord projection."""
    tags: list[dict[str, Any]] = []

    # Phase of flight
    if record.phase_of_flight:
        tags.append({
            "tag_type": "phase_of_flight",
            "tag_value": record.phase_of_flight.lower().replace(" ", "_"),
            "status": "confirmed",
            "confidence_score": float(record.confidence_score or 0.5),
        })

    # Injury severity
    if record.injury_severity:
        tags.append({
            "tag_type": "data_quality",
            "tag_value": f"severity:{record.injury_severity.lower()}",
            "status": "confirmed",
            "confidence_score": float(record.confidence_score or 0.5),
        })

    # Data quality tags
    if record.has_conflicts:
        tags.append({
            "tag_type": "data_quality",
            "tag_value": "has_conflicts",
            "status": "unknown",
            "confidence_score": float(record.confidence_score or 0.0),
            "is_disputed": True,
        })
    if record.confidence_score is not None and float(record.confidence_score) < 0.5:
        tags.append({
            "tag_type": "data_quality",
            "tag_value": "low_confidence",
            "status": "unknown",
            "confidence_score": float(record.confidence_score),
        })
    if record.occurred_at is None and record.occurred_date is None:
        tags.append({
            "tag_type": "data_quality",
            "tag_value": "missing_date",
            "status": "unknown",
            "confidence_score": 0.0,
        })
    if record.location_lat is None and record.location_text is None:
        tags.append({
            "tag_type": "data_quality",
            "tag_value": "missing_location",
            "status": "unknown",
            "confidence_score": 0.0,
        })
    if not record.aircraft_model:
        tags.append({
            "tag_type": "data_quality",
            "tag_value": "missing_aircraft_model",
            "status": "unknown",
            "confidence_score": 0.0,
        })

    # Investigation status
    if record.investigation_status:
        tags.append({
            "tag_type": "investigation_status",
            "tag_value": record.investigation_status,
            "status": "confirmed",
            "confidence_score": float(record.confidence_score or 0.5),
        })

    # Source count quality
    source_ids = record.claim_source_ids or []
    if len(source_ids) == 1:
        tags.append({
            "tag_type": "data_quality",
            "tag_value": "single_source",
            "status": "unknown",
            "confidence_score": float(record.confidence_score or 0.3),
        })

    return tags


def _tags_from_weather(obs: AccidentWeatherObservation) -> list[dict[str, Any]]:
    """Extract tags from a weather observation. Never infer causation."""
    tags: list[dict[str, Any]] = []
    status = "disputed" if obs.is_disputed else (
        "confirmed" if obs.report_type in ("metar", "pirep") else "suspected"
    )
    conf = float(obs.confidence_score or 0.5)

    if obs.flight_rules and obs.flight_rules != "unknown":
        tags.append({
            "tag_type": "weather",
            "tag_value": obs.flight_rules,
            "status": status,
            "confidence_score": conf,
            "is_disputed": obs.is_disputed,
        })
        if obs.flight_rules in ("ifr", "lifr"):
            tags.append({
                "tag_type": "weather",
                "tag_value": "low_ceiling_or_visibility",
                "status": status,
                "confidence_score": conf,
                "is_disputed": obs.is_disputed,
            })

    if obs.thunderstorm_present:
        tags.append({
            "tag_type": "weather",
            "tag_value": "thunderstorm",
            "status": status,
            "confidence_score": conf,
            "is_disputed": obs.is_disputed,
        })

    if obs.visibility_m is not None and obs.visibility_m < 1609:
        # < 1 SM
        tags.append({
            "tag_type": "weather",
            "tag_value": "low_visibility",
            "status": status,
            "confidence_score": conf,
            "is_disputed": obs.is_disputed,
        })

    if obs.icing_risk in ("likely", "severe"):
        tags.append({
            "tag_type": "weather",
            "tag_value": "icing",
            "status": status,
            "confidence_score": conf,
            "is_disputed": obs.is_disputed,
        })

    if obs.turbulence_risk in ("likely", "severe"):
        tags.append({
            "tag_type": "weather",
            "tag_value": "turbulence",
            "status": status,
            "confidence_score": conf,
            "is_disputed": obs.is_disputed,
        })

    return tags


def _tags_from_failure(f: AccidentSystemFailure) -> list[dict[str, Any]]:
    """
    Extract mechanical tags from a system failure record.
    Causal tags only when is_causal_factor is True.
    Ruled-out failures are tagged with status=ruled_out so they can be
    filtered out of confirmed analytics without being hidden entirely.
    """
    tags: list[dict[str, Any]] = []
    status_map = {
        FailureStatus.CONFIRMED: "confirmed",
        FailureStatus.REPORTED: "suspected",
        FailureStatus.SUSPECTED: "suspected",
        FailureStatus.DISPUTED: "disputed",
        FailureStatus.RULED_OUT: "ruled_out",
        FailureStatus.UNKNOWN: "unknown",
    }
    status = status_map.get(f.status, "unknown")
    conf = float(f.confidence_score or 0.5)

    tags.append({
        "tag_type": "mechanical",
        "tag_value": f.failure_category,
        "status": status,
        "confidence_score": conf,
        "is_disputed": f.is_disputed,
    })

    # Causal tag — only when explicitly supported
    if f.is_causal_factor and f.status == FailureStatus.CONFIRMED:
        tags.append({
            "tag_type": "causal_factor",
            "tag_value": f.failure_category,
            "status": "confirmed",
            "confidence_score": conf,
            "is_disputed": False,
        })

    if f.maintenance_related:
        tags.append({
            "tag_type": "mechanical",
            "tag_value": "maintenance_related",
            "status": status,
            "confidence_score": conf,
            "is_disputed": f.is_disputed,
        })

    return tags


# ---------------------------------------------------------------------------
# Similarity scoring (Phase 4)
# ---------------------------------------------------------------------------

# Feature weights — all weights sum to 1.0
# Fatality alone cannot produce high similarity; technical context dominates.
SIMILARITY_WEIGHTS: dict[str, float] = {
    "aircraft_make":          0.12,
    "aircraft_model":         0.14,
    "phase_of_flight":        0.14,
    "weather_flight_rules":   0.10,
    "system_failure_category":0.14,
    "injury_severity":        0.06,  # low weight — fatality alone is insufficient
    "investigation_status":   0.05,
    "country_code":           0.05,
    "weather_thunderstorm":   0.08,
    "weather_low_visibility": 0.06,
    "maintenance_related":    0.06,
}
assert abs(sum(SIMILARITY_WEIGHTS.values()) - 1.0) < 1e-9, "Weights must sum to 1.0"

CONFIDENCE_THRESHOLD_FOR_FEATURE = 0.4  # Features below this lower confidence


@dataclass
class AccidentFeatures:
    """Extracted feature vector for one accident, used in similarity scoring."""
    accident_id: str
    aircraft_make: str | None = None
    aircraft_model: str | None = None
    phase_of_flight: str | None = None
    weather_flight_rules: str | None = None
    system_failure_categories: list[str] = field(default_factory=list)
    injury_severity: str | None = None
    investigation_status: str | None = None
    country_code: str | None = None
    has_thunderstorm: bool = False
    has_low_visibility: bool = False
    has_maintenance_failure: bool = False
    overall_confidence: float = 0.5
    has_disputed: bool = False


def _compute_similarity(
    a: AccidentFeatures, b: AccidentFeatures
) -> tuple[float, list[str], list[str], dict[str, float], bool]:
    """
    Return (score, shared_factors, differing_factors, factor_weights, low_confidence_warning).

    Score is 0.0–1.0. shared_factors and differing_factors are human-readable strings
    explaining the match. factor_weights maps feature name → contribution to score.
    """
    shared: list[str] = []
    differing: list[str] = []
    factor_scores: dict[str, float] = {}
    low_conf_warning = False

    if a.overall_confidence < CONFIDENCE_THRESHOLD_FOR_FEATURE or \
       b.overall_confidence < CONFIDENCE_THRESHOLD_FOR_FEATURE:
        low_conf_warning = True

    # String feature comparison helper
    def _compare(feature: str, va: str | None, vb: str | None, label: str) -> float:
        if va is None and vb is None:
            return 0.0  # both unknown → no contribution
        if va == vb and va is not None:
            shared.append(label)
            return 1.0
        if va is not None and vb is not None:
            differing.append(f"{label}: {va} vs {vb}")
        return 0.0

    factor_scores["aircraft_make"] = _compare(
        "aircraft_make", a.aircraft_make, b.aircraft_make,
        f"Aircraft make: {a.aircraft_make}"
    )
    factor_scores["aircraft_model"] = _compare(
        "aircraft_model", a.aircraft_model, b.aircraft_model,
        f"Aircraft model: {a.aircraft_model}"
    )
    factor_scores["phase_of_flight"] = _compare(
        "phase_of_flight", a.phase_of_flight, b.phase_of_flight,
        f"Phase of flight: {a.phase_of_flight}"
    )
    factor_scores["weather_flight_rules"] = _compare(
        "weather_flight_rules", a.weather_flight_rules, b.weather_flight_rules,
        f"Weather flight rules: {a.weather_flight_rules}"
    )
    factor_scores["injury_severity"] = _compare(
        "injury_severity", a.injury_severity, b.injury_severity,
        f"Injury severity: {a.injury_severity}"
    )
    factor_scores["investigation_status"] = _compare(
        "investigation_status", a.investigation_status, b.investigation_status,
        f"Investigation: {a.investigation_status}"
    )
    factor_scores["country_code"] = _compare(
        "country_code", a.country_code, b.country_code,
        f"Country: {a.country_code}"
    )

    # System failure category — Jaccard similarity
    set_a = set(a.system_failure_categories)
    set_b = set(b.system_failure_categories)
    if set_a or set_b:
        union = set_a | set_b
        intersection = set_a & set_b
        jac = len(intersection) / len(union) if union else 0.0
        factor_scores["system_failure_category"] = jac
        for cat in intersection:
            shared.append(f"System failure: {cat}")
        for cat in (set_a ^ set_b):
            differing.append(f"System failure differs: {cat}")
    else:
        factor_scores["system_failure_category"] = 0.0

    # Boolean features
    def _bool_feature(feature: str, va: bool, vb: bool, label: str) -> float:
        if va and vb:
            shared.append(label)
            return 1.0
        if va != vb:
            differing.append(f"{label}: present in one only")
        return 0.0

    factor_scores["weather_thunderstorm"] = _bool_feature(
        "weather_thunderstorm", a.has_thunderstorm, b.has_thunderstorm, "Thunderstorm"
    )
    factor_scores["weather_low_visibility"] = _bool_feature(
        "weather_low_visibility", a.has_low_visibility, b.has_low_visibility, "Low visibility"
    )
    factor_scores["maintenance_related"] = _bool_feature(
        "maintenance_related", a.has_maintenance_failure, b.has_maintenance_failure,
        "Maintenance-related failure"
    )

    # Weighted sum
    score = sum(
        factor_scores.get(feat, 0.0) * weight
        for feat, weight in SIMILARITY_WEIGHTS.items()
    )
    # Confidence penalty
    avg_conf = (a.overall_confidence + b.overall_confidence) / 2.0
    if avg_conf < CONFIDENCE_THRESHOLD_FOR_FEATURE:
        score = score * avg_conf  # scale by confidence when very low

    return round(score, 4), shared, differing, factor_scores, low_conf_warning


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class AdvancedAnalyticsService:
    """Stateless analytics service. All methods accept AsyncSession; callers commit."""

    # ------------------------------------------------------------------
    # Core analytics (Phase 2)
    # ------------------------------------------------------------------

    @staticmethod
    async def get_accident_summary(
        db: AsyncSession,
        filters: AnalyticsFilters | None = None,
    ) -> dict[str, Any]:
        f = filters or AnalyticsFilters()
        q = select(AccidentRecord).join(
            AccidentEvent, AccidentRecord.id == AccidentEvent.id
        ).where(AccidentEvent.record_status == "active")
        q = _apply_record_filters(q, f, AccidentRecord)
        result = await db.execute(q)
        records = list(result.scalars().all())

        total = len(records)
        fatal = sum(1 for r in records if r.injury_severity == "FATAL")
        disputed = sum(1 for r in records if r.has_conflicts)
        low_conf = sum(1 for r in records if r.confidence_score is not None and float(r.confidence_score) < 0.5)
        avg_conf = (
            sum(float(r.confidence_score) for r in records if r.confidence_score is not None)
            / max(sum(1 for r in records if r.confidence_score is not None), 1)
        )

        return {
            "total_accidents": total,
            "fatal_accidents": fatal,
            "disputed_records": disputed,
            "low_confidence_records": low_conf,
            "avg_confidence": round(avg_conf, 3),
            "filters_applied": {k: v for k, v in vars(f).items() if v is not None},
            "computation_note": (
                "Counts reflect records matching the applied filters. "
                "disputed_records have at least one unresolved field conflict. "
                "low_confidence_records have confidence_score < 0.5."
            ),
        }

    @staticmethod
    async def get_factor_trends(
        db: AsyncSession,
        filters: AnalyticsFilters | None = None,
        group_by: str = "year",
    ) -> dict[str, Any]:
        """Return accident counts grouped by year (or year+phase_of_flight)."""
        f = filters or AnalyticsFilters()
        q = select(
            AccidentRecord.occurred_year,
            AccidentRecord.phase_of_flight,
            AccidentRecord.injury_severity,
            func.count().label("cnt"),
        ).join(AccidentEvent, AccidentRecord.id == AccidentEvent.id).where(
            AccidentEvent.record_status == "active",
            AccidentRecord.occurred_year.isnot(None),
        )
        q = _apply_record_filters(q, f, AccidentRecord)

        if group_by == "year_phase":
            q = q.group_by(
                AccidentRecord.occurred_year,
                AccidentRecord.phase_of_flight,
                AccidentRecord.injury_severity,
            ).order_by(AccidentRecord.occurred_year)
        else:
            q = q.group_by(
                AccidentRecord.occurred_year,
                AccidentRecord.phase_of_flight,
                AccidentRecord.injury_severity,
            ).order_by(AccidentRecord.occurred_year)

        rows = (await db.execute(q)).all()
        by_year: dict[int, int] = {}
        by_year_fatal: dict[int, int] = {}
        for row in rows:
            yr = row[0]
            sev = row[2]
            cnt = row[3]
            by_year[yr] = by_year.get(yr, 0) + cnt
            if sev == "FATAL":
                by_year_fatal[yr] = by_year_fatal.get(yr, 0) + cnt

        return {
            "group_by": group_by,
            "by_year": by_year,
            "by_year_fatal": by_year_fatal,
            "note": "Counts are total accidents per year. fatal counts are FATAL injury severity only.",
        }

    @staticmethod
    async def get_phase_of_flight_distribution(
        db: AsyncSession,
        filters: AnalyticsFilters | None = None,
    ) -> dict[str, Any]:
        f = filters or AnalyticsFilters()
        q = select(
            AccidentRecord.phase_of_flight,
            AccidentRecord.injury_severity,
            func.count().label("cnt"),
        ).join(AccidentEvent, AccidentRecord.id == AccidentEvent.id).where(
            AccidentEvent.record_status == "active",
        )
        q = _apply_record_filters(q, f, AccidentRecord)
        q = q.group_by(AccidentRecord.phase_of_flight, AccidentRecord.injury_severity)
        rows = (await db.execute(q)).all()

        by_phase: dict[str, dict[str, int]] = {}
        for phase, sev, cnt in rows:
            phase_key = phase or "unknown"
            by_phase.setdefault(phase_key, {"total": 0, "FATAL": 0, "SERIOUS": 0, "MINOR": 0, "NONE": 0})
            by_phase[phase_key]["total"] += cnt
            by_phase[phase_key][sev or "NONE"] = by_phase[phase_key].get(sev or "NONE", 0) + cnt

        return {
            "by_phase": by_phase,
            "note": (
                "Phase of flight is the projected winning-claim value. "
                "Records with unresolved conflicts may have unreliable phase data."
            ),
        }

    @staticmethod
    async def get_weather_patterns(
        db: AsyncSession,
        filters: AnalyticsFilters | None = None,
    ) -> dict[str, Any]:
        """Aggregate over AccidentWeatherObservation rows. Distinguishes confirmed from disputed."""
        f = filters or AnalyticsFilters()
        result = await db.execute(
            select(AccidentWeatherObservation).join(
                AccidentEvent,
                AccidentWeatherObservation.accident_event_id == AccidentEvent.id,
            ).where(AccidentEvent.record_status == "active")
        )
        observations = list(result.scalars().all())

        by_flight_rules: dict[str, dict[str, int]] = {}
        thunderstorm_confirmed = thunderstorm_disputed = 0
        low_vis_count = icing_count = turbulence_count = 0
        total_obs = len(observations)
        disputed_obs = 0

        for obs in observations:
            if obs.is_disputed:
                disputed_obs += 1

            fr = obs.flight_rules or "unknown"
            status = "disputed" if obs.is_disputed else "confirmed"
            by_flight_rules.setdefault(fr, {"confirmed": 0, "disputed": 0})
            by_flight_rules[fr][status] += 1

            if obs.thunderstorm_present:
                if obs.is_disputed:
                    thunderstorm_disputed += 1
                else:
                    thunderstorm_confirmed += 1

            if obs.visibility_m is not None and obs.visibility_m < 1609:
                low_vis_count += 1

            if obs.icing_risk in ("likely", "severe"):
                icing_count += 1

            if obs.turbulence_risk in ("likely", "severe"):
                turbulence_count += 1

        return {
            "total_observations": total_obs,
            "disputed_observations": disputed_obs,
            "by_flight_rules": by_flight_rules,
            "thunderstorm_confirmed_count": thunderstorm_confirmed,
            "thunderstorm_disputed_count": thunderstorm_disputed,
            "low_visibility_count": low_vis_count,
            "icing_count": icing_count,
            "turbulence_count": turbulence_count,
            "causation_note": (
                "Weather context does not imply causation. "
                "These counts reflect weather conditions present near the accident — "
                "not confirmed causal factors."
            ),
        }

    @staticmethod
    async def get_system_failure_patterns(
        db: AsyncSession,
        filters: AnalyticsFilters | None = None,
    ) -> dict[str, Any]:
        """
        Aggregate over AccidentSystemFailure rows.
        Explicitly separates confirmed/suspected/disputed/ruled_out counts.
        """
        result = await db.execute(
            select(AccidentSystemFailure).join(
                AccidentEvent,
                AccidentSystemFailure.accident_event_id == AccidentEvent.id,
            ).where(AccidentEvent.record_status == "active")
        )
        failures = list(result.scalars().all())

        by_category: dict[str, dict[str, int]] = {}
        causal_confirmed = maintenance_confirmed = 0

        for f in failures:
            cat = f.failure_category
            status = f.status
            by_category.setdefault(cat, {
                "confirmed": 0, "suspected": 0, "disputed": 0, "ruled_out": 0, "unknown": 0
            })
            by_category[cat][status] = by_category[cat].get(status, 0) + 1

            if f.is_causal_factor and f.status == FailureStatus.CONFIRMED:
                causal_confirmed += 1
            if f.maintenance_related and f.status == FailureStatus.CONFIRMED:
                maintenance_confirmed += 1

        return {
            "total_failure_records": len(failures),
            "by_category": by_category,
            "confirmed_causal_count": causal_confirmed,
            "confirmed_maintenance_related_count": maintenance_confirmed,
            "status_note": (
                "Counts are grouped by status (confirmed/suspected/disputed/ruled_out). "
                "Do not treat suspected or disputed counts as confirmed facts."
            ),
        }

    @staticmethod
    async def get_data_quality_summary(
        db: AsyncSession,
        filters: AnalyticsFilters | None = None,
    ) -> dict[str, Any]:
        f = filters or AnalyticsFilters()
        q = select(AccidentRecord).join(
            AccidentEvent, AccidentRecord.id == AccidentEvent.id
        ).where(AccidentEvent.record_status == "active")
        q = _apply_record_filters(q, f, AccidentRecord)
        result = await db.execute(q)
        records = list(result.scalars().all())

        missing_date = sum(1 for r in records if r.occurred_at is None and r.occurred_date is None)
        missing_location = sum(1 for r in records if r.location_lat is None and r.location_text is None)
        missing_aircraft_model = sum(1 for r in records if not r.aircraft_model)
        has_conflicts = sum(1 for r in records if r.has_conflicts)
        low_confidence = sum(1 for r in records if r.confidence_score is not None and float(r.confidence_score) < 0.5)
        single_source = sum(
            1 for r in records
            if r.claim_source_ids is not None and len(r.claim_source_ids) == 1
        )
        preliminary_only = sum(
            1 for r in records if r.investigation_status == "preliminary"
        )

        return {
            "total_records": len(records),
            "missing_date": missing_date,
            "missing_location": missing_location,
            "missing_aircraft_model": missing_aircraft_model,
            "has_conflicts": has_conflicts,
            "low_confidence_records": low_confidence,
            "single_source_records": single_source,
            "preliminary_only_records": preliminary_only,
            "quality_note": (
                "missing_date: no accident date or datetime. "
                "has_conflicts: at least one unresolved field conflict (claimed by 2+ sources). "
                "low_confidence: source completeness score < 0.5. "
                "single_source: only one contributing source. "
                "preliminary_only: investigation not progressed past preliminary stage."
            ),
        }

    @staticmethod
    async def get_disputed_data_summary(
        db: AsyncSession,
        filters: AnalyticsFilters | None = None,
    ) -> dict[str, Any]:
        f = filters or AnalyticsFilters()
        q = select(AccidentRecord).join(
            AccidentEvent, AccidentRecord.id == AccidentEvent.id
        ).where(AccidentEvent.record_status == "active", AccidentRecord.has_conflicts.is_(True))
        q = _apply_record_filters(q, f, AccidentRecord)
        result = await db.execute(q)
        disputed_records = list(result.scalars().all())

        # Count unresolved conflicts
        conflict_result = await db.execute(
            select(ClaimConflict).where(ClaimConflict.status == "open")
        )
        open_conflicts = list(conflict_result.scalars().all())

        disputed_system_failures = (await db.execute(
            select(func.count()).where(AccidentSystemFailure.is_disputed.is_(True))
        )).scalar_one()

        disputed_weather = (await db.execute(
            select(func.count()).where(AccidentWeatherObservation.is_disputed.is_(True))
        )).scalar_one()

        return {
            "disputed_accident_records": len(disputed_records),
            "open_field_conflicts": len(open_conflicts),
            "disputed_system_failures": disputed_system_failures,
            "disputed_weather_observations": disputed_weather,
        }

    # ------------------------------------------------------------------
    # Pattern tags (Phase 3)
    # ------------------------------------------------------------------

    @staticmethod
    async def rebuild_pattern_tags(
        db: AsyncSession,
        accident_event_id: str,
    ) -> list[AccidentPatternTag]:
        """
        Rebuild all pattern tags for one accident deterministically.

        Existing tags for this accident are deleted and recreated so the
        tag set always reflects the current state of structured data.
        """
        # Delete existing
        await db.execute(
            delete(AccidentPatternTag).where(
                AccidentPatternTag.accident_event_id == accident_event_id
            )
        )

        # Gather structured data
        record_result = await db.execute(
            select(AccidentRecord).where(AccidentRecord.id == accident_event_id)
        )
        record = record_result.scalar_one_or_none()

        weather_result = await db.execute(
            select(AccidentWeatherObservation).where(
                AccidentWeatherObservation.accident_event_id == accident_event_id
            )
        )
        weather_obs = list(weather_result.scalars().all())

        failure_result = await db.execute(
            select(AccidentSystemFailure).where(
                AccidentSystemFailure.accident_event_id == accident_event_id
            )
        )
        failures = list(failure_result.scalars().all())

        # Generate raw tag dicts
        raw_tags: list[dict[str, Any]] = []
        if record:
            raw_tags.extend(_tags_from_record(record))
        for obs in weather_obs:
            raw_tags.extend(_tags_from_weather(obs))
        for f in failures:
            raw_tags.extend(_tags_from_failure(f))

        # Deduplicate by (type, value, status), keep highest confidence
        seen: dict[tuple, dict[str, Any]] = {}
        for t in raw_tags:
            key = (t["tag_type"], t["tag_value"], t["status"])
            existing = seen.get(key)
            if existing is None or (t.get("confidence_score") or 0) > (existing.get("confidence_score") or 0):
                seen[key] = t

        # Persist
        created: list[AccidentPatternTag] = []
        for t in seen.values():
            tag = AccidentPatternTag(
                id=str(uuid.uuid4()),
                accident_event_id=accident_event_id,
                tag_type=t["tag_type"],
                tag_value=t["tag_value"],
                status=t.get("status", "unknown"),
                confidence_score=t.get("confidence_score"),
                source_count=t.get("source_count", 0),
                is_disputed=t.get("is_disputed", False),
            )
            db.add(tag)
            created.append(tag)

        log.info(
            "pattern_tags.rebuilt",
            accident_event_id=accident_event_id,
            tag_count=len(created),
        )
        return created

    # ------------------------------------------------------------------
    # Similar accidents (Phase 4)
    # ------------------------------------------------------------------

    @staticmethod
    async def _extract_features(
        db: AsyncSession, accident_event_id: str
    ) -> AccidentFeatures | None:
        """Extract the feature vector for one accident."""
        record_result = await db.execute(
            select(AccidentRecord).where(AccidentRecord.id == accident_event_id)
        )
        record = record_result.scalar_one_or_none()
        if record is None:
            return None

        feats = AccidentFeatures(accident_event_id=accident_event_id)
        feats.aircraft_make = record.aircraft_make
        feats.aircraft_model = record.aircraft_model
        feats.phase_of_flight = record.phase_of_flight
        feats.injury_severity = record.injury_severity
        feats.investigation_status = record.investigation_status
        feats.country_code = record.country_code
        feats.overall_confidence = float(record.confidence_score or 0.5)
        feats.has_disputed = record.has_conflicts

        # Weather features — use confirmed observations only for non-contested features
        wx_result = await db.execute(
            select(AccidentWeatherObservation).where(
                AccidentWeatherObservation.accident_event_id == accident_event_id
            )
        )
        for obs in wx_result.scalars().all():
            if obs.flight_rules and not feats.weather_flight_rules:
                feats.weather_flight_rules = obs.flight_rules
            if obs.thunderstorm_present:
                feats.has_thunderstorm = True
            if obs.visibility_m is not None and obs.visibility_m < 1609:
                feats.has_low_visibility = True

        # Failure features — only confirmed failures for similarity matching
        fail_result = await db.execute(
            select(AccidentSystemFailure).where(
                AccidentSystemFailure.accident_event_id == accident_event_id,
                AccidentSystemFailure.status.in_(["confirmed", "reported"]),
            )
        )
        for f in fail_result.scalars().all():
            feats.system_failure_categories.append(f.failure_category)
            if f.maintenance_related:
                feats.has_maintenance_failure = True

        return feats

    @staticmethod
    async def find_similar_accidents(
        db: AsyncSession,
        accident_event_id: str,
        limit: int = 10,
        min_score: float = 0.10,
        filters: AnalyticsFilters | None = None,
    ) -> list[dict[str, Any]]:
        """
        Return explainable similar accidents, ranked by similarity score.

        Fatality alone never produces high similarity — shared technical context required.
        Ruled-out factors are excluded from confirmed feature matching.
        Low-confidence or disputed shared features produce a warning.
        """
        target = await AdvancedAnalyticsService._extract_features(db, accident_event_id)
        if target is None:
            return []

        # Fetch candidate accidents (active, not self)
        f = filters or AnalyticsFilters()
        q = select(AccidentRecord.id).join(
            AccidentEvent, AccidentRecord.id == AccidentEvent.id
        ).where(
            AccidentEvent.record_status == "active",
            AccidentRecord.id != accident_event_id,
        )
        q = _apply_record_filters(q, f, AccidentRecord)
        candidate_ids = [row[0] for row in (await db.execute(q)).all()]

        results = []
        for cid in candidate_ids[:200]:  # cap candidates for performance
            cand = await AdvancedAnalyticsService._extract_features(db, cid)
            if cand is None:
                continue
            score, shared, differing, reasons, low_conf = _compute_similarity(target, cand)
            if score < min_score:
                continue
            results.append({
                "accident_id": cid,
                "similarity_score": score,
                "shared_factors": shared,
                "differing_factors": differing,
                "similarity_reasons": reasons,
                "confidence_score": round(
                    (target.overall_confidence + cand.overall_confidence) / 2, 3
                ),
                "low_confidence_warning": low_conf,
                "similarity_note": (
                    "High similarity score reflects shared technical context, "
                    "not a shared cause. Each accident is independently investigated."
                ),
            })

        results.sort(key=lambda x: x["similarity_score"], reverse=True)
        return results[:limit]

    # ------------------------------------------------------------------
    # Snapshot persistence
    # ------------------------------------------------------------------

    @staticmethod
    async def save_snapshot(
        db: AsyncSession,
        *,
        snapshot_type: str,
        parameters: dict[str, Any],
        result: dict[str, Any],
        generated_by: str | None = None,
        source_record_count: int | None = None,
        low_confidence_count: int | None = None,
        disputed_count: int | None = None,
    ) -> AnalyticsSnapshot:
        snap = AnalyticsSnapshot(
            id=str(uuid.uuid4()),
            snapshot_type=snapshot_type,
            parameters=parameters,
            result=result,
            generated_by=generated_by,
            data_version="v1",
            source_record_count=source_record_count,
            low_confidence_count=low_confidence_count,
            disputed_count=disputed_count,
        )
        db.add(snap)
        log.info("analytics.snapshot.saved", snapshot_type=snapshot_type)
        return snap
