"""Full ingestion pipeline: fetch → snapshot → state → claims → docs → project.

v20 changes vs. v19
-------------------
1. Both run_ntsb_api() and run_ntsb_csv() now create and finalize an
   IngestionRun row symmetrically.  Failed runs are recorded as
   `failed`; partial runs (some errors but progress made) are recorded
   as `completed` with `ingestion_errors > 0`.  v19 only persisted runs
   for CSV ingestion.

2. Every _process call now updates source_record_state, the rolling
   per-(source, source_record_id) state.  This is the table that
   answers "when did we last see this record?" and "did its content
   actually change?" — orthogonal to raw_snapshots which is the
   immutable archive.

3. Hash-unchanged re-ingest no longer silently does nothing.  We now
   emit a `source_record_unchanged` revision so the timeline shows
   that we still see the record, plus we bump last_seen_at.

4. Hash-changed re-ingest computes the field-set diff between the
   previous canonical extraction and the new one, and emits
   `source_field_added` / `source_field_removed` revisions where
   appropriate.  This is the "removed/retracted upstream" signal the
   v20 prompt explicitly asks for.

5. Source documents extracted from the raw payload are persisted
   (Step 11).  The extractor only uses real URL fields plus a single
   deterministic CAROL search URL — it does not fabricate
   investigation-page URLs.

6. A `projection_rebuilt` revision is emitted at the end of each
   _process to make local rebuilds visible in the timeline (and
   distinct from real source-side changes).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from atlas.claims.projection import ProjectionService
from atlas.claims.writer import ClaimWriter
from atlas.db.engine import direct_session
from atlas.ingestion import revisions as rev
from atlas.ingestion.deduplicator import DuplicateDetector
from atlas.ingestion.document_extractor import extract_documents_from_ntsb
from atlas.ingestion.generic_csv_adapter import (
    SourceMapping,
    build_generic_snapshot,
    load_csv_with_mapping,
    normalise_generic,
)
from atlas.ingestion.normalizer import build_canonical_fields
from atlas.ingestion.ntsb_adapter import (
    NTSB_SOURCE_ID,
    NTSBAdapter,
    build_snapshot,
    compute_payload_hash,
    load_from_csv,
)
from atlas.models.orm import (
    AccidentEvent,
    AccidentRecord,
    ClaimType,
    DataQualityIssue,
    DatePrecision,
    DuplicateCandidateReview,
    EventExternalId,
    IngestionRun,
    RawSnapshot,
    RecordStatus,
    SourceDocument,
    SourceRecordState,
)

log = structlog.get_logger(__name__)

# Bump when build_canonical_fields() changes its extraction in a way that
# affects the canonical output for unchanged raw input.  Comparing this
# against source_record_state.parser_version lets the pipeline detect
# "old extraction but new parser" and re-extract even on hash-unchanged
# re-ingest.  Stored as a string so non-integer schemes (e.g. "2.1") are
# allowed in the future.
PARSER_VERSION = "1"

# Short label used when emitting NTSB revisions.  Kept in sync with the
# Source.short_name seeded by the migration.
_NTSB_SHORT_NAME = "NTSB"


@dataclass
class IngestionResult:
    run_id: str
    source: str
    started_at: datetime
    completed_at: datetime | None = None
    records_fetched: int = 0
    snapshots_new: int = 0
    snapshots_skipped: int = 0
    events_created: int = 0
    events_updated: int = 0
    claims_written: int = 0
    documents_created: int = 0
    revisions_emitted: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return not self.errors


@dataclass
class _GenericMatchDecision:
    auto_event: AccidentEvent | None = None
    review_candidates: list[dict[str, Any]] = field(default_factory=list)


# ── Run-record helpers (symmetric for API and CSV) ────────────────────────────

async def _create_run_record(
    *,
    run_id: str,
    source_name: str,
    source_id: str | None,
    started_at: datetime,
    records_fetched: int,
) -> None:
    """Persist a `running` run row at the start of the ingestion."""
    async with direct_session() as session:
        session.add(IngestionRun(
            id=run_id,
            source_id=source_id,
            source_name=source_name,
            status="running",
            started_at=started_at,
            records_fetched=records_fetched,
        ))
        await session.commit()


async def _finalize_run_record(
    *,
    result: IngestionResult,
    source_id: str | None,
) -> None:
    """Update the run row with terminal status and counters."""
    async with direct_session() as session:
        run_obj = await session.get(IngestionRun, result.run_id)
        if run_obj is None:
            # Should not happen — _create_run_record runs unconditionally
            # at the start. Insert a minimal row so the operational
            # ledger stays complete instead of missing.
            run_obj = IngestionRun(
                id=result.run_id,
                source_id=source_id,
                source_name=result.source,
                status="failed",
                started_at=result.started_at,
            )
            session.add(run_obj)

        run_obj.status = "failed" if (result.errors and result.snapshots_new == 0) \
            else "completed"
        run_obj.completed_at = result.completed_at
        run_obj.records_fetched = result.records_fetched
        run_obj.snapshots_new = result.snapshots_new
        run_obj.snapshots_skipped = result.snapshots_skipped
        run_obj.events_created = result.events_created
        run_obj.events_updated = result.events_updated
        run_obj.claims_written = result.claims_written
        run_obj.ingestion_errors = len(result.errors)
        run_obj.errors = result.errors or None
        await session.commit()


class IngestionPipeline:
    async def run_ntsb_api(self, start: date, end: date) -> IngestionResult:
        run_id = str(uuid.uuid4())
        started_at = datetime.now(tz=UTC)
        result = IngestionResult(run_id=run_id, source="NTSB_API", started_at=started_at)

        # Persist a running row immediately so operators see in-progress runs
        # AND so a fetch-time crash still leaves a row to investigate.
        await _create_run_record(
            run_id=run_id,
            source_name="NTSB_API",
            source_id=NTSB_SOURCE_ID,
            started_at=started_at,
            records_fetched=0,
        )

        try:
            async with NTSBAdapter() as adapter:
                try:
                    raw_records = await adapter.fetch_date_range(start, end)
                except Exception as exc:
                    # Record the error on the result; the outer finally clause
                    # will set completed_at (if still None) and call
                    # _finalize_run_record exactly once. Calling it here too
                    # would double-write the run row.
                    result.errors.append(str(exc))
                    return result

            result.records_fetched = len(raw_records)
            async with direct_session() as session:
                for raw in raw_records:
                    try:
                        await self._process(raw, session, result, run_id)
                    except Exception as exc:
                        result.errors.append(f"{raw.get('EventId','?')}: {exc}")
                        await session.rollback()

            result.completed_at = datetime.now(tz=UTC)
            return result
        finally:
            # Always finalize, even if an unanticipated exception escaped.
            if result.completed_at is None:
                result.completed_at = datetime.now(tz=UTC)
            await _finalize_run_record(result=result, source_id=NTSB_SOURCE_ID)


    async def run_generic_csv(
        self, filepath: str, mapping: SourceMapping, dry_run: bool = False
    ) -> IngestionResult:
        """Ingest any CSV source using a SourceMapping.

        dry_run=True parses and normalises without writing to the DB —
        useful for validating a new mapping before committing.
        """
        run_id = str(uuid.uuid4())
        started_at = datetime.now(tz=UTC)
        source_id = mapping.source_id
        result = IngestionResult(
            run_id=run_id,
            source=f"GENERIC_CSV:{source_id}",
            started_at=started_at,
        )

        try:
            raw_records = load_csv_with_mapping(filepath, mapping)
        except Exception as exc:
            result.errors.append(f"csv_load: {exc}")
            result.completed_at = datetime.now(tz=UTC)
            if not dry_run:
                await _create_run_record(
                    run_id=run_id, source_name=result.source, source_id=source_id,
                    started_at=started_at, records_fetched=0,
                )
                await _finalize_run_record(result=result, source_id=source_id)
            return result

        result.records_fetched = len(raw_records)

        if dry_run:
            for raw in raw_records:
                try:
                    normalise_generic(raw.get("__canonical__", {}))
                except Exception as exc:
                    result.errors.append(str(exc))
            result.completed_at = datetime.now(tz=UTC)
            log.info("generic_csv.dry_run", records=result.records_fetched, errors=len(result.errors))
            return result

        await _create_run_record(
            run_id=run_id, source_name=result.source, source_id=source_id,
            started_at=started_at, records_fetched=len(raw_records),
        )
        try:
            async with direct_session() as session:
                for raw in raw_records:
                    try:
                        await self._process_generic(raw, session, result, run_id, mapping)
                    except Exception as exc:
                        result.errors.append(str(exc))
                        await session.rollback()
            result.completed_at = datetime.now(tz=UTC)
            return result
        finally:
            if result.completed_at is None:
                result.completed_at = datetime.now(tz=UTC)
            await _finalize_run_record(result=result, source_id=source_id)

    async def _process_generic(
        self,
        raw: dict[str, Any],
        session: AsyncSession,
        result: IngestionResult,
        run_id: str,
        mapping: SourceMapping,
    ) -> None:
        """Process a single generic-CSV row."""
        source_id = mapping.source_id
        record_id = raw.get("__record_id__") or None
        canonical_raw: dict[str, Any] = raw.get("__canonical__", {})

        payload_str = json.dumps(raw, sort_keys=True, default=str)
        h = hashlib.sha256(payload_str.encode()).hexdigest()

        prior = (await session.execute(
            select(RawSnapshot).where(
                RawSnapshot.source_id == source_id,
                RawSnapshot.payload_hash == h,
            ).limit(1)
        )).scalar_one_or_none()
        if prior is not None:
            result.snapshots_skipped += 1
            await session.commit()
            return

        canonical = normalise_generic(canonical_raw)
        if not canonical:
            await session.commit()
            return

        snap = build_generic_snapshot(
            raw, source_id=source_id, source_record_id=record_id, run_id=run_id,
        )
        session.add(snap)
        result.snapshots_new += 1

        src_tag = source_id.split("-")[1].upper() if "-" in source_id else source_id[:4].upper()
        canonical_id = f"{src_tag}-{record_id}" if record_id else f"{src_tag}-{snap.id[:8]}"

        # Cross-source event matching.  High-confidence matches are attached to
        # the existing event so claims can conflict on the same record.
        # Medium-confidence matches create reviewable duplicate candidates
        # instead of silently merging.
        match_decision = await self._match_existing_event_for_generic(
            session, canonical, source_id=source_id, source_record_id=record_id,
        )
        event = match_decision.auto_event

        if event is None:
            event = (await session.execute(
                select(AccidentEvent).where(AccidentEvent.canonical_id == canonical_id).limit(1)
            )).scalar_one_or_none()

        is_first = event is None
        if event is None:
            event = AccidentEvent(
                id=str(uuid.uuid4()), canonical_id=canonical_id,
                record_status=RecordStatus.ACTIVE.value,
                occurred_at=canonical.get("occurred_at"),
                occurred_at_precision=canonical.get(
                    "occurred_at_precision", DatePrecision.DAY.value
                ),
                location_text=canonical.get("location_text"),
                country_code=canonical.get("country_code"),
            )
            session.add(event)
            result.events_created += 1
        else:
            result.events_updated += 1

        await session.flush()

        if record_id:
            await self._link_external_id(
                session, event_id=event.id, source_id=source_id,
                external_id=str(record_id), external_id_type="source_record_id",
            )

        if match_decision.review_candidates:
            await self._record_duplicate_candidates(
                session,
                source_event_id=event.id,
                source_id=source_id,
                source_record_id=record_id,
                ingestion_run_id=run_id,
                candidates=match_decision.review_candidates,
            )

        writer = ClaimWriter(session, event.id, source_id, run_id)
        ids = await writer.write_fields(
            canonical, snapshot_id=snap.id,
            claim_type=ClaimType.CONFIRMED.value, effective_at=None,
        )
        result.claims_written += len(ids)
        await self._emit_consistency_issues(
            session, event_id=event.id, source_id=source_id, canonical=canonical,
        )

        if is_first:
            rev.emit_source_record_first_seen(
                session, event_id=event.id, source_id=source_id,
                source_record_id=record_id or "", snapshot_id=snap.id,
                ingestion_run_id=run_id, source_short_name=src_tag,
            )
        else:
            rev.emit_source_snapshot_changed(
                session, event_id=event.id, source_id=source_id,
                source_record_id=record_id or "", snapshot_id=snap.id,
                ingestion_run_id=run_id, changed_fields=list(canonical.keys()),
                source_short_name=src_tag,
            )
        result.revisions_emitted += 1

        await ProjectionService(session).rebuild_event(event.id)
        rev.emit_projection_rebuilt(session, event_id=event.id, ingestion_run_id=run_id)
        result.revisions_emitted += 1
        await session.commit()


    async def _match_existing_event_for_generic(
        self,
        session: AsyncSession,
        canonical: dict[str, Any],
        *,
        source_id: str,
        source_record_id: str | None,
    ) -> _GenericMatchDecision:
        """Return an auto-match or review candidates for a generic row.

        Matching is deliberately conservative:
        - exact external IDs win immediately when available;
        - exact registration + exact date wins only if unique;
        - fuzzy/spatial candidates above the duplicate detector threshold are
          stored for reviewer confirmation unless the score is high enough and
          unambiguous.
        """
        decision = _GenericMatchDecision()

        # Existing source external IDs are the most deterministic match signal.
        if source_record_id:
            external_match = (await session.execute(
                select(AccidentEvent)
                .join(EventExternalId, EventExternalId.event_id == AccidentEvent.id)
                .where(
                    EventExternalId.source_id == source_id,
                    EventExternalId.external_id_type == "source_record_id",
                    EventExternalId.external_id == str(source_record_id),
                    AccidentEvent.record_status == RecordStatus.ACTIVE.value,
                )
                .limit(1)
            )).scalar_one_or_none()
            if external_match is not None:
                decision.auto_event = external_match
                return decision

        registration = canonical.get("aircraft_registration")
        occurred_at = canonical.get("occurred_at")
        occurred_date = getattr(occurred_at, "date", lambda: None)() if occurred_at else None

        # Regression-proof high-precision match: same registration and same date.
        if registration and occurred_date:
            rows = (await session.execute(
                select(AccidentEvent)
                .join(AccidentRecord, AccidentRecord.id == AccidentEvent.id)
                .where(
                    AccidentEvent.record_status == RecordStatus.ACTIVE.value,
                    AccidentRecord.aircraft_registration == str(registration).upper(),
                    AccidentRecord.occurred_date == occurred_date,
                )
                .order_by(AccidentEvent.created_at.asc(), AccidentEvent.id.asc())
                .limit(2)
            )).scalars().all()
            if len(rows) == 1:
                decision.auto_event = rows[0]
                return decision

        # Fuzzy review candidates from a small date-window candidate set.
        if occurred_date is None:
            return decision

        date_min = occurred_date - timedelta(days=7)
        date_max = occurred_date + timedelta(days=7)
        candidate_rows = (await session.execute(
            select(AccidentEvent, AccidentRecord)
            .join(AccidentRecord, AccidentRecord.id == AccidentEvent.id)
            .where(
                AccidentEvent.record_status == RecordStatus.ACTIVE.value,
                AccidentRecord.occurred_date >= date_min,
                AccidentRecord.occurred_date <= date_max,
            )
            .order_by(AccidentRecord.occurred_date.asc().nullsfirst(), AccidentEvent.id.asc())
            .limit(100)
        )).all()

        incoming = self._duplicate_dict_from_canonical(canonical)
        incoming["event_id"] = f"incoming:{source_id}:{source_record_id or 'unknown'}"
        existing = [self._duplicate_dict_from_record(event, record) for event, record in candidate_rows]
        candidates = DuplicateDetector().find_candidates(incoming, existing)
        if not candidates:
            return decision

        best = candidates[0]
        # High-confidence, unambiguous fuzzy match: auto-attach.
        if best.match_score >= 0.90 and (len(candidates) == 1 or best.match_score - candidates[1].match_score >= 0.10):
            decision.auto_event = next(
                event for event, _record in candidate_rows if event.id == best.event_id_b
            )
            return decision

        # Medium candidates become review tasks.
        for c in candidates[:5]:
            if c.match_score >= 0.50:
                decision.review_candidates.append({
                    "candidate_event_id": c.event_id_b,
                    "match_type": c.match_type,
                    "match_score": c.match_score,
                    "match_reasons": c.match_fields,
                })
        return decision

    def _duplicate_dict_from_canonical(self, canonical: dict[str, Any]) -> dict[str, Any]:
        occurred_at = canonical.get("occurred_at")
        coords = canonical.get("location_coordinates") or {}
        return {
            "occurred_at": occurred_at.date() if hasattr(occurred_at, "date") else occurred_at,
            "latitude": canonical.get("location_lat") or coords.get("latitude"),
            "longitude": canonical.get("location_lon") or coords.get("longitude"),
            "aircraft_registration": canonical.get("aircraft_registration"),
            "aircraft_make": canonical.get("aircraft_make"),
            "aircraft_model": canonical.get("aircraft_model"),
            "operator_name": canonical.get("operator_name"),
            "location_text": canonical.get("location_text"),
            "fatalities_total": canonical.get("fatalities_total"),
        }

    def _duplicate_dict_from_record(self, event: AccidentEvent, record: AccidentRecord) -> dict[str, Any]:
        return {
            "event_id": event.id,
            "occurred_at": record.occurred_date,
            "latitude": float(record.location_lat) if record.location_lat is not None else None,
            "longitude": float(record.location_lon) if record.location_lon is not None else None,
            "aircraft_registration": record.aircraft_registration,
            "aircraft_make": record.aircraft_make,
            "aircraft_model": record.aircraft_model,
            "operator_name": record.operator_name,
            "location_text": record.location_text,
            "fatalities_total": record.fatalities_total,
        }

    async def _record_duplicate_candidates(
        self,
        session: AsyncSession,
        *,
        source_event_id: str,
        source_id: str,
        source_record_id: str | None,
        ingestion_run_id: str,
        candidates: list[dict[str, Any]],
    ) -> None:
        for c in candidates:
            existing = (await session.execute(
                select(DuplicateCandidateReview).where(
                    DuplicateCandidateReview.source_event_id == source_event_id,
                    DuplicateCandidateReview.candidate_event_id == c["candidate_event_id"],
                ).limit(1)
            )).scalar_one_or_none()
            if existing is not None:
                continue
            session.add(DuplicateCandidateReview(
                id=str(uuid.uuid4()),
                source_event_id=source_event_id,
                candidate_event_id=c["candidate_event_id"],
                source_id=source_id,
                source_record_id=source_record_id,
                ingestion_run_id=ingestion_run_id,
                match_type=c.get("match_type", "fuzzy"),
                match_score=c.get("match_score", 0.0),
                match_reasons=c.get("match_reasons") or [],
                status="pending",
            ))

    async def _link_external_id(
        self,
        session: AsyncSession,
        *,
        event_id: str,
        source_id: str,
        external_id: str,
        external_id_type: str,
    ) -> None:
        existing = (await session.execute(
            select(EventExternalId).where(
                EventExternalId.source_id == source_id,
                EventExternalId.external_id_type == external_id_type,
                EventExternalId.external_id == external_id,
            ).limit(1)
        )).scalar_one_or_none()
        if existing is None:
            session.add(EventExternalId(
                id=str(uuid.uuid4()),
                event_id=event_id,
                source_id=source_id,
                external_id_type=external_id_type,
                external_id=external_id,
            ))
        else:
            existing.event_id = event_id

    async def _emit_consistency_issues(
        self,
        session: AsyncSession,
        *,
        event_id: str,
        source_id: str,
        canonical: dict[str, Any],
    ) -> None:
        checks = [
            ("fatalities_total", "fatalities_crew", "fatalities_passengers"),
            ("serious_injuries", "serious_injuries_crew", "serious_injuries_passengers"),
            ("minor_injuries", "minor_injuries_crew", "minor_injuries_passengers"),
        ]
        for total_field, crew_field, pax_field in checks:
            total = canonical.get(total_field)
            crew = canonical.get(crew_field)
            pax = canonical.get(pax_field)
            if total is None or crew is None or pax is None:
                continue
            if int(total) == int(crew) + int(pax):
                continue
            existing = (await session.execute(
                select(DataQualityIssue).where(
                    DataQualityIssue.event_id == event_id,
                    DataQualityIssue.issue_code == "split_total_mismatch",
                    DataQualityIssue.field_name == total_field,
                    DataQualityIssue.status == "open",
                ).limit(1)
            )).scalar_one_or_none()
            details = {
                "total_field": total_field,
                "total": int(total),
                "crew_field": crew_field,
                "crew": int(crew),
                "passenger_field": pax_field,
                "passengers": int(pax),
                "expected_total": int(crew) + int(pax),
            }
            if existing is None:
                session.add(DataQualityIssue(
                    id=str(uuid.uuid4()),
                    event_id=event_id,
                    source_id=source_id,
                    issue_code="split_total_mismatch",
                    field_name=total_field,
                    severity="warning",
                    status="open",
                    details=details,
                ))
            else:
                existing.details = details

    async def run_ntsb_csv(self, filepath: str) -> IngestionResult:
        run_id = str(uuid.uuid4())
        started_at = datetime.now(tz=UTC)
        result = IngestionResult(run_id=run_id, source="NTSB_CSV", started_at=started_at)

        try:
            raw_records = await load_from_csv(filepath)
        except Exception as exc:
            # File-load failure: persist a failed run so this is still
            # visible in the operational ledger.
            result.errors.append(f"csv_load: {exc}")
            result.completed_at = datetime.now(tz=UTC)
            await _create_run_record(
                run_id=run_id,
                source_name="NTSB_CSV",
                source_id=NTSB_SOURCE_ID,
                started_at=started_at,
                records_fetched=0,
            )
            await _finalize_run_record(result=result, source_id=NTSB_SOURCE_ID)
            return result

        result.records_fetched = len(raw_records)
        await _create_run_record(
            run_id=run_id,
            source_name="NTSB_CSV",
            source_id=NTSB_SOURCE_ID,
            started_at=started_at,
            records_fetched=len(raw_records),
        )

        try:
            async with direct_session() as session:
                for raw in raw_records:
                    try:
                        await self._process(raw, session, result, run_id)
                    except Exception as exc:
                        result.errors.append(str(exc))
                        await session.rollback()
            result.completed_at = datetime.now(tz=UTC)
            return result
        finally:
            if result.completed_at is None:
                result.completed_at = datetime.now(tz=UTC)
            await _finalize_run_record(result=result, source_id=NTSB_SOURCE_ID)

    # ── Per-record processing ────────────────────────────────────────────

    async def _process(
        self,
        raw: dict[str, Any],
        session: AsyncSession,
        result: IngestionResult,
        run_id: str,
    ) -> None:
        h = compute_payload_hash(raw)
        event_id_raw = (raw.get("EventId") or "").strip()

        # Prior state for this (source, source_record_id), if any.
        prior_state: SourceRecordState | None = None
        if event_id_raw:
            prior_state = (await session.execute(
                select(SourceRecordState).where(
                    SourceRecordState.source_id == NTSB_SOURCE_ID,
                    SourceRecordState.source_record_id == event_id_raw,
                ).limit(1)
            )).scalar_one_or_none()

        # ── Branch A: hash unchanged ──────────────────────────────────────
        # We've already archived an identical raw payload for this source.
        # raw_snapshots stays unchanged (it's immutable) — but the rolling
        # state and the timeline both want to know that we re-saw this
        # record without a content change.
        prior_snapshot = (await session.execute(
            select(RawSnapshot).where(
                RawSnapshot.source_id == NTSB_SOURCE_ID,
                RawSnapshot.payload_hash == h,
            ).limit(1)
        )).scalar_one_or_none()

        if prior_snapshot is not None:
            result.snapshots_skipped += 1
            now = datetime.now(tz=UTC)
            if prior_state is not None:
                prior_state.last_seen_at = now
                # If the parser version moved on since the previous
                # extraction, the canonical claims may be out of date even
                # though the raw payload is byte-identical.  We do NOT
                # automatically re-extract here (that would risk
                # duplicating ingestion work for every hash-unchanged
                # record on a deploy that bumped the parser); we just
                # record the parser drift for an explicit `atlas
                # reproject` to act on.  parser_version on the row stays
                # at its old value so the drift remains visible.
                if prior_state.event_id is not None:
                    rev.emit_source_record_unchanged(
                        session,
                        event_id=prior_state.event_id,
                        source_id=NTSB_SOURCE_ID,
                        source_record_id=event_id_raw or "",
                        ingestion_run_id=run_id,
                        source_short_name=_NTSB_SHORT_NAME,
                    )
                    result.revisions_emitted += 1
            await session.commit()
            return

        # ── Branch B: new content (first-seen or hash-changed) ───────────
        snap = build_snapshot(raw, source_record_id=event_id_raw or None, run_id=run_id)
        session.add(snap)
        result.snapshots_new += 1

        canonical = build_canonical_fields(raw)
        if not canonical:
            # No usable canonical fields — keep the snapshot in the
            # archive but do not progress to event creation / claims.
            await session.commit()
            return

        canonical_id = f"NTSB-{event_id_raw}" if event_id_raw else f"NTSB-{snap.id[:8]}"
        event = (await session.execute(
            select(AccidentEvent).where(AccidentEvent.canonical_id == canonical_id).limit(1)
        )).scalar_one_or_none()

        is_first_event = event is None
        if event is None:
            event = AccidentEvent(
                id=str(uuid.uuid4()),
                canonical_id=canonical_id,
                record_status=RecordStatus.ACTIVE.value,
                occurred_at=canonical.get("occurred_at"),
                occurred_at_precision=canonical.get(
                    "occurred_at_precision", DatePrecision.DAY.value),
                location_text=canonical.get("location_text"),
                country_code=canonical.get("country_code"),
            )
            session.add(event)
            result.events_created += 1
        else:
            result.events_updated += 1

        await session.flush()

        # ── Field-set diff against previous canonical extraction ──────────
        new_fields = sorted(canonical.keys())
        prior_fields: list[str] = (
            list(prior_state.current_field_names or [])
            if prior_state is not None else []
        )
        added_fields = sorted(set(new_fields) - set(prior_fields))
        removed_fields = sorted(set(prior_fields) - set(new_fields))

        # ── Source-record state: upsert ──────────────────────────────────
        now = datetime.now(tz=UTC)
        if prior_state is None and event_id_raw:
            session.add(SourceRecordState(
                source_id=NTSB_SOURCE_ID,
                source_record_id=event_id_raw,
                event_id=event.id,
                first_seen_at=now,
                last_seen_at=now,
                last_changed_at=now,
                current_payload_hash=h,
                current_snapshot_id=snap.id,
                previous_payload_hash=None,
                parser_version=PARSER_VERSION,
                current_field_names=new_fields,
            ))
        elif prior_state is not None:
            prior_state.event_id = event.id
            prior_state.last_seen_at = now
            prior_state.last_changed_at = now
            prior_state.previous_payload_hash = prior_state.current_payload_hash
            prior_state.current_payload_hash = h
            prior_state.current_snapshot_id = snap.id
            prior_state.parser_version = PARSER_VERSION
            prior_state.current_field_names = new_fields

        # ── Claims ───────────────────────────────────────────────────────
        ct = ClaimType.CONFIRMED.value
        writer = ClaimWriter(session, event.id, NTSB_SOURCE_ID, run_id)
        ids = await writer.write_fields(
            canonical, snapshot_id=snap.id, claim_type=ct,
            effective_at=None,
        )
        result.claims_written += len(ids)
        await self._emit_consistency_issues(
            session, event_id=event.id, source_id=NTSB_SOURCE_ID, canonical=canonical,
        )

        # ── Source documents ─────────────────────────────────────────────
        # Documents are stable per source/url — re-running ingestion
        # should not produce duplicates.  We dedupe by (event_id,
        # source_id, url) before insert.
        candidates = extract_documents_from_ntsb(raw)
        if candidates:
            existing_urls = set((await session.execute(
                select(SourceDocument.url).where(
                    SourceDocument.event_id == event.id,
                    SourceDocument.source_id == NTSB_SOURCE_ID,
                )
            )).scalars().all())
            for c in candidates:
                if c.url in existing_urls:
                    continue
                doc_id = str(uuid.uuid4())
                pub_date = None
                if c.published_at:
                    try:
                        pub_date = date.fromisoformat(c.published_at)
                    except ValueError:
                        pub_date = None
                doc = SourceDocument(
                    id=doc_id,
                    event_id=event.id,
                    source_id=NTSB_SOURCE_ID,
                    document_type=c.document_type,
                    url=c.url,
                    url_verified=False,   # never set on construction
                    title=c.title,
                    published_at=pub_date,
                    is_available=None,    # not yet checked
                )
                session.add(doc)
                result.documents_created += 1
                rev.emit_source_document_linked(
                    session,
                    event_id=event.id,
                    source_id=NTSB_SOURCE_ID,
                    source_document_id=doc_id,
                    ingestion_run_id=run_id,
                    document_type=c.document_type,
                    title=c.title,
                )
                result.revisions_emitted += 1
                existing_urls.add(c.url)

        # ── Timeline revisions ───────────────────────────────────────────
        if is_first_event or prior_state is None:
            rev.emit_source_record_first_seen(
                session,
                event_id=event.id,
                source_id=NTSB_SOURCE_ID,
                source_record_id=event_id_raw or "",
                snapshot_id=snap.id,
                ingestion_run_id=run_id,
                source_short_name=_NTSB_SHORT_NAME,
            )
            result.revisions_emitted += 1
        else:
            rev.emit_source_snapshot_changed(
                session,
                event_id=event.id,
                source_id=NTSB_SOURCE_ID,
                source_record_id=event_id_raw or "",
                snapshot_id=snap.id,
                ingestion_run_id=run_id,
                changed_fields=new_fields,
                source_short_name=_NTSB_SHORT_NAME,
            )
            result.revisions_emitted += 1

        if added_fields:
            rev.emit_source_field_added(
                session,
                event_id=event.id,
                source_id=NTSB_SOURCE_ID,
                snapshot_id=snap.id,
                ingestion_run_id=run_id,
                fields=added_fields,
                source_short_name=_NTSB_SHORT_NAME,
            )
            result.revisions_emitted += 1
        if removed_fields:
            rev.emit_source_field_removed(
                session,
                event_id=event.id,
                source_id=NTSB_SOURCE_ID,
                snapshot_id=snap.id,
                ingestion_run_id=run_id,
                fields=removed_fields,
                source_short_name=_NTSB_SHORT_NAME,
            )
            result.revisions_emitted += 1

        # ── Projection ───────────────────────────────────────────────────
        await ProjectionService(session).rebuild_event(event.id)
        rev.emit_projection_rebuilt(
            session,
            event_id=event.id,
            ingestion_run_id=run_id,
        )
        result.revisions_emitted += 1
        await session.commit()
