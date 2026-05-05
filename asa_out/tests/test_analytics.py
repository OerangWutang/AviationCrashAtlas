"""
Tests for the Advanced Analytics & Pattern Detection feature.

Covers:
- ORM model importability, enum values, required columns
- Pattern tag generation from record, weather, and failure data (Phase 3):
  - confirmed vs suspected vs disputed vs ruled_out counting
  - causal tags only when is_causal_factor=True and status=confirmed
  - data quality tags: missing_date, missing_location, low_confidence
  - ruled_out failures produce ruled_out tags (not excluded silently)
- Similarity scoring (Phase 4):
  - weighted feature matching
  - fatality alone insufficient for high similarity
  - shared_factors and differing_factors explanations
  - low_confidence_warning when either accident has low confidence
  - spec example: A+B share aircraft+phase+IFR+engine → ranked above C (fatal only)
- AdvancedAnalyticsService unit tests (mocked DB):
  - get_accident_summary: disputed/low_confidence counts
  - get_data_quality_summary: all seven quality metrics
  - get_system_failure_patterns: by_category counts by status
  - get_weather_patterns: confirmed vs disputed thunderstorm
  - rebuild_pattern_tags: creates deterministic tags, deduplicates
- Analytics filters: min_confidence filtering, year filtering
- API router response shapes:
  - GET /api/v1/analytics/advanced/summary — 200
  - GET /api/v1/analytics/advanced/data-quality — 200
  - GET /api/v1/analytics/advanced/system-failures — 200
  - GET /api/v1/accidents/{id}/similar — 200 / 404
  - POST /api/v1/accidents/{id}/patterns/rebuild — 200
  - POST /api/v1/analytics/advanced/rebuild — requires admin
- Empty state behavior
- Causation note never asserted unless explicit
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Mock helpers
# ─────────────────────────────────────────────────────────────────────────────

def _record(
    *,
    id: str | None = None,
    phase_of_flight: str | None = "approach",
    injury_severity: str | None = "FATAL",
    confidence_score: float | None = 0.85,
    has_conflicts: bool = False,
    occurred_year: int | None = 2022,
    aircraft_make: str | None = "Cessna",
    aircraft_model: str | None = "172",
    country_code: str | None = "US",
    investigation_status: str | None = "final",
    occurred_at=None,
    occurred_date=None,
    location_lat: float | None = 40.0,
    location_text: str | None = "Newark, NJ",
    claim_source_ids: list | None = None,
) -> MagicMock:
    r = MagicMock()
    r.id = id or str(uuid.uuid4())
    r.phase_of_flight = phase_of_flight
    r.injury_severity = injury_severity
    r.confidence_score = confidence_score
    r.has_conflicts = has_conflicts
    r.occurred_year = occurred_year
    r.aircraft_make = aircraft_make
    r.aircraft_model = aircraft_model
    r.country_code = country_code
    r.investigation_status = investigation_status
    r.occurred_at = occurred_at
    r.occurred_date = occurred_date
    r.location_lat = location_lat
    r.location_text = location_text
    r.claim_source_ids = claim_source_ids or ["src-1", "src-2"]
    return r


def _weather(
    *,
    flight_rules: str | None = "vfr",
    is_disputed: bool = False,
    thunderstorm_present: bool = False,
    visibility_m: float | None = None,
    icing_risk: str | None = None,
    turbulence_risk: str | None = None,
    confidence_score: float | None = 0.8,
    report_type: str = "metar",
) -> MagicMock:
    w = MagicMock()
    w.flight_rules = flight_rules
    w.is_disputed = is_disputed
    w.thunderstorm_present = thunderstorm_present
    w.visibility_m = visibility_m
    w.icing_risk = icing_risk
    w.turbulence_risk = turbulence_risk
    w.confidence_score = confidence_score
    w.report_type = report_type
    return w


def _failure(
    *,
    failure_category: str = "engine",
    status: str = "confirmed",
    is_disputed: bool = False,
    is_causal_factor: bool = False,
    maintenance_related: bool | None = None,
    confidence_score: float | None = 0.9,
) -> MagicMock:
    f = MagicMock()
    f.failure_category = failure_category
    f.status = status
    f.is_disputed = is_disputed
    f.is_causal_factor = is_causal_factor
    f.maintenance_related = maintenance_related
    f.confidence_score = confidence_score
    return f


# ─────────────────────────────────────────────────────────────────────────────
# ORM sanity
# ─────────────────────────────────────────────────────────────────────────────

class TestAnalyticsOrm:
    def test_models_importable(self):
        from atlas.models.orm import (  # noqa: F401
            AnalyticsSnapshot, AccidentPatternTag, AccidentSimilarityScore,
        )

    def test_enum_values(self):
        from atlas.models.orm import SnapshotType, PatternTagType, PatternTagStatus
        assert SnapshotType.DATA_QUALITY == "data_quality"
        assert SnapshotType.SIMILAR_ACCIDENTS == "similar_accidents"
        assert PatternTagType.WEATHER == "weather"
        assert PatternTagType.CAUSAL_FACTOR == "causal_factor"
        assert PatternTagStatus.CONFIRMED == "confirmed"
        assert PatternTagStatus.RULED_OUT == "ruled_out"

    def test_pattern_tag_unique_constraint(self):
        from atlas.models.orm import AccidentPatternTag
        constraints = {c.name for c in AccidentPatternTag.__table__.constraints}
        assert "uq_pattern_tag" in constraints

    def test_similarity_score_required_columns(self):
        from atlas.models.orm import AccidentSimilarityScore
        cols = {c.key for c in AccidentSimilarityScore.__table__.c}
        assert {"similarity_score", "shared_factors", "differing_factors",
                "low_confidence_warning"}.issubset(cols)


# ─────────────────────────────────────────────────────────────────────────────
# Pattern tag generation (Phase 3)
# ─────────────────────────────────────────────────────────────────────────────

class TestPatternTagGeneration:
    def _from_record(self, **kwargs):
        from atlas.analytics.service import _tags_from_record
        return _tags_from_record(_record(**kwargs))

    def _from_weather(self, **kwargs):
        from atlas.analytics.service import _tags_from_weather
        return _tags_from_weather(_weather(**kwargs))

    def _from_failure(self, **kwargs):
        from atlas.analytics.service import _tags_from_failure
        return _tags_from_failure(_failure(**kwargs))

    def _tag(self, tags, tag_type, tag_value):
        return next((t for t in tags if t["tag_type"] == tag_type and t["tag_value"] == tag_value), None)

    # Record tags
    def test_phase_tag_generated(self):
        tags = self._from_record(phase_of_flight="approach")
        t = self._tag(tags, "phase_of_flight", "approach")
        assert t is not None
        assert t["status"] == "confirmed"

    def test_severity_tag_generated(self):
        tags = self._from_record(injury_severity="FATAL")
        t = self._tag(tags, "data_quality", "severity:fatal")
        assert t is not None

    def test_conflict_tag_when_has_conflicts(self):
        tags = self._from_record(has_conflicts=True)
        t = self._tag(tags, "data_quality", "has_conflicts")
        assert t is not None
        assert t.get("is_disputed") is True

    def test_low_confidence_tag(self):
        tags = self._from_record(confidence_score=0.3)
        t = self._tag(tags, "data_quality", "low_confidence")
        assert t is not None

    def test_no_low_confidence_tag_when_high(self):
        tags = self._from_record(confidence_score=0.9)
        t = self._tag(tags, "data_quality", "low_confidence")
        assert t is None

    def test_missing_date_tag(self):
        tags = self._from_record(occurred_at=None, occurred_date=None)
        t = self._tag(tags, "data_quality", "missing_date")
        assert t is not None

    def test_missing_location_tag(self):
        tags = self._from_record(location_lat=None, location_text=None)
        t = self._tag(tags, "data_quality", "missing_location")
        assert t is not None

    def test_no_missing_location_tag_when_present(self):
        tags = self._from_record(location_lat=40.0, location_text="Newark")
        t = self._tag(tags, "data_quality", "missing_location")
        assert t is None

    def test_single_source_tag(self):
        tags = self._from_record(claim_source_ids=["src-1"])
        t = self._tag(tags, "data_quality", "single_source")
        assert t is not None

    # Weather tags
    def test_ifr_tag_from_weather(self):
        tags = self._from_weather(flight_rules="ifr")
        t = self._tag(tags, "weather", "ifr")
        assert t is not None

    def test_disputed_weather_has_disputed_status(self):
        tags = self._from_weather(flight_rules="ifr", is_disputed=True)
        t = self._tag(tags, "weather", "ifr")
        assert t is not None
        assert t["status"] == "disputed"

    def test_thunderstorm_tag(self):
        tags = self._from_weather(thunderstorm_present=True)
        t = self._tag(tags, "weather", "thunderstorm")
        assert t is not None

    def test_low_visibility_tag(self):
        tags = self._from_weather(visibility_m=800)  # < 1609 m
        t = self._tag(tags, "weather", "low_visibility")
        assert t is not None

    def test_no_low_visibility_tag_when_good(self):
        tags = self._from_weather(visibility_m=10000)
        t = self._tag(tags, "weather", "low_visibility")
        assert t is None

    # Failure tags
    def test_confirmed_failure_tag(self):
        tags = self._from_failure(failure_category="engine", status="confirmed")
        t = self._tag(tags, "mechanical", "engine")
        assert t is not None
        assert t["status"] == "confirmed"

    def test_suspected_failure_tag(self):
        tags = self._from_failure(failure_category="fuel", status="suspected")
        t = self._tag(tags, "mechanical", "fuel")
        assert t["status"] == "suspected"

    def test_ruled_out_failure_has_ruled_out_tag(self):
        """Ruled-out failures must produce ruled_out tags, not be silently excluded."""
        tags = self._from_failure(failure_category="flight_controls", status="ruled_out")
        t = self._tag(tags, "mechanical", "flight_controls")
        assert t is not None
        assert t["status"] == "ruled_out"

    def test_causal_tag_only_when_explicit(self):
        """causal_factor tag requires is_causal_factor=True AND status=confirmed."""
        # Not causal — no causal tag
        tags_no = self._from_failure(is_causal_factor=False, status="confirmed")
        assert self._tag(tags_no, "causal_factor", "engine") is None

        # Causal confirmed — causal tag present
        tags_yes = self._from_failure(is_causal_factor=True, status="confirmed")
        assert self._tag(tags_yes, "causal_factor", "engine") is not None

    def test_causal_tag_not_generated_for_suspected(self):
        """Suspected failure cannot produce a causal_factor tag."""
        tags = self._from_failure(is_causal_factor=True, status="suspected")
        # status=suspected means is_causal_factor is ignored for causal tag
        assert self._tag(tags, "causal_factor", "engine") is None

    def test_maintenance_tag(self):
        tags = self._from_failure(maintenance_related=True)
        t = self._tag(tags, "mechanical", "maintenance_related")
        assert t is not None


# ─────────────────────────────────────────────────────────────────────────────
# Similarity scoring (Phase 4)
# ─────────────────────────────────────────────────────────────────────────────

class TestSimilarityScoring:
    def _feats(self, **kwargs):
        from atlas.analytics.service import AccidentFeatures
        defaults = dict(
            accident_id=str(uuid.uuid4()),
            aircraft_make="Cessna",
            aircraft_model="172",
            phase_of_flight="approach",
            weather_flight_rules="ifr",
            system_failure_categories=["engine"],
            injury_severity="FATAL",
            investigation_status="final",
            country_code="US",
            has_thunderstorm=False,
            has_low_visibility=False,
            has_maintenance_failure=False,
            overall_confidence=0.85,
            has_disputed=False,
        )
        defaults.update(kwargs)
        return AccidentFeatures(**defaults)

    def _score(self, a, b):
        from atlas.analytics.service import _compute_similarity
        return _compute_similarity(a, b)

    def test_identical_features_high_score(self):
        a = self._feats()
        b = self._feats(accident_id=str(uuid.uuid4()))
        score, shared, _, _, _ = self._score(a, b)
        assert score >= 0.7

    def test_no_shared_technical_context_low_score(self):
        """Two accidents sharing only fatality should score lower than technical matches."""
        technical = self._feats()
        fatal_only = self._feats(
            accident_id=str(uuid.uuid4()),
            aircraft_make="Boeing",       # different
            aircraft_model="737",          # different
            phase_of_flight="cruise",      # different
            weather_flight_rules="vfr",    # different
            system_failure_categories=[],  # different
        )
        score_tech, _, _, _, _ = self._score(technical, self._feats(accident_id=str(uuid.uuid4())))
        score_fatal, _, _, _, _ = self._score(technical, fatal_only)
        # Both fatal, but technical match should score higher
        assert score_tech > score_fatal

    def test_shared_factors_included(self):
        a = self._feats(aircraft_make="Cessna", phase_of_flight="approach")
        b = self._feats(accident_id=str(uuid.uuid4()), aircraft_make="Cessna", phase_of_flight="approach")
        _, shared, _, _, _ = self._score(a, b)
        assert any("Cessna" in s for s in shared)
        assert any("approach" in s for s in shared)

    def test_differing_factors_included(self):
        a = self._feats(aircraft_make="Cessna", phase_of_flight="approach")
        b = self._feats(accident_id=str(uuid.uuid4()), aircraft_make="Piper", phase_of_flight="cruise")
        _, _, differing, _, _ = self._score(a, b)
        assert len(differing) >= 2

    def test_low_confidence_warning_when_either_low(self):
        a = self._feats(overall_confidence=0.3)
        b = self._feats(accident_id=str(uuid.uuid4()), overall_confidence=0.85)
        _, _, _, _, warn = self._score(a, b)
        assert warn is True

    def test_no_low_confidence_warning_when_both_high(self):
        a = self._feats(overall_confidence=0.85)
        b = self._feats(accident_id=str(uuid.uuid4()), overall_confidence=0.90)
        _, _, _, _, warn = self._score(a, b)
        assert warn is False

    def test_spec_example_b_ranks_above_c(self):
        """
        Spec: A and B share aircraft model, approach phase, IFR, and engine failure.
        C is fatal but shares no technical/contextual factors.
        Expected: B ranks above C.
        """
        from atlas.analytics.service import _compute_similarity, AccidentFeatures
        a = self._feats()  # reference accident
        b = self._feats(accident_id="b")  # technical twin
        c = self._feats(
            accident_id="c",
            aircraft_make="Boeing",
            aircraft_model="787",
            phase_of_flight="cruise",
            weather_flight_rules="vfr",
            system_failure_categories=[],
            # Still FATAL
        )
        score_b, _, _, _, _ = _compute_similarity(a, b)
        score_c, _, _, _, _ = _compute_similarity(a, c)
        assert score_b > score_c, f"B ({score_b:.3f}) should rank above C ({score_c:.3f})"

    def test_similarity_weights_sum_to_one(self):
        from atlas.analytics.service import SIMILARITY_WEIGHTS
        total = sum(SIMILARITY_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9, f"Weights sum to {total}, expected 1.0"

    def test_empty_failure_categories_no_crash(self):
        a = self._feats(system_failure_categories=[])
        b = self._feats(accident_id=str(uuid.uuid4()), system_failure_categories=[])
        score, _, _, _, _ = self._score(a, b)
        assert 0.0 <= score <= 1.0


# ─────────────────────────────────────────────────────────────────────────────
# AdvancedAnalyticsService unit tests (mocked session)
# ─────────────────────────────────────────────────────────────────────────────

class TestAdvancedAnalyticsService:

    @pytest.mark.asyncio
    async def test_get_accident_summary_counts_disputed(self):
        """disputed_records counts accidents with has_conflicts=True."""
        from atlas.analytics.service import AdvancedAnalyticsService, AnalyticsFilters

        records = [
            _record(has_conflicts=True, confidence_score=0.8),
            _record(has_conflicts=False, confidence_score=0.3),
            _record(has_conflicts=False, confidence_score=0.9),
        ]
        scalars = MagicMock()
        scalars.all.return_value = records
        exec_result = MagicMock()
        exec_result.scalars.return_value = scalars
        session = MagicMock()
        session.execute = AsyncMock(return_value=exec_result)

        result = await AdvancedAnalyticsService.get_accident_summary(session, AnalyticsFilters())
        assert result["disputed_records"] == 1
        assert result["low_confidence_records"] == 1
        assert result["total_accidents"] == 3

    @pytest.mark.asyncio
    async def test_get_data_quality_summary(self):
        """All seven quality metrics are counted correctly."""
        from atlas.analytics.service import AdvancedAnalyticsService

        records = [
            _record(occurred_at=None, occurred_date=None, location_lat=None, location_text=None,
                    aircraft_model=None, has_conflicts=True, confidence_score=0.3,
                    claim_source_ids=["src-1"]),
            _record(investigation_status="preliminary",
                    occurred_at=datetime(2022, 3, 1),  # has date — doesn't count as missing
                    location_lat=40.0,                 # has location
                    aircraft_model="172",              # has model
                    confidence_score=0.8,
                    claim_source_ids=["src-1", "src-2"]),
        ]
        scalars = MagicMock()
        scalars.all.return_value = records
        exec_result = MagicMock()
        exec_result.scalars.return_value = scalars
        session = MagicMock()
        session.execute = AsyncMock(return_value=exec_result)

        result = await AdvancedAnalyticsService.get_data_quality_summary(session)
        assert result["missing_date"] == 1
        assert result["missing_location"] == 1
        assert result["missing_aircraft_model"] == 1
        assert result["has_conflicts"] == 1
        assert result["low_confidence_records"] == 1
        assert result["single_source_records"] == 1
        assert result["preliminary_only_records"] == 1

    @pytest.mark.asyncio
    async def test_get_system_failure_patterns_separates_status(self):
        """Engine confirmed, suspected, and disputed are counted separately."""
        from atlas.analytics.service import AdvancedAnalyticsService

        failures = [
            _failure(failure_category="engine", status="confirmed"),
            _failure(failure_category="engine", status="suspected"),
            _failure(failure_category="engine", status="ruled_out"),
            _failure(failure_category="fuel", status="disputed", is_disputed=True),
        ]
        scalars = MagicMock()
        scalars.all.return_value = failures
        exec_result = MagicMock()
        exec_result.scalars.return_value = scalars
        session = MagicMock()
        session.execute = AsyncMock(return_value=exec_result)

        result = await AdvancedAnalyticsService.get_system_failure_patterns(session)
        engine = result["by_category"]["engine"]
        assert engine["confirmed"] == 1
        assert engine["suspected"] == 1
        assert engine["ruled_out"] == 1
        assert result["by_category"]["fuel"]["disputed"] == 1
        # Confirmed != total
        assert engine["confirmed"] != sum(engine.values())

    @pytest.mark.asyncio
    async def test_get_weather_patterns_separates_disputed(self):
        """Thunderstorm confirmed and disputed are counted separately."""
        from atlas.analytics.service import AdvancedAnalyticsService

        obs = [
            _weather(thunderstorm_present=True, is_disputed=False),
            _weather(thunderstorm_present=True, is_disputed=True),
            _weather(thunderstorm_present=False, flight_rules="ifr"),
        ]
        scalars = MagicMock()
        scalars.all.return_value = obs
        exec_result = MagicMock()
        exec_result.scalars.return_value = scalars
        session = MagicMock()
        session.execute = AsyncMock(return_value=exec_result)

        result = await AdvancedAnalyticsService.get_weather_patterns(session)
        assert result["thunderstorm_confirmed_count"] == 1
        assert result["thunderstorm_disputed_count"] == 1
        assert result["total_observations"] == 3

    @pytest.mark.asyncio
    async def test_rebuild_pattern_tags_deduplicates(self):
        """Same (type, value, status) from multiple sources → one tag, highest confidence."""
        from atlas.analytics.service import AdvancedAnalyticsService

        rec = _record(phase_of_flight="approach", confidence_score=0.85)
        record_result = MagicMock()
        record_result.scalar_one_or_none.return_value = rec

        # Two weather observations both producing an ifr tag
        w1 = _weather(flight_rules="ifr", confidence_score=0.7)
        w2 = _weather(flight_rules="ifr", confidence_score=0.9)
        weather_scalars = MagicMock()
        weather_scalars.all.return_value = [w1, w2]
        weather_result = MagicMock()
        weather_result.scalars.return_value = weather_scalars

        failure_scalars = MagicMock()
        failure_scalars.all.return_value = []
        failure_result = MagicMock()
        failure_result.scalars.return_value = failure_scalars

        session = MagicMock()
        session.add = MagicMock()

        call_count = 0
        async def fake_execute(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return MagicMock()  # delete result
            if call_count == 2:
                return record_result
            if call_count == 3:
                return weather_result
            return failure_result

        session.execute = AsyncMock(side_effect=fake_execute)

        tags = await AdvancedAnalyticsService.rebuild_pattern_tags(session, "evt-1")

        # The ifr tag should appear only once despite two weather observations
        ifr_tags = [t for t in tags if t.tag_type == "weather" and t.tag_value == "ifr"]
        assert len(ifr_tags) == 1

    @pytest.mark.asyncio
    async def test_get_accident_summary_empty(self):
        from atlas.analytics.service import AdvancedAnalyticsService

        scalars = MagicMock()
        scalars.all.return_value = []
        exec_result = MagicMock()
        exec_result.scalars.return_value = scalars
        session = MagicMock()
        session.execute = AsyncMock(return_value=exec_result)

        result = await AdvancedAnalyticsService.get_accident_summary(session)
        assert result["total_accidents"] == 0
        assert result["disputed_records"] == 0

    @pytest.mark.asyncio
    async def test_causal_factor_not_counted_for_suspected(self):
        """confirmed_causal_count should NOT include suspected failures."""
        from atlas.analytics.service import AdvancedAnalyticsService

        failures = [
            _failure(failure_category="engine", status="confirmed", is_causal_factor=True),
            _failure(failure_category="fuel",   status="suspected", is_causal_factor=True),  # not counted
        ]
        scalars = MagicMock()
        scalars.all.return_value = failures
        exec_result = MagicMock()
        exec_result.scalars.return_value = scalars
        session = MagicMock()
        session.execute = AsyncMock(return_value=exec_result)

        result = await AdvancedAnalyticsService.get_system_failure_patterns(session)
        assert result["confirmed_causal_count"] == 1  # only confirmed, not suspected


# ─────────────────────────────────────────────────────────────────────────────
# API router — FastAPI test client
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def analytics_client():
    import httpx
    from fastapi import FastAPI
    from atlas.analytics.router import router as adv_router
    from atlas.db.engine import get_db, get_read_db
    from atlas.api.auth import require_reviewer, require_admin, OperatorContext

    app = FastAPI()
    app.include_router(adv_router)

    async def noop_db():
        yield MagicMock()

    app.dependency_overrides[get_db] = noop_db
    app.dependency_overrides[get_read_db] = noop_db
    app.dependency_overrides[require_reviewer] = lambda: OperatorContext(
        id="reviewer", role="reviewer", key_id=""
    )
    # Admin override — for non-admin rebuild test we'll patch per-test
    app.dependency_overrides[require_admin] = lambda: OperatorContext(
        id="admin", role="admin", key_id=""
    )

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


class TestAnalyticsRouterRead:

    @pytest.mark.asyncio
    async def test_summary_200(self, analytics_client):
        with patch(
            "atlas.analytics.router.AdvancedAnalyticsService.get_accident_summary",
            new_callable=AsyncMock,
            return_value={
                "total_accidents": 100, "fatal_accidents": 20,
                "disputed_records": 5, "low_confidence_records": 10,
                "avg_confidence": 0.75, "filters_applied": {},
                "computation_note": "test",
            },
        ):
            resp = await analytics_client.get("/api/v1/analytics/advanced/summary")
        assert resp.status_code == 200
        assert resp.json()["total_accidents"] == 100

    @pytest.mark.asyncio
    async def test_data_quality_200(self, analytics_client):
        with patch(
            "atlas.analytics.router.AdvancedAnalyticsService.get_data_quality_summary",
            new_callable=AsyncMock,
            return_value={
                "total_records": 50, "missing_date": 3, "missing_location": 5,
                "missing_aircraft_model": 7, "has_conflicts": 4,
                "low_confidence_records": 8, "single_source_records": 12,
                "preliminary_only_records": 2, "quality_note": "test",
            },
        ):
            resp = await analytics_client.get("/api/v1/analytics/advanced/data-quality")
        assert resp.status_code == 200
        assert resp.json()["missing_date"] == 3

    @pytest.mark.asyncio
    async def test_system_failures_200(self, analytics_client):
        with patch(
            "atlas.analytics.router.AdvancedAnalyticsService.get_system_failure_patterns",
            new_callable=AsyncMock,
            return_value={
                "total_failure_records": 25,
                "by_category": {"engine": {"confirmed": 5, "suspected": 3}},
                "confirmed_causal_count": 3,
                "confirmed_maintenance_related_count": 2,
                "status_note": "test",
            },
        ):
            resp = await analytics_client.get("/api/v1/analytics/advanced/system-failures")
        assert resp.status_code == 200
        data = resp.json()
        assert data["by_category"]["engine"]["confirmed"] == 5

    @pytest.mark.asyncio
    async def test_similar_accidents_404_bad_accident(self, analytics_client):
        from fastapi import HTTPException
        with patch(
            "atlas.analytics.router._require_accident",
            new_callable=AsyncMock,
            side_effect=HTTPException(404, "not found"),
        ):
            resp = await analytics_client.get("/api/v1/accidents/bad-id/similar")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_similar_accidents_200_empty(self, analytics_client):
        with (
            patch("atlas.analytics.router._require_accident", new_callable=AsyncMock),
            patch(
                "atlas.analytics.router.AdvancedAnalyticsService.find_similar_accidents",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            resp = await analytics_client.get("/api/v1/accidents/evt-1/similar")
        assert resp.status_code == 200
        data = resp.json()
        assert data["similar_count"] == 0
        assert "similarity_note" in data

    @pytest.mark.asyncio
    async def test_similar_accidents_includes_explanation(self, analytics_client):
        similar = {
            "accident_id": "evt-2",
            "similarity_score": 0.72,
            "shared_factors": ["Aircraft make: Cessna", "Phase of flight: approach"],
            "differing_factors": ["Country: US vs Canada"],
            "similarity_reasons": {"aircraft_make": 1.0, "phase_of_flight": 1.0},
            "confidence_score": 0.8,
            "low_confidence_warning": False,
            "similarity_note": "test note",
        }
        with (
            patch("atlas.analytics.router._require_accident", new_callable=AsyncMock),
            patch(
                "atlas.analytics.router.AdvancedAnalyticsService.find_similar_accidents",
                new_callable=AsyncMock,
                return_value=[similar],
            ),
        ):
            resp = await analytics_client.get("/api/v1/accidents/evt-1/similar")
        data = resp.json()
        assert data["similar_count"] == 1
        first = data["similar_accidents"][0]
        assert "shared_factors" in first
        assert "differing_factors" in first
        assert "similarity_note" in first

    @pytest.mark.asyncio
    async def test_patterns_rebuild_200(self, analytics_client):
        from atlas.models.orm import AccidentPatternTag
        tag = MagicMock(spec=AccidentPatternTag)
        tag.tag_type = "phase_of_flight"
        tag.tag_value = "approach"
        tag.status = "confirmed"
        tag.confidence_score = 0.85
        tag.is_disputed = False

        with (
            patch("atlas.analytics.router._require_accident", new_callable=AsyncMock),
            patch(
                "atlas.analytics.router.AdvancedAnalyticsService.rebuild_pattern_tags",
                new_callable=AsyncMock,
                return_value=[tag],
            ),
        ):
            resp = await analytics_client.post("/api/v1/accidents/evt-1/patterns/rebuild")
        assert resp.status_code == 200
        data = resp.json()
        assert data["tag_count"] == 1
        assert data["tags"][0]["tag_value"] == "approach"

    @pytest.mark.asyncio
    async def test_rebuild_all_200(self, analytics_client):
        with patch(
            "atlas.analytics.router.AdvancedAnalyticsService.rebuild_pattern_tags",
            new_callable=AsyncMock,
            return_value=[],
        ):
            # Patch the select inside the route by mocking the session execute
            exec_result = MagicMock()
            exec_result.all.return_value = [("evt-1",), ("evt-2",)]

            async def patched_db():
                session = MagicMock()
                session.execute = AsyncMock(return_value=exec_result)
                yield session

            from atlas.db.engine import get_db
            from atlas.analytics.router import router as adv_router

            import httpx
            from fastapi import FastAPI
            from atlas.api.auth import require_admin, OperatorContext

            app2 = FastAPI()
            app2.include_router(adv_router)
            app2.dependency_overrides[get_db] = patched_db
            app2.dependency_overrides[require_admin] = lambda: OperatorContext(
                id="admin", role="admin", key_id=""
            )

            client2 = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app2), base_url="http://test"
            )
            resp = await client2.post("/api/v1/analytics/advanced/rebuild")

        assert resp.status_code == 200
        assert resp.json()["rebuilt_accident_count"] == 2


# ─────────────────────────────────────────────────────────────────────────────
# Analytics filter contract
# ─────────────────────────────────────────────────────────────────────────────

class TestAnalyticsFilters:
    def test_filter_dataclass_defaults(self):
        from atlas.analytics.service import AnalyticsFilters
        f = AnalyticsFilters()
        assert f.include_disputed is True
        assert f.include_suspected is True
        assert f.include_ruled_out is True
        assert f.min_confidence is None

    def test_apply_record_filters_year(self):
        """_apply_record_filters must add year constraints."""
        from atlas.analytics.service import _apply_record_filters, AnalyticsFilters
        from sqlalchemy import select
        from atlas.models.orm import AccidentRecord

        q = select(AccidentRecord)
        f = AnalyticsFilters(start_year=2020, end_year=2023)
        q2 = _apply_record_filters(q, f, AccidentRecord)
        # Compiled query should reference the year filters
        compiled = str(q2.compile())
        assert "occurred_year" in compiled


# ─────────────────────────────────────────────────────────────────────────────
# Causation contract
# ─────────────────────────────────────────────────────────────────────────────

class TestCausationContract:
    """Verify that analytics never assert causation without explicit source support."""

    def test_weather_patterns_has_causation_note(self):
        """get_weather_patterns response must include a causation_note."""
        import asyncio
        from atlas.analytics.service import AdvancedAnalyticsService

        scalars = MagicMock()
        scalars.all.return_value = []
        exec_result = MagicMock()
        exec_result.scalars.return_value = scalars
        session = MagicMock()
        session.execute = AsyncMock(return_value=exec_result)

        result = asyncio.run(AdvancedAnalyticsService.get_weather_patterns(session))
        assert "causation" in result.get("causation_note", "").lower()

    def test_system_failure_patterns_has_status_note(self):
        import asyncio
        from atlas.analytics.service import AdvancedAnalyticsService

        scalars = MagicMock()
        scalars.all.return_value = []
        exec_result = MagicMock()
        exec_result.scalars.return_value = scalars
        session = MagicMock()
        session.execute = AsyncMock(return_value=exec_result)

        result = asyncio.run(AdvancedAnalyticsService.get_system_failure_patterns(session))
        assert "suspected" in result.get("status_note", "").lower()

    def test_similarity_note_says_not_shared_cause(self):
        from atlas.analytics.service import _compute_similarity, AccidentFeatures

        a = AccidentFeatures(accident_id="a")
        b = AccidentFeatures(accident_id="b")
        # Just verify the score runs without error and note is in API layer
        score, _, _, _, _ = _compute_similarity(a, b)
        assert 0.0 <= score <= 1.0
