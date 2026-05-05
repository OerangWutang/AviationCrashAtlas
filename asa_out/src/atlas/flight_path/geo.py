"""
atlas.flight_path.geo — Reusable geospatial helpers for flight path reconstruction.

All functions operate on WGS84 decimal-degree coordinates.  No external
geospatial libraries are required — pure Python math only.

Unit conventions
----------------
  Distances : kilometres (km)
  Bearings  : degrees true (0 = North, 90 = East, clockwise)
  Altitudes : feet (callers normalise before passing)
  Speed     : knots
  Lat/lon   : decimal degrees, WGS84

Coordinate validity
-------------------
  latitude  : -90 ≤ lat ≤ 90
  longitude : -180 ≤ lon ≤ 180
  Points with invalid or NULL coordinates are excluded from map rendering.
  Raw invalid values may still be stored in raw_data for provenance.

Extension points
----------------
  - Douglas-Peucker path simplification: add simplify_path(points, epsilon)
    for reducing dense ADS-B tracks before sending to the frontend.
  - Rhumb-line distance: useful for short legs along constant bearing.
  - Cross-track/along-track distance: useful for route deviation calculation.
  - Terrain elevation lookup: add get_elevation(lat, lon) once a DEM source
    is available (SRTM, Mapbox Terrain-RGB, etc.).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EARTH_RADIUS_KM = 6371.0088   # mean radius, WGS84


# ---------------------------------------------------------------------------
# Coordinate validation
# ---------------------------------------------------------------------------

def is_valid_lat(lat: float | None) -> bool:
    """Return True when lat is a finite number in [-90, 90]."""
    return lat is not None and math.isfinite(lat) and -90.0 <= lat <= 90.0


def is_valid_lon(lon: float | None) -> bool:
    """Return True when lon is a finite number in [-180, 180]."""
    return lon is not None and math.isfinite(lon) and -180.0 <= lon <= 180.0


def is_valid_coord(lat: float | None, lon: float | None) -> bool:
    """Return True only when both lat and lon are valid."""
    return is_valid_lat(lat) and is_valid_lon(lon)


# ---------------------------------------------------------------------------
# Distance
# ---------------------------------------------------------------------------

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Great-circle distance in kilometres between two WGS84 coordinate pairs.

    Uses the haversine formula.  Accurate to ~0.5% for distances up to
    ~2000 km — sufficient for accident investigation use cases.
    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi  = math.radians(lat2 - lat1)
    dlam  = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(min(a, 1.0)))


# ---------------------------------------------------------------------------
# Bearing
# ---------------------------------------------------------------------------

def bearing_degrees(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Initial bearing from point 1 to point 2, in degrees true (0–360).

    Returns 0.0 when both points are identical.
    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlam = math.radians(lon2 - lon1)
    x = math.sin(dlam) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
    b = math.degrees(math.atan2(x, y))
    return (b + 360.0) % 360.0


# ---------------------------------------------------------------------------
# Path length
# ---------------------------------------------------------------------------

@dataclass
class LatLon:
    lat: float
    lon: float


def path_length_km(points: Sequence[LatLon]) -> float:
    """
    Total great-circle path length across an ordered sequence of points.

    Points with invalid coordinates are silently skipped (they do not
    contribute to the length, but don't cause errors).
    """
    valid = [p for p in points if is_valid_coord(p.lat, p.lon)]
    if len(valid) < 2:
        return 0.0
    total = 0.0
    for a, b in zip(valid, valid[1:]):
        total += haversine_km(a.lat, a.lon, b.lat, b.lon)
    return round(total, 3)


# ---------------------------------------------------------------------------
# Bounding box
# ---------------------------------------------------------------------------

@dataclass
class BoundingBox:
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float

    def to_dict(self) -> dict[str, float]:
        return {
            "min_lat": self.min_lat, "max_lat": self.max_lat,
            "min_lon": self.min_lon, "max_lon": self.max_lon,
        }

    @property
    def center(self) -> tuple[float, float]:
        return (
            (self.min_lat + self.max_lat) / 2,
            (self.min_lon + self.max_lon) / 2,
        )


def bounding_box(points: Sequence[LatLon]) -> BoundingBox | None:
    """
    Return the minimum bounding rectangle for a set of path points.

    Returns None when no valid coordinates exist.
    """
    valid = [p for p in points if is_valid_coord(p.lat, p.lon)]
    if not valid:
        return None
    lats = [p.lat for p in valid]
    lons = [p.lon for p in valid]
    return BoundingBox(
        min_lat=min(lats), max_lat=max(lats),
        min_lon=min(lons), max_lon=max(lons),
    )


def expand_bbox(bbox: BoundingBox, margin_deg: float = 0.1) -> BoundingBox:
    """Add a margin in degrees around a bounding box for map padding."""
    return BoundingBox(
        min_lat=bbox.min_lat - margin_deg,
        max_lat=bbox.max_lat + margin_deg,
        min_lon=bbox.min_lon - margin_deg,
        max_lon=bbox.max_lon + margin_deg,
    )


# ---------------------------------------------------------------------------
# Point sorting key (mirrors timeline 4-tier strategy)
# ---------------------------------------------------------------------------

_LARGE = 10 ** 9


def point_sort_key(
    recorded_time_utc=None,
    relative_offset_seconds=None,
    sequence_index=None,
    created_at=None,
) -> tuple:
    """
    4-tier sort key for flight path points.

    Tier 1: recorded_time_utc   (epoch seconds; None → pushed to end)
    Tier 2: relative_offset_seconds (signed int; None → pushed to end)
    Tier 3: sequence_index      (int; None → pushed to end)
    Tier 4: created_at          (epoch seconds; 0.0 fallback)
    """
    t1 = recorded_time_utc.timestamp() if recorded_time_utc else float("inf")
    t2 = relative_offset_seconds if relative_offset_seconds is not None else _LARGE
    t3 = sequence_index if sequence_index is not None else _LARGE
    t4 = created_at.timestamp() if created_at else 0.0
    return (t1, t2, t3, t4)


# ---------------------------------------------------------------------------
# Segment type derivation
# ---------------------------------------------------------------------------

_ESTIMATED_TYPES = frozenset({
    "estimated", "inferred", "report_estimate", "planned_route", "search_area",
})
_RECORDED_TYPES = frozenset({
    "adsb", "radar", "fdr", "departure", "enroute", "final_approach",
    "impact", "wreckage_location", "cvr_reference",
})


def derive_segment_type(
    start_point_type: str,
    end_point_type: str,
    start_disputed: bool = False,
    end_disputed: bool = False,
) -> str:
    """
    Derive the rendering category for a segment from its endpoint types.

    Rules (in priority order):
      1. Either endpoint is disputed → "disputed"
      2. Either endpoint is an estimated/inferred type → "estimated"
      3. Both endpoints are in the observed/recorded set → "recorded"
      4. Mixed known/unknown → "observed"
      5. Otherwise → "unknown"
    """
    if start_disputed or end_disputed:
        return "disputed"
    a, b = start_point_type, end_point_type
    if a in _ESTIMATED_TYPES or b in _ESTIMATED_TYPES:
        return "estimated"
    if a in _RECORDED_TYPES and b in _RECORDED_TYPES:
        return "recorded"
    if a in _RECORDED_TYPES or b in _RECORDED_TYPES:
        return "observed"
    if a == "last_known_position" or b == "last_known_position":
        return "observed"
    if a == "witness_report" or b == "witness_report":
        return "estimated"
    return "unknown"


# ---------------------------------------------------------------------------
# Confidence scoring for points
# ---------------------------------------------------------------------------

_SOURCE_METHOD_CONFIDENCE: dict[str, float] = {
    "fdr":                  1.00,
    "adsb":                 0.95,
    "radar":                0.85,
    "cvr":                  0.80,
    "atc_transcript":       0.70,
    "investigation_report": 0.65,
    "witness":              0.40,
    "manual":               0.55,
    "inferred":             0.35,
    "estimated":            0.30,
    "unknown":              0.20,
}

_TIME_PRECISION_CONFIDENCE: dict[str, float] = {
    "exact":         1.00,
    "approximate":   0.70,
    "relative":      0.50,
    "sequence_only": 0.40,
    "unknown":       0.20,
}


def compute_point_confidence(
    source_method: str | None,
    time_precision: str,
    is_disputed: bool,
    has_position: bool = True,
) -> float:
    """
    Return a 0.0–1.0 confidence score for a flight path point.

    Factors:
      source_method_factor  — how authoritative is the source
      time_precision_factor — how precise is the temporal placement
      position_factor       — 1.0 if coordinates are valid, 0.7 if position unknown
    """
    src_f = _SOURCE_METHOD_CONFIDENCE.get(source_method or "unknown", 0.20)
    time_f = _TIME_PRECISION_CONFIDENCE.get(time_precision, 0.20)
    pos_f = 1.0 if has_position else 0.7
    score = (src_f + time_f + pos_f) / 3.0
    if is_disputed:
        score = max(0.0, score - 0.30)
    return round(score, 3)
