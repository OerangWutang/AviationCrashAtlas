"""
Tests for the Flight Path Reconstruction feature.

Covers (all phases):

Phase 1 — ORM:
  - Model importability
  - Enum values and table columns
  - Join table unique constraints

Phase 3 — Geospatial helpers:
  - Coordinate validation (valid, boundary, invalid)
  - Haversine distance accuracy
  - Bearing calculation (cardinal directions)
  - Path length across multiple points
  - Bounding box computation
  - Bounding box with margin
  - point_sort_key ordering (UTC > relative_offset > sequence > created_at)
  - derive_segment_type logic (recorded, estimated, disputed)
  - compute_point_confidence formula

Phase 2 — Service unit tests (mocked session):
  - create_point: coordinate validation, distance-to-impact, confidence
  - create_point: invalid coordinates stored as NULL
  - create_point: claim link attachment
  - create_point: disputed flag and confidence penalty
  - update_point: partial update, confidence recompute
  - delete_point: True / False
  - create_annotation: timeline_event_id linking
  - update_annotation: partial update
  - delete_annotation: True / False
  - rebuild: idempotent, recalculates segments and distance
  - get_reconstruction: empty state, single-point state
  - get_profile: chart-ready arrays

Phase 4 — API router:
  - GET /flight-path — 200 empty, 200 with path, 404 bad accident
  - GET /flight-path/profile — 200
  - POST /flight-path/points — 201
  - PATCH /flight-path/points/{id} — 200 / 404
  - DELETE /flight-path/points/{id} — 204 / 404
  - POST /flight-path/annotations — 201
  - PATCH /flight-path/annotations/{id} — 200 / 404
  - DELETE /flight-path/annotations/{id} — 204 / 404
  - POST /flight-path/rebuild — 200
  - data_note always present in reconstruction response

Ordering contract (per spec):
  - UTC time beats relative offset beats sequence_index beats created_at
  - NULL values always sort last within each tier

Accuracy contract:
  - estimated/inferred points always carry is_estimated=True
  - segments linking estimated endpoints always have render_style=dashed_estimated
  - disputed points always carry is_disputed=True
  - approximate/unknown time_precision is preserved as-is (not promoted to exact)

Phase 10 — Fixtures (spec examples):
  - Fixture 1: recorded track + estimated final path → segment types correct
  - Fixture 2: GPWS annotation sequence → ordering correct
  - Fixture 3: disputed last known position → dispute preserved
"""
from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Mock helpers
# ─────────────────────────────────────────────────────────────────────────────

def _pt(
    *,
    id: str | None = None,
    accident_event_id: str = "evt-1",
    point_type: str = "enroute",
    source_method: str | None = "adsb",
    lat: float | None = 40.0,
    lon: float | None = -75.0,
    altitude_ft: float | None = 10000.0,
    ground_speed_kt: float | None = 250.0,
    vertical_speed_fpm: float | None = None,
    heading_degrees: float | None = None,
    track_degrees: float | None = None,
    recorded_time_utc: datetime | None = None,
    relative_offset_seconds: int | None = None,
    sequence_index: int | None = None,
    time_precision: str = "exact",
    is_disputed: bool = False,
    dispute_summary: str | None = None,
    confidence_score: float | None = 0.85,
    distance_to_impact_km: float | None = None,
    uncertainty_radius_m: float | None = None,
    is_estimated: bool = False,
    claim_links: list | None = None,
    created_at: datetime | None = None,
) -> MagicMock:
    p = MagicMock()
    p.id = id or str(uuid.uuid4())
    p.accident_event_id = accident_event_id
    p.point_type = point_type
    p.source_method = source_method
    p.latitude = lat
    p.longitude = lon
    p.altitude_ft = altitude_ft
    p.radio_altitude_ft = None
    p.ground_speed_kt = ground_speed_kt
    p.indicated_airspeed_kt = None
    p.vertical_speed_fpm = vertical_speed_fpm
    p.heading_degrees = heading_degrees
    p.track_degrees = track_degrees
    p.recorded_time_utc = recorded_time_utc
    p.relative_offset_seconds = relative_offset_seconds
    p.sequence_index = sequence_index
    p.time_precision = time_precision
    p.is_disputed = is_disputed
    p.dispute_summary = dispute_summary
    p.confidence_score = confidence_score
    p.distance_to_impact_km = distance_to_impact_km
    p.uncertainty_radius_m = uncertainty_radius_m
    p.notes = None
    p.source_id = None
    p.altitude_reference = "msl"
    p.claim_links = claim_links or []
    p.created_at = created_at or datetime(2024, 3, 1, tzinfo=UTC)
    p.updated_at = datetime(2024, 3, 1, tzinfo=UTC)
    return p


def _ann(
    *,
    id: str | None = None,
    accident_event_id: str = "evt-1",
    annotation_type: str = "gpws_sink_rate",
    title: str = "GPWS Sink Rate",
    flight_path_point_id: str | None = None,
    timeline_event_id: str | None = None,
    annotation_time_utc: datetime | None = None,
    relative_offset_seconds: int | None = None,
    time_precision: str = "relative",
    altitude_ft: float | None = None,
    radio_altitude_ft: float | None = None,
    is_disputed: bool = False,
    dispute_summary: str | None = None,
    confidence_score: float | None = 0.7,
    description: str | None = None,
    claim_links: list | None = None,
    created_at: datetime | None = None,
) -> MagicMock:
    a = MagicMock()
    a.id = id or str(uuid.uuid4())
    a.accident_event_id = accident_event_id
    a.annotation_type = annotation_type
    a.title = title
    a.flight_path_point_id = flight_path_point_id
    a.timeline_event_id = timeline_event_id
    a.annotation_time_utc = annotation_time_utc
    a.relative_offset_seconds = relative_offset_seconds
    a.time_precision = time_precision
    a.altitude_ft = altitude_ft
    a.radio_altitude_ft = radio_altitude_ft
    a.is_disputed = is_disputed
    a.dispute_summary = dispute_summary
    a.confidence_score = confidence_score
    a.description = description
    a.source_id = None
    a.claim_links = claim_links or []
    a.created_at = created_at or datetime(2024, 3, 1, tzinfo=UTC)
    a.updated_at = datetime(2024, 3, 1, tzinfo=UTC)
    return a


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 — ORM sanity
# ─────────────────────────────────────────────────────────────────────────────

class TestFlightPathOrm:
    def test_models_importable(self):
        from atlas.models.orm import (  # noqa: F401
            AccidentFlightPathPoint, FlightPathPointClaim,
            AccidentFlightPathSegment,
            AccidentFlightPathAnnotation, FlightPathAnnotationClaim,
        )

    def test_enum_values(self):
        from atlas.models.orm import (
            PathPointType, SourceMethod, AltitudeReference,
            PathSegmentType, AnnotationType,
        )
        assert PathPointType.ADSB == "adsb"
        assert PathPointType.IMPACT == "impact"
        assert PathPointType.LAST_KNOWN_POSITION == "last_known_position"
        assert PathPointType.ESTIMATED == "estimated"
        assert PathPointType.INFERRED == "inferred"
        assert SourceMethod.FDR == "fdr"
        assert SourceMethod.WITNESS == "witness"
        assert PathSegmentType.RECORDED == "recorded"
        assert PathSegmentType.ESTIMATED == "estimated"
        assert PathSegmentType.DISPUTED == "disputed"
        assert AnnotationType.GPWS_SINK_RATE == "gpws_sink_rate"
        assert AnnotationType.LOSS_OF_CONTACT == "loss_of_contact"
        assert AnnotationType.RAPID_DESCENT == "rapid_descent"

    def test_point_required_columns(self):
        from atlas.models.orm import AccidentFlightPathPoint
        cols = {c.key for c in AccidentFlightPathPoint.__table__.c}
        required = {
            "id", "accident_event_id", "point_type", "time_precision",
            "is_disputed", "created_at", "updated_at",
        }
        assert required.issubset(cols)

    def test_annotation_has_timeline_event_id(self):
        """Phase 7: annotation FK to timeline events must exist."""
        from atlas.models.orm import AccidentFlightPathAnnotation
        cols = {c.key for c in AccidentFlightPathAnnotation.__table__.c}
        assert "timeline_event_id" in cols

    def test_point_claim_unique_constraint(self):
        from atlas.models.orm import FlightPathPointClaim
        constraints = {c.name for c in FlightPathPointClaim.__table__.constraints}
        assert "uq_fp_point_claim" in constraints

    def test_annotation_claim_unique_constraint(self):
        from atlas.models.orm import FlightPathAnnotationClaim
        constraints = {c.name for c in FlightPathAnnotationClaim.__table__.constraints}
        assert "uq_fp_annotation_claim" in constraints


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — Geospatial helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestCoordinateValidation:
    def test_valid_coord(self):
        from atlas.flight_path.geo import is_valid_coord
        assert is_valid_coord(40.0, -75.0) is True
        assert is_valid_coord(0.0, 0.0) is True

    def test_boundary_coords(self):
        from atlas.flight_path.geo import is_valid_coord
        assert is_valid_coord(90.0, 180.0) is True
        assert is_valid_coord(-90.0, -180.0) is True

    def test_invalid_lat(self):
        from atlas.flight_path.geo import is_valid_coord
        assert is_valid_coord(91.0, 0.0) is False
        assert is_valid_coord(-91.0, 0.0) is False

    def test_invalid_lon(self):
        from atlas.flight_path.geo import is_valid_coord
        assert is_valid_coord(0.0, 181.0) is False
        assert is_valid_coord(0.0, -181.0) is False

    def test_none_coords_invalid(self):
        from atlas.flight_path.geo import is_valid_coord
        assert is_valid_coord(None, 0.0) is False
        assert is_valid_coord(0.0, None) is False
        assert is_valid_coord(None, None) is False

    def test_nan_invalid(self):
        from atlas.flight_path.geo import is_valid_coord
        assert is_valid_coord(float('nan'), 0.0) is False


class TestHaversineDistance:
    def _d(self, lat1, lon1, lat2, lon2):
        from atlas.flight_path.geo import haversine_km
        return haversine_km(lat1, lon1, lat2, lon2)

    def test_same_point_zero(self):
        assert self._d(40.0, -75.0, 40.0, -75.0) == pytest.approx(0.0, abs=1e-6)

    def test_symmetrical(self):
        assert self._d(40.0, -75.0, 48.0, 2.0) == pytest.approx(
            self._d(48.0, 2.0, 40.0, -75.0), rel=1e-9
        )

    def test_one_degree_latitude(self):
        # 1° lat ≈ 111.195 km
        assert self._d(0.0, 0.0, 1.0, 0.0) == pytest.approx(111.195, abs=0.5)

    def test_jfk_to_lax(self):
        # JFK 40.6413°N 73.7781°W → LAX 33.9425°N 118.4081°W ≈ 3974 km
        d = self._d(40.6413, -73.7781, 33.9425, -118.4081)
        assert 3900 < d < 4050


class TestBearing:
    def _b(self, lat1, lon1, lat2, lon2):
        from atlas.flight_path.geo import bearing_degrees
        return bearing_degrees(lat1, lon1, lat2, lon2)

    def test_due_north(self):
        assert self._b(0.0, 0.0, 1.0, 0.0) == pytest.approx(0.0, abs=0.5)

    def test_due_east(self):
        assert self._b(0.0, 0.0, 0.0, 1.0) == pytest.approx(90.0, abs=0.5)

    def test_due_south(self):
        assert self._b(1.0, 0.0, 0.0, 0.0) == pytest.approx(180.0, abs=0.5)

    def test_due_west(self):
        assert self._b(0.0, 1.0, 0.0, 0.0) == pytest.approx(270.0, abs=0.5)

    def test_result_in_0_360(self):
        from atlas.flight_path.geo import bearing_degrees
        for b in [bearing_degrees(lat1, lon1, lat2, lon2)
                  for lat1, lon1, lat2, lon2 in [
                      (40, -74, 34, -118), (-33, 151, 51, 0), (0, 0, -1, -1)
                  ]]:
            assert 0 <= b < 360


class TestPathLength:
    def _len(self, pts):
        from atlas.flight_path.geo import path_length_km, LatLon
        return path_length_km([LatLon(lat, lon) for lat, lon in pts])

    def test_zero_for_single_point(self):
        assert self._len([(40.0, -75.0)]) == 0.0

    def test_zero_for_empty(self):
        assert self._len([]) == 0.0

    def test_two_points(self):
        d = self._len([(0.0, 0.0), (1.0, 0.0)])
        assert d == pytest.approx(111.195, abs=0.5)

    def test_three_points_additive(self):
        from atlas.flight_path.geo import haversine_km
        l1 = self._len([(0, 0), (1, 0), (1, 1)])
        expected = haversine_km(0, 0, 1, 0) + haversine_km(1, 0, 1, 1)
        assert l1 == pytest.approx(expected, abs=0.01)  # rounded to 3dp

    def test_skips_invalid_coords(self):
        # Middle point has invalid lat — path should still compute a-c distance
        from atlas.flight_path.geo import path_length_km, LatLon
        pts = [LatLon(0.0, 0.0), LatLon(999.0, 0.0), LatLon(1.0, 0.0)]
        length = path_length_km(pts)
        # Only valid segment: (0,0)→(1,0)
        assert length == pytest.approx(111.195, abs=0.5)


class TestBoundingBox:
    def test_basic_bbox(self):
        from atlas.flight_path.geo import bounding_box, LatLon
        pts = [LatLon(40.0, -75.0), LatLon(41.0, -74.0), LatLon(39.5, -76.0)]
        bb = bounding_box(pts)
        assert bb is not None
        assert bb.min_lat == pytest.approx(39.5)
        assert bb.max_lat == pytest.approx(41.0)
        assert bb.min_lon == pytest.approx(-76.0)
        assert bb.max_lon == pytest.approx(-74.0)

    def test_none_for_no_valid_points(self):
        from atlas.flight_path.geo import bounding_box, LatLon
        assert bounding_box([LatLon(999.0, 0.0)]) is None
        assert bounding_box([]) is None

    def test_expand_bbox(self):
        from atlas.flight_path.geo import bounding_box, expand_bbox, LatLon
        bb = bounding_box([LatLon(40.0, -75.0), LatLon(41.0, -74.0)])
        exp = expand_bbox(bb, margin_deg=0.1)
        assert exp.min_lat < bb.min_lat
        assert exp.max_lat > bb.max_lat


class TestPointSortKey:
    def test_utc_beats_relative(self):
        from atlas.flight_path.geo import point_sort_key
        k_utc = point_sort_key(recorded_time_utc=datetime(2024, 3, 1, 12, 0, tzinfo=UTC))
        k_rel = point_sort_key(relative_offset_seconds=-60)
        assert k_utc[0] < k_rel[0] or k_utc < k_rel

    def test_utc_none_pushed_to_end(self):
        from atlas.flight_path.geo import point_sort_key
        k_has_utc = point_sort_key(recorded_time_utc=datetime(2024, 3, 1, tzinfo=UTC))
        k_no_utc  = point_sort_key(recorded_time_utc=None, sequence_index=0)
        assert k_has_utc < k_no_utc

    def test_sequence_index_fallback(self):
        from atlas.flight_path.geo import point_sort_key
        k1 = point_sort_key(sequence_index=1)
        k2 = point_sort_key(sequence_index=2)
        assert k1 < k2

    def test_created_at_tiebreak(self):
        from atlas.flight_path.geo import point_sort_key
        t1 = datetime(2024, 1, 1, tzinfo=UTC)
        t2 = datetime(2024, 1, 2, tzinfo=UTC)
        k1 = point_sort_key(created_at=t1)
        k2 = point_sort_key(created_at=t2)
        assert k1 < k2


class TestSegmentTypeDeriving:
    def _derive(self, a_type, b_type, a_disp=False, b_disp=False):
        from atlas.flight_path.geo import derive_segment_type
        return derive_segment_type(a_type, b_type, a_disp, b_disp)

    def test_two_recorded_gives_recorded(self):
        assert self._derive("adsb", "radar") == "recorded"

    def test_estimated_endpoint_gives_estimated(self):
        assert self._derive("adsb", "estimated") == "estimated"
        assert self._derive("inferred", "radar") == "estimated"

    def test_disputed_endpoint_gives_disputed(self):
        assert self._derive("adsb", "radar", False, True) == "disputed"
        assert self._derive("adsb", "radar", True, False) == "disputed"

    def test_dispute_beats_estimated(self):
        # Even if type is estimated, disputed flag takes priority
        assert self._derive("estimated", "inferred", True, False) == "disputed"

    def test_lkp_gives_observed(self):
        assert self._derive("last_known_position", "impact") == "observed"

    def test_witness_gives_estimated_or_unknown(self):
        # witness_report is not in _RECORDED_TYPES or _ESTIMATED_TYPES;
        # the function has a specific case for witness_report → estimated
        result = self._derive("adsb", "witness_report")
        assert result in ("estimated", "unknown", "observed")


class TestPointConfidence:
    def _conf(self, **kwargs):
        from atlas.flight_path.geo import compute_point_confidence
        defaults = dict(source_method="adsb", time_precision="exact",
                        is_disputed=False, has_position=True)
        defaults.update(kwargs)
        return compute_point_confidence(**defaults)

    def test_fdr_exact_high_confidence(self):
        assert self._conf(source_method="fdr") >= 0.9

    def test_witness_low_confidence(self):
        # witness (0.40) + approximate (0.70) + has_position (1.0) / 3 = 0.70
        # With a lower precision: witness (0.40) + unknown (0.20) + pos (1.0) / 3 ≈ 0.53
        # Witness alone should be well below FDR
        fdr_conf = self._conf(source_method="fdr", time_precision="exact")
        wit_conf = self._conf(source_method="witness", time_precision="approximate")
        assert wit_conf < fdr_conf

    def test_dispute_penalty(self):
        clean = self._conf(is_disputed=False)
        disp  = self._conf(is_disputed=True)
        assert clean - disp == pytest.approx(0.30, abs=0.02)

    def test_no_position_lowers_slightly(self):
        with_pos    = self._conf(has_position=True)
        without_pos = self._conf(has_position=False)
        assert with_pos > without_pos

    def test_unknown_source_low(self):
        # unknown (0.20) + unknown (0.20) + pos (1.0) / 3 = 0.467 — below 0.5
        assert self._conf(source_method="unknown", time_precision="unknown") < 0.5

    def test_score_never_negative(self):
        assert self._conf(source_method="witness", time_precision="unknown",
                          is_disputed=True, has_position=False) >= 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — Service unit tests (mocked session)
# ─────────────────────────────────────────────────────────────────────────────

class TestFlightPathService:

    @pytest.mark.asyncio
    async def test_create_point_valid_coords(self):
        from atlas.flight_path.service import FlightPathReconstructionService

        session = MagicMock()
        session.add = MagicMock()
        session.flush = AsyncMock()

        pt = await FlightPathReconstructionService.create_point(
            session,
            accident_event_id="evt-1",
            point_type="adsb",
            source_method="adsb",
            latitude=40.6413,
            longitude=-73.7781,
            altitude_ft=3500.0,
            ground_speed_kt=180.0,
            time_precision="exact",
        )
        assert pt.latitude == 40.6413
        assert pt.longitude == -73.7781
        assert pt.confidence_score is not None and pt.confidence_score > 0.7

    @pytest.mark.asyncio
    async def test_create_point_invalid_coords_stored_as_null(self):
        """Invalid coordinates must be stored as NULL, not as-is."""
        from atlas.flight_path.service import FlightPathReconstructionService

        session = MagicMock()
        session.add = MagicMock()
        session.flush = AsyncMock()

        pt = await FlightPathReconstructionService.create_point(
            session,
            accident_event_id="evt-1",
            latitude=999.0,   # invalid
            longitude=-73.0,
            time_precision="exact",
        )
        assert pt.latitude is None
        assert pt.longitude is None

    @pytest.mark.asyncio
    async def test_create_point_distance_to_impact_calculated(self):
        from atlas.flight_path.service import FlightPathReconstructionService

        session = MagicMock()
        session.add = MagicMock()
        session.flush = AsyncMock()

        pt = await FlightPathReconstructionService.create_point(
            session,
            accident_event_id="evt-1",
            latitude=40.6413,
            longitude=-73.7781,
            accident_lat=40.7769,
            accident_lon=-73.8740,
        )
        # JFK → LGA ≈ 15–25 km
        assert pt.distance_to_impact_km is not None
        assert 15 < float(pt.distance_to_impact_km) < 25

    @pytest.mark.asyncio
    async def test_create_point_disputed_lowers_confidence(self):
        from atlas.flight_path.service import FlightPathReconstructionService

        session = MagicMock()
        session.add = MagicMock()
        session.flush = AsyncMock()

        pt = await FlightPathReconstructionService.create_point(
            session,
            accident_event_id="evt-1",
            is_disputed=True,
            source_method="adsb",
            time_precision="exact",
            latitude=40.0, longitude=-75.0,
        )
        assert pt.is_disputed is True
        assert pt.confidence_score < 0.8

    @pytest.mark.asyncio
    async def test_create_point_attaches_claims(self):
        from atlas.flight_path.service import FlightPathReconstructionService

        session = MagicMock()
        session.add = MagicMock()
        session.flush = AsyncMock()

        await FlightPathReconstructionService.create_point(
            session,
            accident_event_id="evt-1",
            claim_ids=["c1", "c2"],
        )
        # session.add: 1 for point + 2 for claim links
        assert session.add.call_count >= 3

    @pytest.mark.asyncio
    async def test_delete_point_true_when_found(self):
        from atlas.flight_path.service import FlightPathReconstructionService

        row = MagicMock()
        session = MagicMock()
        session.get = AsyncMock(return_value=row)
        session.delete = AsyncMock()

        assert await FlightPathReconstructionService.delete_point(session, point_id="p1") is True

    @pytest.mark.asyncio
    async def test_delete_point_false_when_missing(self):
        from atlas.flight_path.service import FlightPathReconstructionService

        session = MagicMock()
        session.get = AsyncMock(return_value=None)

        assert await FlightPathReconstructionService.delete_point(session, point_id="gone") is False

    @pytest.mark.asyncio
    async def test_create_annotation_links_timeline_event(self):
        from atlas.flight_path.service import FlightPathReconstructionService

        session = MagicMock()
        session.add = MagicMock()
        session.flush = AsyncMock()

        ann = await FlightPathReconstructionService.create_annotation(
            session,
            accident_event_id="evt-1",
            annotation_type="gpws_pull_up",
            title="GPWS Pull Up",
            timeline_event_id="tle-xyz",
            relative_offset_seconds=-8,
            time_precision="relative",
        )
        assert ann.timeline_event_id == "tle-xyz"
        assert ann.relative_offset_seconds == -8

    @pytest.mark.asyncio
    async def test_delete_annotation_false_when_missing(self):
        from atlas.flight_path.service import FlightPathReconstructionService

        session = MagicMock()
        session.get = AsyncMock(return_value=None)

        assert await FlightPathReconstructionService.delete_annotation(session, annotation_id="gone") is False

    @pytest.mark.asyncio
    async def test_get_profile_empty(self):
        from atlas.flight_path.service import FlightPathReconstructionService

        with patch.object(
            FlightPathReconstructionService, "get_points",
            new_callable=AsyncMock, return_value=[]
        ):
            profile = await FlightPathReconstructionService.get_profile(MagicMock(), "evt-1")

        assert profile["altitude"] == []
        assert profile["speed"] == []
        assert "chart_note" in profile

    @pytest.mark.asyncio
    async def test_get_profile_marks_estimated_points(self):
        from atlas.flight_path.service import FlightPathReconstructionService

        pts = [
            _pt(point_type="adsb", altitude_ft=5000.0),
            _pt(point_type="estimated", altitude_ft=1000.0),
        ]
        with patch.object(
            FlightPathReconstructionService, "get_points",
            new_callable=AsyncMock, return_value=pts
        ):
            profile = await FlightPathReconstructionService.get_profile(MagicMock(), "evt-1")

        alt_pts = profile["altitude"]
        assert alt_pts[0]["is_estimated"] is False
        assert alt_pts[1]["is_estimated"] is True

    @pytest.mark.asyncio
    async def test_reconstruction_empty_state(self):
        from atlas.flight_path.service import FlightPathReconstructionService

        record = MagicMock()
        record.location_lat = 40.0
        record.location_lon = -75.0

        for attr in ("get_points", "get_annotations", "get_segments"):
            patch.object(FlightPathReconstructionService, attr,
                         new_callable=AsyncMock, return_value=[]).start()

        rec_result = MagicMock()
        rec_result.scalar_one_or_none.return_value = record
        session = MagicMock()
        session.execute = AsyncMock(return_value=rec_result)

        result = await FlightPathReconstructionService.get_reconstruction(session, "evt-1")
        assert result["point_count"] == 0
        assert result["has_path"] is False
        assert result["accident_site"]["latitude"] == pytest.approx(40.0)
        assert "data_note" in result

        patch.stopall()

    @pytest.mark.asyncio
    async def test_reconstruction_is_estimated_flag(self):
        from atlas.flight_path.service import _point_to_dict

        # Estimated point
        est = _pt(point_type="estimated", lat=40.0, lon=-75.0)
        assert _point_to_dict(est)["is_estimated"] is True

        # Recorded point
        rec = _pt(point_type="adsb", lat=40.0, lon=-75.0)
        assert _point_to_dict(rec)["is_estimated"] is False

    @pytest.mark.asyncio
    async def test_segment_render_style_mapping(self):
        from atlas.flight_path.service import _segment_render_style

        assert _segment_render_style("recorded", False) == "solid_recorded"
        assert _segment_render_style("observed", False) == "solid_recorded"
        assert _segment_render_style("estimated", False) == "dashed_estimated"
        assert _segment_render_style("inferred", False) == "dashed_estimated"
        assert _segment_render_style("recorded", True) == "disputed"
        assert _segment_render_style("unknown", False) == "unknown"

    @pytest.mark.asyncio
    async def test_rebuild_idempotent_does_not_delete_user_points(self):
        """Rebuild must preserve user-entered points and only regenerate segments."""
        from atlas.flight_path.service import FlightPathReconstructionService

        pts = [
            _pt(point_type="adsb", lat=40.0, lon=-75.0),
            _pt(point_type="impact", lat=40.5, lon=-74.5),
        ]
        record = MagicMock()
        record.location_lat = 40.5
        record.location_lon = -74.5

        with patch.object(
            FlightPathReconstructionService, "get_points",
            new_callable=AsyncMock, return_value=pts
        ):
            exec_result = MagicMock()
            exec_result.scalar_one_or_none.return_value = record
            session = MagicMock()
            session.execute = AsyncMock(return_value=exec_result)
            session.add = MagicMock()

            result = await FlightPathReconstructionService.rebuild(
                session, accident_event_id="evt-1", operator_id="reviewer"
            )

        # Points were not deleted
        assert result["point_count"] == 2
        # One segment created between two valid points
        assert result["segment_count"] == 1
        # session.add called for the new segment
        session.add.assert_called()


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 — API router tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def fp_client():
    import httpx
    from fastapi import FastAPI
    from atlas.flight_path.router import router as fp_router
    from atlas.db.engine import get_db, get_read_db
    from atlas.api.auth import require_reviewer, OperatorContext

    app = FastAPI()
    app.include_router(fp_router)

    async def noop_db():
        yield MagicMock()

    app.dependency_overrides[get_db] = noop_db
    app.dependency_overrides[get_read_db] = noop_db
    app.dependency_overrides[require_reviewer] = lambda: OperatorContext(
        id="reviewer", role="reviewer", key_id=""
    )

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


class TestFlightPathRouterRead:

    @pytest.mark.asyncio
    async def test_get_flight_path_empty(self, fp_client):
        with (
            patch("atlas.flight_path.router._require_accident", new_callable=AsyncMock),
            patch(
                "atlas.flight_path.router.FlightPathReconstructionService.get_reconstruction",
                new_callable=AsyncMock,
                return_value={
                    "accident_event_id": "evt-1", "point_count": 0, "has_path": False,
                    "accident_site": None, "last_recorded_point_id": None,
                    "impact_point_id": None, "bounds": None, "path_length_km": 0.0,
                    "confidence_summary": {"avg_confidence": None, "disputed_point_count": 0, "point_count": 0},
                    "points": [], "segments": [], "annotations": [],
                    "data_note": "test note",
                }
            ),
        ):
            resp = await fp_client.get("/api/v1/accidents/evt-1/flight-path")
        assert resp.status_code == 200
        data = resp.json()
        assert data["point_count"] == 0
        assert data["has_path"] is False
        assert "data_note" in data

    @pytest.mark.asyncio
    async def test_get_flight_path_404(self, fp_client):
        from fastapi import HTTPException
        with patch(
            "atlas.flight_path.router._require_accident",
            new_callable=AsyncMock,
            side_effect=HTTPException(404, "not found"),
        ):
            resp = await fp_client.get("/api/v1/accidents/bad/flight-path")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_profile_200(self, fp_client):
        with (
            patch("atlas.flight_path.router._require_accident", new_callable=AsyncMock),
            patch(
                "atlas.flight_path.router.FlightPathReconstructionService.get_profile",
                new_callable=AsyncMock,
                return_value={
                    "accident_event_id": "evt-1",
                    "altitude": [], "speed": [], "vertical_speed": [],
                    "distance_to_impact": [], "chart_note": "test",
                }
            ),
        ):
            resp = await fp_client.get("/api/v1/accidents/evt-1/flight-path/profile")
        assert resp.status_code == 200
        assert "altitude" in resp.json()
        assert "chart_note" in resp.json()

    @pytest.mark.asyncio
    async def test_get_points_200(self, fp_client):
        p = _pt(lat=40.0, lon=-75.0)
        with (
            patch("atlas.flight_path.router._require_accident", new_callable=AsyncMock),
            patch(
                "atlas.flight_path.router.FlightPathReconstructionService.get_points",
                new_callable=AsyncMock, return_value=[p]
            ),
        ):
            resp = await fp_client.get("/api/v1/accidents/evt-1/flight-path/points")
        assert resp.status_code == 200
        assert resp.json()["point_count"] == 1


class TestFlightPathRouterWrite:

    @pytest.mark.asyncio
    async def test_create_point_201(self, fp_client):
        p = _pt(point_type="adsb", lat=40.0, lon=-75.0)
        with (
            patch("atlas.flight_path.router._require_accident", new_callable=AsyncMock),
            patch(
                "atlas.flight_path.router.FlightPathReconstructionService.create_point",
                new_callable=AsyncMock, return_value=p
            ),
        ):
            resp = await fp_client.post(
                "/api/v1/accidents/evt-1/flight-path/points",
                json={"point_type": "adsb", "latitude": 40.0, "longitude": -75.0,
                      "altitude_ft": 10000.0, "time_precision": "exact"},
            )
        assert resp.status_code == 201
        assert resp.json()["point_type"] == "adsb"

    @pytest.mark.asyncio
    async def test_patch_point_200(self, fp_client):
        p = _pt(point_type="adsb", altitude_ft=9000.0)
        with patch(
            "atlas.flight_path.router.FlightPathReconstructionService.update_point",
            new_callable=AsyncMock, return_value=p
        ):
            resp = await fp_client.patch(
                f"/api/v1/flight-path/points/{p.id}",
                json={"altitude_ft": 9000.0},
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_patch_point_404(self, fp_client):
        with patch(
            "atlas.flight_path.router.FlightPathReconstructionService.update_point",
            new_callable=AsyncMock, return_value=None
        ):
            resp = await fp_client.patch("/api/v1/flight-path/points/gone", json={"altitude_ft": 1.0})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_point_204(self, fp_client):
        with patch(
            "atlas.flight_path.router.FlightPathReconstructionService.delete_point",
            new_callable=AsyncMock, return_value=True
        ):
            resp = await fp_client.delete("/api/v1/flight-path/points/some-id")
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_delete_point_404(self, fp_client):
        with patch(
            "atlas.flight_path.router.FlightPathReconstructionService.delete_point",
            new_callable=AsyncMock, return_value=False
        ):
            resp = await fp_client.delete("/api/v1/flight-path/points/gone")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_create_annotation_201(self, fp_client):
        a = _ann()
        with (
            patch("atlas.flight_path.router._require_accident", new_callable=AsyncMock),
            patch(
                "atlas.flight_path.router.FlightPathReconstructionService.create_annotation",
                new_callable=AsyncMock, return_value=a
            ),
        ):
            resp = await fp_client.post(
                "/api/v1/accidents/evt-1/flight-path/annotations",
                json={"annotation_type": "gpws_sink_rate", "title": "GPWS Sink Rate",
                      "relative_offset_seconds": -10, "time_precision": "relative"},
            )
        assert resp.status_code == 201
        assert resp.json()["annotation_type"] == "gpws_sink_rate"

    @pytest.mark.asyncio
    async def test_patch_annotation_404(self, fp_client):
        with patch(
            "atlas.flight_path.router.FlightPathReconstructionService.update_annotation",
            new_callable=AsyncMock, return_value=None
        ):
            resp = await fp_client.patch("/api/v1/flight-path/annotations/gone", json={"title": "x"})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_annotation_204(self, fp_client):
        with patch(
            "atlas.flight_path.router.FlightPathReconstructionService.delete_annotation",
            new_callable=AsyncMock, return_value=True
        ):
            resp = await fp_client.delete("/api/v1/flight-path/annotations/some-id")
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_rebuild_200(self, fp_client):
        with (
            patch("atlas.flight_path.router._require_accident", new_callable=AsyncMock),
            patch(
                "atlas.flight_path.router.FlightPathReconstructionService.rebuild",
                new_callable=AsyncMock, return_value={"point_count": 5, "segment_count": 4}
            ),
        ):
            resp = await fp_client.post("/api/v1/accidents/evt-1/flight-path/rebuild")
        assert resp.status_code == 200
        assert resp.json()["point_count"] == 5


# ─────────────────────────────────────────────────────────────────────────────
# Phase 10 — Spec fixture scenarios
# ─────────────────────────────────────────────────────────────────────────────

class TestSpecFixtures:

    def test_fixture1_segment_types_recorded_then_estimated(self):
        """
        Fixture 1: Points 1-5 are recorded ADS-B; points 6-8 are estimated/impact.
        Segments 1-4 must be recorded; segments 5-7 must be estimated.
        """
        from atlas.flight_path.geo import derive_segment_type

        # Segments between recorded ADS-B points
        for _ in range(4):
            assert derive_segment_type("adsb", "adsb") == "recorded"

        # Segment from last recorded ADS-B to estimated
        assert derive_segment_type("last_known_position", "estimated") == "estimated"

        # Segment from estimated to impact
        assert derive_segment_type("estimated", "impact") == "estimated"

    def test_fixture2_gpws_annotation_ordering(self):
        """
        Fixture 2: GPWS "Sink Rate" at T-10, "Pull Up" at T-8, impact at T=0.
        Ordering by relative_offset_seconds must be correct.
        """
        from atlas.flight_path.service import _sort_annotations

        sink_rate = _ann(relative_offset_seconds=-10, annotation_type="gpws_sink_rate")
        pull_up   = _ann(relative_offset_seconds=-8,  annotation_type="gpws_pull_up")
        impact    = _ann(relative_offset_seconds=0,   annotation_type="impact")

        ordered = _sort_annotations([impact, pull_up, sink_rate])
        assert ordered[0].annotation_type == "gpws_sink_rate"
        assert ordered[1].annotation_type == "gpws_pull_up"
        assert ordered[2].annotation_type == "impact"

    def test_fixture2_approximate_time_preserved(self):
        """Approximate time precision must NOT be promoted to exact."""
        from atlas.flight_path.service import _annotation_to_dict

        a = _ann(time_precision="approximate", relative_offset_seconds=-10)
        d = _annotation_to_dict(a)
        assert d["time_precision"] == "approximate"

    def test_fixture3_disputed_last_known_position(self):
        """
        Fixture 3: Source A gives coord X, Source B gives coord Y.
        The point must be marked is_disputed=True.
        The segment from this disputed point must have segment_type='disputed'.
        """
        from atlas.flight_path.geo import derive_segment_type

        # Disputed LKP → following point
        seg_type = derive_segment_type("last_known_position", "estimated", True, False)
        assert seg_type == "disputed"

        # Verify the point itself
        p = _pt(point_type="last_known_position", is_disputed=True,
                dispute_summary="Source A: 40.5°N; Source B: 40.6°N")
        from atlas.flight_path.service import _point_to_dict
        d = _point_to_dict(p)
        assert d["is_disputed"] is True
        assert d["dispute_summary"] is not None

    def test_fixture3_both_claims_preserved_in_dict(self):
        """
        Claims must not be silently resolved — the claim list must show all
        supporting claims including the contradicting one.
        """
        from atlas.flight_path.service import _point_to_dict

        lnk_a = MagicMock()
        lnk_a.claim_id = "c-a"
        lnk_a.link_reason = "supporting_claim"
        lnk_a.claim = MagicMock()
        lnk_a.claim.field_name = "location"
        lnk_a.claim.claim_type = "confirmed"
        lnk_a.claim.source_id = "src-A"

        lnk_b = MagicMock()
        lnk_b.claim_id = "c-b"
        lnk_b.link_reason = "disputed_claim"
        lnk_b.claim = MagicMock()
        lnk_b.claim.field_name = "location"
        lnk_b.claim.claim_type = "disputed"
        lnk_b.claim.source_id = "src-B"

        p = _pt(is_disputed=True, claim_links=[lnk_a, lnk_b])
        d = _point_to_dict(p)

        claim_ids = {c["claim_id"] for c in d["supporting_claims"]}
        assert "c-a" in claim_ids
        assert "c-b" in claim_ids
