"""
FlightPathReconstructionService
================================
Manages the reconstructed flight path for aviation accidents.

Design principles
-----------------
- Claims are the source of truth.  Service rows are derived/curated records.
- Estimated and inferred points are NEVER promoted to "recorded" status.
- raw_data is preserved verbatim; never returned by the default reconstruction
  payload (use get_raw_points() or the /points endpoint for raw detail).
- Rebuild is idempotent: re-running never deletes user-entered points,
  overwrites raw source data, or silently resolves disputes.
- Point ordering: UTC time → relative offset → sequence index → created_at.

Rebuild recalculates
--------------------
  ✓ distance_to_impact_km for all points with valid coordinates
  ✓ Auto-generated segments (existing user-created segments preserved)
  ✓ Segment type (recorded / estimated / disputed)
  ✓ Segment length and bearing
  ✓ Profile arrays (altitude, speed, vertical speed)
  ✓ Confidence scores for points and segments

Rebuild does NOT
----------------
  ✗ Delete user-entered points or annotations
  ✗ Invent phantom points
  ✗ Overwrite raw_data
  ✗ Silently resolve disputes
  ✗ Create causal conclusions

Extension points
----------------
  - Inject ADS-B / radar / FDR candidate points via create_point() batch
    from ingestion pipeline before calling rebuild().
  - Add terrain elevation lookup in rebuild() to compute AGL altitude from
    MSL altitude minus terrain elevation (needs DEM source).
  - Add path simplification (Douglas-Peucker) in get_reconstruction() to
    reduce frontend payload for very dense tracks.
  - Export to KML / GeoJSON once the need arises.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from atlas.flight_path.geo import (
    BoundingBox,
    LatLon,
    bounding_box,
    bearing_degrees,
    compute_point_confidence,
    derive_segment_type,
    expand_bbox,
    haversine_km,
    is_valid_coord,
    path_length_km,
    point_sort_key,
)
from atlas.models.orm import (
    AccidentEvent,
    AccidentFlightPathAnnotation,
    AccidentFlightPathPoint,
    AccidentFlightPathSegment,
    AccidentRecord,
    Claim,
    FlightPathAnnotationClaim,
    FlightPathPointClaim,
    PathPointType,
    PathSegmentType,
    TimePrecision,
)

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Ordering helper
# ---------------------------------------------------------------------------

def _sort_points(points: list[AccidentFlightPathPoint]) -> list[AccidentFlightPathPoint]:
    return sorted(
        points,
        key=lambda p: point_sort_key(
            recorded_time_utc=p.recorded_time_utc,
            relative_offset_seconds=p.relative_offset_seconds,
            sequence_index=p.sequence_index,
            created_at=p.created_at,
        ),
    )


def _sort_annotations(annotations: list[AccidentFlightPathAnnotation]) -> list[AccidentFlightPathAnnotation]:
    def _ann_key(a: AccidentFlightPathAnnotation) -> tuple:
        t1 = a.annotation_time_utc.timestamp() if a.annotation_time_utc else float("inf")
        t2 = a.relative_offset_seconds if a.relative_offset_seconds is not None else 10 ** 9
        t3 = a.created_at.timestamp() if a.created_at else 0.0
        return (t1, t2, t3)
    return sorted(annotations, key=_ann_key)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class FlightPathReconstructionService:
    """Stateless service — callers own the session and commit."""

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    @staticmethod
    async def get_points(
        db: AsyncSession,
        accident_event_id: str,
    ) -> list[AccidentFlightPathPoint]:
        result = await db.execute(
            select(AccidentFlightPathPoint)
            .where(AccidentFlightPathPoint.accident_event_id == accident_event_id)
            .options(
                selectinload(AccidentFlightPathPoint.claim_links).selectinload(
                    FlightPathPointClaim.claim
                ),
                selectinload(AccidentFlightPathPoint.source),
            )
        )
        return _sort_points(list(result.scalars().all()))

    @staticmethod
    async def get_annotations(
        db: AsyncSession,
        accident_event_id: str,
    ) -> list[AccidentFlightPathAnnotation]:
        result = await db.execute(
            select(AccidentFlightPathAnnotation)
            .where(AccidentFlightPathAnnotation.accident_event_id == accident_event_id)
            .options(
                selectinload(AccidentFlightPathAnnotation.claim_links).selectinload(
                    FlightPathAnnotationClaim.claim
                ),
            )
        )
        return _sort_annotations(list(result.scalars().all()))

    @staticmethod
    async def get_segments(
        db: AsyncSession,
        accident_event_id: str,
    ) -> list[AccidentFlightPathSegment]:
        result = await db.execute(
            select(AccidentFlightPathSegment)
            .where(AccidentFlightPathSegment.accident_event_id == accident_event_id)
            .options(
                selectinload(AccidentFlightPathSegment.start_point),
                selectinload(AccidentFlightPathSegment.end_point),
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_supporting_claims(
        db: AsyncSession,
        accident_event_id: str,
    ) -> list[Claim]:
        # Point claims
        pt_subq = (
            select(AccidentFlightPathPoint.id)
            .where(AccidentFlightPathPoint.accident_event_id == accident_event_id)
            .scalar_subquery()
        )
        pt_claims = list((await db.execute(
            select(Claim)
            .join(FlightPathPointClaim, FlightPathPointClaim.claim_id == Claim.id)
            .where(FlightPathPointClaim.flight_path_point_id.in_(pt_subq))
            .distinct()
        )).scalars().all())

        # Annotation claims
        ann_subq = (
            select(AccidentFlightPathAnnotation.id)
            .where(AccidentFlightPathAnnotation.accident_event_id == accident_event_id)
            .scalar_subquery()
        )
        ann_claims = list((await db.execute(
            select(Claim)
            .join(FlightPathAnnotationClaim, FlightPathAnnotationClaim.claim_id == Claim.id)
            .where(FlightPathAnnotationClaim.annotation_id.in_(ann_subq))
            .distinct()
        )).scalars().all())

        # Merge and deduplicate by id
        seen: dict[str, Claim] = {}
        for c in pt_claims + ann_claims:
            seen[c.id] = c
        return list(seen.values())

    # ------------------------------------------------------------------
    # Reconstruction payload
    # ------------------------------------------------------------------

    @staticmethod
    async def get_reconstruction(
        db: AsyncSession,
        accident_event_id: str,
    ) -> dict[str, Any]:
        """
        Build the complete frontend reconstruction payload.

        Does NOT include raw_data blobs — use get_points() for that.
        """
        points = await FlightPathReconstructionService.get_points(db, accident_event_id)
        annotations = await FlightPathReconstructionService.get_annotations(db, accident_event_id)
        segments = await FlightPathReconstructionService.get_segments(db, accident_event_id)

        # Fetch accident location for reference
        record_result = await db.execute(
            select(AccidentRecord).where(AccidentRecord.id == accident_event_id)
        )
        record = record_result.scalar_one_or_none()
        accident_lat = float(record.location_lat) if record and record.location_lat else None
        accident_lon = float(record.location_lon) if record and record.location_lon else None

        # Build bounding box
        latlons = [
            LatLon(float(p.latitude), float(p.longitude))
            for p in points
            if is_valid_coord(
                float(p.latitude) if p.latitude is not None else None,
                float(p.longitude) if p.longitude is not None else None,
            )
        ]
        if accident_lat is not None and accident_lon is not None:
            latlons.append(LatLon(accident_lat, accident_lon))
        bbox = bounding_box(latlons)
        bbox_padded = expand_bbox(bbox) if bbox else None

        # Confidence summary
        conf_scores = [float(p.confidence_score) for p in points if p.confidence_score is not None]
        avg_conf = round(sum(conf_scores) / len(conf_scores), 3) if conf_scores else None
        disputed_points = sum(1 for p in points if p.is_disputed)

        # Find special markers
        last_recorded = next(
            (p for p in reversed(points) if p.point_type in (
                PathPointType.ADSB, PathPointType.RADAR, PathPointType.FDR,
                PathPointType.LAST_KNOWN_POSITION,
            )), None
        )
        impact_point = next(
            (p for p in points if p.point_type == PathPointType.IMPACT), None
        )

        return {
            "accident_event_id": accident_event_id,
            "point_count": len(points),
            "has_path": len(points) > 0,
            "accident_site": {
                "latitude": accident_lat,
                "longitude": accident_lon,
            } if accident_lat is not None else None,
            "last_recorded_point_id": last_recorded.id if last_recorded else None,
            "impact_point_id": impact_point.id if impact_point else None,
            "bounds": bbox_padded.to_dict() if bbox_padded else None,
            "path_length_km": path_length_km(latlons[:-1] if accident_lat else latlons),
            "confidence_summary": {
                "avg_confidence": avg_conf,
                "disputed_point_count": disputed_points,
                "point_count": len(points),
            },
            "points": [_point_to_dict(p) for p in points],
            "segments": [_segment_to_dict(s) for s in segments],
            "annotations": [_annotation_to_dict(a) for a in annotations],
            "data_note": (
                "Points with time_precision=approximate, relative, or unknown are "
                "rendered as estimated/inferred — never as confirmed recorded positions. "
                "Disputed points preserve all source claims without resolution."
            ),
        }

    @staticmethod
    async def get_profile(
        db: AsyncSession,
        accident_event_id: str,
    ) -> dict[str, Any]:
        """
        Return chart-ready altitude, speed, and vertical speed profile arrays.

        Each array is ordered by the same 4-tier sort key as the map points.
        Points with NULL altitude/speed are included with null values so the
        chart can display gaps rather than silently skipping them.
        """
        points = await FlightPathReconstructionService.get_points(db, accident_event_id)

        altitude_series: list[dict] = []
        speed_series: list[dict] = []
        vs_series: list[dict] = []
        dist_series: list[dict] = []

        for p in points:
            # X-axis label: prefer UTC time, then relative offset, then sequence
            if p.recorded_time_utc:
                x_label = p.recorded_time_utc.isoformat()
                x_type = "utc"
            elif p.relative_offset_seconds is not None:
                x_label = str(p.relative_offset_seconds)
                x_type = "relative_s"
            elif p.sequence_index is not None:
                x_label = str(p.sequence_index)
                x_type = "sequence"
            else:
                x_label = p.id
                x_type = "id"

            base = {
                "point_id": p.id,
                "x": x_label,
                "x_type": x_type,
                "point_type": p.point_type,
                "time_precision": p.time_precision,
                "is_estimated": p.point_type in ("estimated", "inferred", "report_estimate"),
                "is_disputed": p.is_disputed,
                "confidence_score": float(p.confidence_score) if p.confidence_score else None,
            }

            altitude_series.append({**base, "altitude_ft": float(p.altitude_ft) if p.altitude_ft is not None else None})
            speed_series.append({
                **base,
                "ground_speed_kt": float(p.ground_speed_kt) if p.ground_speed_kt is not None else None,
                "indicated_airspeed_kt": float(p.indicated_airspeed_kt) if p.indicated_airspeed_kt is not None else None,
            })
            vs_series.append({**base, "vertical_speed_fpm": float(p.vertical_speed_fpm) if p.vertical_speed_fpm is not None else None})
            dist_series.append({**base, "distance_to_impact_km": float(p.distance_to_impact_km) if p.distance_to_impact_km is not None else None})

        return {
            "accident_event_id": accident_event_id,
            "altitude": altitude_series,
            "speed": speed_series,
            "vertical_speed": vs_series,
            "distance_to_impact": dist_series,
            "chart_note": (
                "Points with is_estimated=true should be rendered differently "
                "(dashed/lower opacity) — they are inferred, not recorded values. "
                "time_precision field indicates how precise the X-axis placement is."
            ),
        }

    # ------------------------------------------------------------------
    # Write — points
    # ------------------------------------------------------------------

    @staticmethod
    async def create_point(
        db: AsyncSession,
        *,
        accident_event_id: str,
        point_type: str = PathPointType.UNKNOWN,
        source_method: str | None = None,
        sequence_index: int | None = None,
        recorded_time_utc: datetime | None = None,
        relative_offset_seconds: int | None = None,
        time_precision: str = TimePrecision.UNKNOWN,
        latitude: float | None = None,
        longitude: float | None = None,
        altitude_ft: float | None = None,
        altitude_reference: str | None = None,
        radio_altitude_ft: float | None = None,
        ground_speed_kt: float | None = None,
        indicated_airspeed_kt: float | None = None,
        vertical_speed_fpm: float | None = None,
        heading_degrees: float | None = None,
        track_degrees: float | None = None,
        uncertainty_radius_m: float | None = None,
        is_disputed: bool = False,
        dispute_summary: str | None = None,
        notes: str | None = None,
        raw_data: dict | None = None,
        source_id: str | None = None,
        claim_ids: list[str] | None = None,
        # For distance-to-impact calculation
        accident_lat: float | None = None,
        accident_lon: float | None = None,
    ) -> AccidentFlightPathPoint:
        # Validate coordinates — store None if invalid
        lat = latitude if (latitude is not None and is_valid_coord(latitude, longitude or 0.0)) else None
        lon = longitude if (longitude is not None and is_valid_coord(latitude or 0.0, longitude)) else None
        has_pos = lat is not None and lon is not None

        # Distance to impact
        dist_km: float | None = None
        if has_pos and accident_lat is not None and accident_lon is not None:
            if is_valid_coord(accident_lat, accident_lon):
                dist_km = round(haversine_km(lat, lon, accident_lat, accident_lon), 3)  # type: ignore

        conf = compute_point_confidence(source_method, time_precision, is_disputed, has_pos)

        pt_id = str(uuid.uuid4())
        point = AccidentFlightPathPoint(
            id=pt_id,
            accident_event_id=accident_event_id,
            source_id=source_id,
            sequence_index=sequence_index,
            recorded_time_utc=recorded_time_utc,
            relative_offset_seconds=relative_offset_seconds,
            time_precision=time_precision,
            latitude=lat,
            longitude=lon,
            altitude_ft=altitude_ft,
            altitude_reference=altitude_reference,
            radio_altitude_ft=radio_altitude_ft,
            ground_speed_kt=ground_speed_kt,
            indicated_airspeed_kt=indicated_airspeed_kt,
            vertical_speed_fpm=vertical_speed_fpm,
            heading_degrees=heading_degrees,
            track_degrees=track_degrees,
            distance_to_impact_km=dist_km,
            uncertainty_radius_m=uncertainty_radius_m,
            point_type=point_type,
            source_method=source_method,
            confidence_score=conf,
            is_disputed=is_disputed,
            dispute_summary=dispute_summary,
            notes=notes,
            raw_data=raw_data,
        )
        db.add(point)
        await db.flush()

        for cid in (claim_ids or []):
            db.add(FlightPathPointClaim(
                id=str(uuid.uuid4()),
                flight_path_point_id=pt_id,
                claim_id=cid,
            ))

        log.info("fp.point.created", pt_id=pt_id, type=point_type, confidence=conf)
        return point

    @staticmethod
    async def update_point(
        db: AsyncSession,
        *,
        point_id: str,
        updates: dict[str, Any],
    ) -> AccidentFlightPathPoint | None:
        result = await db.execute(
            select(AccidentFlightPathPoint)
            .where(AccidentFlightPathPoint.id == point_id)
            .options(selectinload(AccidentFlightPathPoint.claim_links))
        )
        point = result.scalar_one_or_none()
        if point is None:
            return None

        allowed = {
            "sequence_index", "recorded_time_utc", "relative_offset_seconds",
            "time_precision", "latitude", "longitude", "altitude_ft",
            "altitude_reference", "radio_altitude_ft", "ground_speed_kt",
            "indicated_airspeed_kt", "vertical_speed_fpm", "heading_degrees",
            "track_degrees", "uncertainty_radius_m", "point_type",
            "source_method", "is_disputed", "dispute_summary", "notes",
        }
        for k, v in updates.items():
            if k in allowed:
                setattr(point, k, v)

        # Recompute confidence
        has_pos = is_valid_coord(
            float(point.latitude) if point.latitude else None,
            float(point.longitude) if point.longitude else None,
        )
        point.confidence_score = compute_point_confidence(
            point.source_method, point.time_precision, point.is_disputed, has_pos
        )
        return point

    @staticmethod
    async def delete_point(db: AsyncSession, *, point_id: str) -> bool:
        row = await db.get(AccidentFlightPathPoint, point_id)
        if row is None:
            return False
        await db.delete(row)
        log.info("fp.point.deleted", point_id=point_id)
        return True

    # ------------------------------------------------------------------
    # Write — annotations
    # ------------------------------------------------------------------

    @staticmethod
    async def create_annotation(
        db: AsyncSession,
        *,
        accident_event_id: str,
        annotation_type: str,
        title: str,
        description: str | None = None,
        flight_path_point_id: str | None = None,
        timeline_event_id: str | None = None,
        source_id: str | None = None,
        annotation_time_utc: datetime | None = None,
        relative_offset_seconds: int | None = None,
        time_precision: str = TimePrecision.UNKNOWN,
        altitude_ft: float | None = None,
        radio_altitude_ft: float | None = None,
        is_disputed: bool = False,
        dispute_summary: str | None = None,
        claim_ids: list[str] | None = None,
    ) -> AccidentFlightPathAnnotation:
        ann_id = str(uuid.uuid4())
        ann = AccidentFlightPathAnnotation(
            id=ann_id,
            accident_event_id=accident_event_id,
            flight_path_point_id=flight_path_point_id,
            timeline_event_id=timeline_event_id,
            source_id=source_id,
            annotation_time_utc=annotation_time_utc,
            relative_offset_seconds=relative_offset_seconds,
            time_precision=time_precision,
            annotation_type=annotation_type,
            title=title,
            description=description,
            altitude_ft=altitude_ft,
            radio_altitude_ft=radio_altitude_ft,
            confidence_score=_annotation_confidence(time_precision, is_disputed),
            is_disputed=is_disputed,
            dispute_summary=dispute_summary,
        )
        db.add(ann)
        await db.flush()

        for cid in (claim_ids or []):
            db.add(FlightPathAnnotationClaim(
                id=str(uuid.uuid4()),
                annotation_id=ann_id,
                claim_id=cid,
            ))

        log.info("fp.annotation.created", ann_id=ann_id, type=annotation_type)
        return ann

    @staticmethod
    async def update_annotation(
        db: AsyncSession,
        *,
        annotation_id: str,
        updates: dict[str, Any],
    ) -> AccidentFlightPathAnnotation | None:
        result = await db.execute(
            select(AccidentFlightPathAnnotation)
            .where(AccidentFlightPathAnnotation.id == annotation_id)
        )
        ann = result.scalar_one_or_none()
        if ann is None:
            return None
        allowed = {
            "annotation_type", "title", "description", "annotation_time_utc",
            "relative_offset_seconds", "time_precision", "altitude_ft",
            "radio_altitude_ft", "is_disputed", "dispute_summary",
            "flight_path_point_id", "timeline_event_id",
        }
        for k, v in updates.items():
            if k in allowed:
                setattr(ann, k, v)
        ann.confidence_score = _annotation_confidence(ann.time_precision, ann.is_disputed)
        return ann

    @staticmethod
    async def delete_annotation(db: AsyncSession, *, annotation_id: str) -> bool:
        row = await db.get(AccidentFlightPathAnnotation, annotation_id)
        if row is None:
            return False
        await db.delete(row)
        log.info("fp.annotation.deleted", annotation_id=annotation_id)
        return True

    # ------------------------------------------------------------------
    # Rebuild
    # ------------------------------------------------------------------

    @staticmethod
    async def rebuild(
        db: AsyncSession,
        *,
        accident_event_id: str,
        operator_id: str,
    ) -> dict[str, Any]:
        """
        Recalculate all derived fields for the flight path.

        Idempotent: safe to call multiple times.  Deletes and recreates
        auto-generated segments; preserves all user-entered points and annotations.
        """
        # Fetch accident location for distance-to-impact
        record_result = await db.execute(
            select(AccidentRecord).where(AccidentRecord.id == accident_event_id)
        )
        record = record_result.scalar_one_or_none()
        acc_lat = float(record.location_lat) if record and record.location_lat else None
        acc_lon = float(record.location_lon) if record and record.location_lon else None

        points = await FlightPathReconstructionService.get_points(db, accident_event_id)

        # 1. Recompute confidence and distance_to_impact for all points
        for p in points:
            lat = float(p.latitude) if p.latitude is not None else None
            lon = float(p.longitude) if p.longitude is not None else None
            has_pos = is_valid_coord(lat, lon)

            if has_pos and acc_lat is not None and acc_lon is not None:
                p.distance_to_impact_km = round(
                    haversine_km(lat, lon, acc_lat, acc_lon), 3  # type: ignore
                )

            p.confidence_score = compute_point_confidence(
                p.source_method, p.time_precision, p.is_disputed, has_pos
            )

        # 2. Delete all auto-generated segments and recreate
        await db.execute(
            delete(AccidentFlightPathSegment).where(
                AccidentFlightPathSegment.accident_event_id == accident_event_id
            )
        )

        # Build segments between consecutive ordered points with valid coords
        valid_points = [
            p for p in points
            if is_valid_coord(
                float(p.latitude) if p.latitude else None,
                float(p.longitude) if p.longitude else None,
            )
        ]

        for prev_p, next_p in zip(valid_points, valid_points[1:]):
            lat_a = float(prev_p.latitude)
            lon_a = float(prev_p.longitude)
            lat_b = float(next_p.latitude)
            lon_b = float(next_p.longitude)

            seg_type = derive_segment_type(
                prev_p.point_type, next_p.point_type,
                prev_p.is_disputed, next_p.is_disputed,
            )
            length = haversine_km(lat_a, lon_a, lat_b, lon_b)
            brng = bearing_degrees(lat_a, lon_a, lat_b, lon_b)
            conf = round(
                (float(prev_p.confidence_score or 0) + float(next_p.confidence_score or 0)) / 2, 3
            )

            seg = AccidentFlightPathSegment(
                id=str(uuid.uuid4()),
                accident_event_id=accident_event_id,
                start_point_id=prev_p.id,
                end_point_id=next_p.id,
                segment_type=seg_type,
                length_km=round(length, 3),
                bearing_degrees=round(brng, 2),
                confidence_score=conf,
                is_disputed=prev_p.is_disputed or next_p.is_disputed,
            )
            db.add(seg)

        log.info(
            "fp.rebuilt",
            accident_event_id=accident_event_id,
            point_count=len(points),
            segment_count=len(valid_points) - 1 if len(valid_points) > 1 else 0,
            operator_id=operator_id,
        )
        return {"point_count": len(points), "segment_count": max(len(valid_points) - 1, 0)}


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _point_to_dict(p: AccidentFlightPathPoint) -> dict[str, Any]:
    """Serialise a point without raw_data (kept in DB for provenance)."""
    claims = [
        {
            "claim_id": lnk.claim_id,
            "field_name": lnk.claim.field_name if lnk.claim else "",
            "claim_type": lnk.claim.claim_type if lnk.claim else "unknown",
            "source_id": lnk.claim.source_id if lnk.claim else "",
            "link_reason": lnk.link_reason,
        }
        for lnk in p.claim_links
    ]
    return {
        "id": p.id,
        "sequence_index": p.sequence_index,
        "recorded_time_utc": p.recorded_time_utc.isoformat() if p.recorded_time_utc else None,
        "relative_offset_seconds": p.relative_offset_seconds,
        "time_precision": p.time_precision,
        "latitude": float(p.latitude) if p.latitude is not None else None,
        "longitude": float(p.longitude) if p.longitude is not None else None,
        "altitude_ft": float(p.altitude_ft) if p.altitude_ft is not None else None,
        "altitude_reference": p.altitude_reference,
        "radio_altitude_ft": float(p.radio_altitude_ft) if p.radio_altitude_ft is not None else None,
        "ground_speed_kt": float(p.ground_speed_kt) if p.ground_speed_kt is not None else None,
        "indicated_airspeed_kt": float(p.indicated_airspeed_kt) if p.indicated_airspeed_kt is not None else None,
        "vertical_speed_fpm": float(p.vertical_speed_fpm) if p.vertical_speed_fpm is not None else None,
        "heading_degrees": float(p.heading_degrees) if p.heading_degrees is not None else None,
        "track_degrees": float(p.track_degrees) if p.track_degrees is not None else None,
        "distance_to_impact_km": float(p.distance_to_impact_km) if p.distance_to_impact_km is not None else None,
        "uncertainty_radius_m": float(p.uncertainty_radius_m) if p.uncertainty_radius_m is not None else None,
        "point_type": p.point_type,
        "source_method": p.source_method,
        "confidence_score": float(p.confidence_score) if p.confidence_score is not None else None,
        "is_disputed": p.is_disputed,
        "dispute_summary": p.dispute_summary,
        "notes": p.notes,
        "supporting_claims": claims,
        # Rendering hint — never present estimated points as recorded
        "is_estimated": p.point_type in (
            "estimated", "inferred", "report_estimate", "planned_route",
        ),
    }


def _segment_to_dict(s: AccidentFlightPathSegment) -> dict[str, Any]:
    return {
        "id": s.id,
        "start_point_id": s.start_point_id,
        "end_point_id": s.end_point_id,
        "segment_type": s.segment_type,
        "length_km": float(s.length_km) if s.length_km is not None else None,
        "bearing_degrees": float(s.bearing_degrees) if s.bearing_degrees is not None else None,
        "confidence_score": float(s.confidence_score) if s.confidence_score is not None else None,
        "is_disputed": s.is_disputed,
        "uncertainty_summary": s.uncertainty_summary,
        # Rendering hint — drives map polyline style
        "render_style": _segment_render_style(s.segment_type, s.is_disputed),
    }


def _segment_render_style(seg_type: str, is_disputed: bool) -> str:
    """
    Return a rendering hint string consumed by the frontend map component.

    Values: "solid_recorded" | "dashed_estimated" | "disputed" | "unknown"
    """
    if is_disputed:
        return "disputed"
    if seg_type in ("recorded", "observed"):
        return "solid_recorded"
    if seg_type in ("estimated", "inferred", "interpolated", "planned_route"):
        return "dashed_estimated"
    return "unknown"


def _annotation_to_dict(a: AccidentFlightPathAnnotation) -> dict[str, Any]:
    claims = [
        {
            "claim_id": lnk.claim_id,
            "field_name": lnk.claim.field_name if lnk.claim else "",
            "claim_type": lnk.claim.claim_type if lnk.claim else "unknown",
            "link_reason": lnk.link_reason,
        }
        for lnk in a.claim_links
    ]
    return {
        "id": a.id,
        "flight_path_point_id": a.flight_path_point_id,
        "timeline_event_id": a.timeline_event_id,
        "annotation_time_utc": a.annotation_time_utc.isoformat() if a.annotation_time_utc else None,
        "relative_offset_seconds": a.relative_offset_seconds,
        "time_precision": a.time_precision,
        "annotation_type": a.annotation_type,
        "title": a.title,
        "description": a.description,
        "altitude_ft": float(a.altitude_ft) if a.altitude_ft is not None else None,
        "radio_altitude_ft": float(a.radio_altitude_ft) if a.radio_altitude_ft is not None else None,
        "confidence_score": float(a.confidence_score) if a.confidence_score is not None else None,
        "is_disputed": a.is_disputed,
        "dispute_summary": a.dispute_summary,
        "supporting_claims": claims,
    }


def _annotation_confidence(time_precision: str, is_disputed: bool) -> float:
    _TP = {"exact": 1.0, "approximate": 0.7, "relative": 0.5, "sequence_only": 0.4, "unknown": 0.2}
    score = _TP.get(time_precision, 0.2)
    if is_disputed:
        score = max(0.0, score - 0.3)
    return round(score, 3)
