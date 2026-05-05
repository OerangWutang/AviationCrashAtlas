"""
Projection service.

Fixes from review:
1. is_winning flag is now persisted back to Claim rows after selection.
   Previously the UI showed zero "active claims" because is_winning was
   never set. Now the cycle is:
     - Clear all is_winning for this event
     - Select winner per field
     - Set winner.is_winning = True
     - Write accident_record projection

2. confidence_breakdown is stored in accident_records.confidence_breakdown
   so the /provenance endpoint can return the full factor list.

3. location_lat / location_lon are stored as plain floats alongside the
   PostGIS geometry, so the API can return them in JSON responses and
   the map can use real coordinates (not hardcoded mock ones).

v20: per-field projection_explanations and aggregate document_status
are now stored on the projection row.  The frontend uses these to
explain "why is this displayed?" and to drive the document chip on the
evidence bar — without inventing reasons or guessing aggregates.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

import structlog
from shapely.geometry import Point
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from atlas.confidence.engine import ConfidenceEngine
from atlas.models import claim_value as cv
from atlas.models.orm import (
    AccidentEvent,
    AccidentRecord,
    Claim,
    ClaimConflict,
    ClaimHistory,
    ClaimType,
    Source,
    SourceDocument,
)

log = structlog.get_logger(__name__)


# Documented selection_reason codes.  Keep in sync with the frontend's
# humanizeSelectionReason() switch.  Adding a new code here without
# updating the frontend is fine — unknown codes pass through with
# underscores replaced by spaces.
class SelectionReason:
    ONLY_ACTIVE_CLAIM = "only_active_claim"
    SELECTED_OFFICIAL_FINAL = "selected_official_final"
    SELECTED_LATEST_OFFICIAL = "selected_latest_official"
    SELECTED_HIGHER_TIER = "selected_higher_tier"
    WITHHELD_OPEN_DISPUTE = "withheld_open_dispute"
    WITHHELD_NO_ACTIVE_CLAIM = "withheld_no_active_claim"
    APPROXIMATE_NEAREST_CITY_ONLY = "approximate_nearest_city_only"


# Claim types eligible to become a projection winner or to count as supporting
# evidence in explanations.  PENDING (unreviewed), DISPUTED (contradicted),
# REJECTED (operator-rejected), and SUPERSEDED (replaced) all fail this gate —
# the projection refuses to display values that aren't backed by an active
# confirmed/inferred claim.  Centralising this set keeps _select_winners and
# _build_explanations from drifting apart.
_ELIGIBLE_CLAIM_TYPES = frozenset({
    ClaimType.CONFIRMED.value,
    ClaimType.INFERRED.value,
})


class ProjectionService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._confidence = ConfidenceEngine(session)

    async def rebuild_event(self, event_id: str) -> None:
        """
        Full projection rebuild for one event.
        Steps:
          1. Clear all is_winning flags for this event
          2. Load all active claims and pick winners per field
          3. Mark winning claims is_winning=True
          4. Compute confidence score + breakdown
          5. Build per-field projection explanations (v20)
          6. Compute aggregate document_status (v20)
          7. Upsert accident_records row with breakdown stored
          8. Update event.overall_confidence_score
        """
        # Step 1: clear all winning flags
        await self._session.execute(
            update(Claim)
            .where(Claim.event_id == event_id)
            .values(is_winning=False)
        )

        # Step 2: load active claims
        r = await self._session.execute(
            select(Claim).where(
                Claim.event_id == event_id,
                Claim.claim_type != ClaimType.SUPERSEDED.value,
            )
        )
        claims = list(r.scalars().all())

        # Step 3: load sources — must happen before winner selection so tier
        # can be used as part of the priority key.
        source_ids = list({c.source_id for c in claims})
        if source_ids:
            r2 = await self._session.execute(select(Source).where(Source.id.in_(source_ids)))
            sources = {s.id: s for s in r2.scalars().all()}
        else:
            sources = {}

        # Step 4: pick winners and load conflicts.
        # Conflicts must be loaded before marking is_winning so we can exclude
        # winners for fields that are withheld due to open conflicts.
        winners = self._select_winners(claims, sources)

        # Load conflicts (needed for record build, explanations, and is_winning)
        r3 = await self._session.execute(
            select(ClaimConflict).where(ClaimConflict.event_id == event_id)
        )
        conflicts = list(r3.scalars().all())

        # Fields with any open conflict — their values are withheld by
        # _build_record().  Claims for these fields must NOT be marked
        # is_winning because the projected record does not show their value.
        # Marking them is_winning would imply they are authoritative when
        # the UI is actually withholding the field.
        open_conflict_fields: set[str] = {
            cf.field_name
            for cf in conflicts
            if getattr(cf, "status", None) == "open"
            or (getattr(cf, "status", None) is None and cf.resolution is None)
        }

        # projected_winners: winners whose fields are actually displayed.
        # Used for is_winning, source_ids (winning_source_count), and
        # primary_source_id — all of which should reflect what the UI shows.
        projected_winners = {
            field: claim
            for field, claim in winners.items()
            if field not in open_conflict_fields
        }

        for claim in projected_winners.values():
            claim.is_winning = True

        # Step 5: confidence score + stored breakdown
        score, breakdown = await self._confidence.score_event(event_id)

        # Step 6: load source documents before building explanations.
        # Final-report provenance is stricter than source tier: tier 1 means
        # official, but "selected from official final report" must only be
        # emitted when a verified final-report document is actually linked for
        # the selected source.
        r4 = await self._session.execute(
            select(SourceDocument).where(SourceDocument.event_id == event_id)
        )
        docs = list(r4.scalars().all())

        # Step 7: per-field projection explanations
        explanations = self._build_explanations(
            claims=claims,
            winners=winners,
            sources=sources,
            conflicts=conflicts,
            source_documents=docs,
        )

        # Step 8: aggregate document_status
        document_status = self._aggregate_document_status(docs)

        # Build record
        record_data = self._build_record(
            event_id=event_id,
            claims=claims,
            winners=winners,
            sources=sources,
            conflicts=conflicts,
            score=score,
            breakdown=breakdown.to_dict(),
            explanations=explanations,
            document_status=document_status,
        )

        stmt = pg_insert(AccidentRecord.__table__).values(**record_data)
        stmt = stmt.on_conflict_do_update(
            index_elements=["id"],
            set_={k: v for k, v in record_data.items() if k != "id"},
        )
        await self._session.execute(stmt)

        # Step 8: update event-level score
        event = await self._session.get(AccidentEvent, event_id)
        if event:
            event.overall_confidence_score = score
            event.updated_at = datetime.now(tz=UTC)

        log.info("projection.rebuilt", event_id=event_id, score=round(score, 3))

    async def rebuild_all(self, batch_size: int = 200) -> tuple[int, int]:
        """
        Rebuild projections for all **active** events in keyset-paginated batches.

        Returns (rebuilt, failed).  The caller can distinguish a clean run
        (failed==0) from a partial run and surface failures appropriately instead
        of printing a cheerful success message regardless of errors.

        Only AccidentEvent rows with record_status == 'active' are processed.
        Merged, disputed, and retracted events are intentionally skipped — their
        projection rows are either superseded or no longer meaningful.
        """
        rebuilt = 0
        failed = 0
        last_id: str | None = None
        batch_num = 0

        while True:
            stmt = (
                select(AccidentEvent.id)
                .where(AccidentEvent.record_status == "active")   # ← was missing
                .order_by(AccidentEvent.id)
                .limit(batch_size)
            )
            if last_id is not None:
                stmt = stmt.where(AccidentEvent.id > last_id)

            result = await self._session.execute(stmt)
            ids = [row[0] for row in result.all()]
            if not ids:
                break

            batch_num += 1
            for eid in ids:
                try:
                    await self.rebuild_event(eid)
                    rebuilt += 1
                except Exception:
                    log.exception("projection.error", event_id=eid)
                    failed += 1

            last_id = ids[-1]
            await self._session.commit()
            log.info(
                "projection.batch",
                batch=batch_num,
                done=rebuilt,
                failed=failed,
                last_id=last_id,
            )

        log.info("projection.rebuild_all.complete", rebuilt=rebuilt, failed=failed)
        return rebuilt, failed

    async def finalize_accepted_claims_for_field(
        self,
        event_id: str,
        field_name: str,
        resolved_by: str,
    ) -> None:
        """
        After any conflict resolution on event_id/field_name, check whether
        the field is now fully settled and restore any previously-accepted-but-
        deferred claims to their pre-dispute type.

        This addresses the deferred-restoration edge case: when A-vs-B was
        resolved (A accepted) but A-vs-C was still open, the accepted claim
        was deliberately not restored at that time.  When A-vs-C is later
        resolved, this method runs and unblocks A.

        Algorithm:
        1. If any open conflicts remain for this field, do nothing — the field
           is still in dispute and no claim should be projected.
        2. Otherwise, collect all resolved conflicts for this field and find
           every accepted_claim_id.  For each such claim that is still DISPUTED,
           restore it to its pre-dispute type (via ClaimHistory).

        Called by the resolve_conflict endpoint after every successful resolution,
        regardless of resolution_type.  This is cheap (field-scoped) and
        idempotent — running it when no claims need restoration is a no-op.
        """
        # Step 1: abort if any open conflicts remain.
        open_r = await self._session.execute(
            select(ClaimConflict).where(
                ClaimConflict.event_id == event_id,
                ClaimConflict.field_name == field_name,
                ClaimConflict.status == "open",
            ).limit(1)
        )
        if open_r.scalar_one_or_none() is not None:
            return  # Field is still in dispute — nothing to do.

        # Step 2: collect accepted_claim_ids from all resolved conflicts on
        # this field.  A claim may appear in multiple resolved conflicts
        # (e.g. accepted in A-vs-B and again in A-vs-C).
        resolved_r = await self._session.execute(
            select(ClaimConflict).where(
                ClaimConflict.event_id == event_id,
                ClaimConflict.field_name == field_name,
                ClaimConflict.status == "resolved",
            )
        )
        resolved_conflicts = list(resolved_r.scalars().all())
        accepted_ids: set[str] = {
            cf.accepted_claim_id
            for cf in resolved_conflicts
            if cf.accepted_claim_id is not None
        }
        rejected_ids: set[str] = {
            claim_id
            for cf in resolved_conflicts
            if cf.rejected_claim_ids
            for claim_id in cf.rejected_claim_ids
        }

        if not accepted_ids:
            return  # No claim was explicitly accepted — nothing to restore.

        # Step 2a: contradiction detection.
        # A claim that was accepted in one conflict but rejected in another is
        # contradictory — reviewers disagree about which source is authoritative.
        # Projecting in this case would silently pick one side; instead, keep
        # the field withheld and log a warning so the contradiction is visible.
        contradictions = accepted_ids & rejected_ids
        if contradictions:
            log.warning(
                "projection.field_finalized.contradictory_resolutions",
                event_id=event_id,
                field_name=field_name,
                contradicted_claim_ids=sorted(contradictions),
                message=(
                    "Field finalization aborted: one or more claims were accepted "
                    "in one conflict but rejected in another. Resolve the "
                    "contradiction manually before the field can be projected."
                ),
            )
            return

        # Multiple distinct accepted claims — ambiguous; keep field withheld.
        if len(accepted_ids) > 1:
            log.warning(
                "projection.field_finalized.ambiguous_multiple_accepted",
                event_id=event_id,
                field_name=field_name,
                accepted_claim_ids=sorted(accepted_ids),
                message=(
                    "Field finalization aborted: multiple distinct claims were "
                    "accepted across different conflicts. Exactly one accepted "
                    "claim is required for unambiguous projection."
                ),
            )
            return

        # Step 3: restore the single accepted claim if still DISPUTED.
        # Skip REJECTED claims — they were explicitly discarded by a reviewer
        # and must never be restored regardless of other resolution decisions.
        for claim_id in accepted_ids:
            claim: Claim | None = await self._session.get(Claim, claim_id)
            if claim is None:
                continue
            if claim.claim_type == ClaimType.REJECTED.value:
                continue   # permanently excluded — never restore
            if claim.claim_type != ClaimType.DISPUTED.value:
                continue  # Already restored or not found.

            # Recover pre-dispute type from ClaimHistory.
            history_r = await self._session.execute(
                select(ClaimHistory)
                .where(
                    ClaimHistory.claim_id == claim_id,
                    ClaimHistory.new_claim_type == ClaimType.DISPUTED.value,
                )
                .order_by(ClaimHistory.changed_at.asc())
                .limit(1)
            )
            first_dispute = history_r.scalar_one_or_none()
            restore_type = (
                first_dispute.old_claim_type
                if first_dispute and first_dispute.old_claim_type in _ELIGIBLE_CLAIM_TYPES
                else ClaimType.CONFIRMED.value
            )
            old_type = claim.claim_type
            claim.claim_type = restore_type
            self._session.add(ClaimHistory(
                id=str(uuid.uuid4()),
                claim_id=claim.id,
                old_claim_type=old_type,
                new_claim_type=restore_type,
                change_reason=f"field_finalized:{event_id}:{field_name}",
                changed_by=resolved_by,
            ))
            log.info(
                "projection.field_finalized.claim_restored",
                event_id=event_id,
                field_name=field_name,
                claim_id=claim_id,
                old_type=old_type,
                restore_type=restore_type,
            )

    def _select_winners(self, claims: list[Claim], sources: dict[str, Any]) -> dict[str, Claim]:
        """
        For each field, pick the highest-priority eligible claim.

        Only CONFIRMED and INFERRED claims are eligible to become winners.
        PENDING means unreviewed — it must never be silently projected into
        the read model.  DISPUTED is also excluded: if the only claim for a
        field is disputed, the field is omitted from the projection and the
        UI must surface the dispute rather than silently showing one side.

        Priority (ascending = better):
          1. claim_type: confirmed(0) > inferred(1)
          2. source tier: tier 1 (official) beats tier 4 (unverified)
          3. recency: newer wins on equal type+tier
        """
        by_field: dict[str, list[Claim]] = {}
        for c in claims:
            if c.claim_type in _ELIGIBLE_CLAIM_TYPES:
                by_field.setdefault(c.field_name, []).append(c)

        _priority = {
            ClaimType.CONFIRMED.value: 0,
            ClaimType.INFERRED.value: 1,
        }
        winners: dict[str, Claim] = {}
        for field_name, field_claims in by_field.items():
            sorted_claims = sorted(
                field_claims,
                key=lambda c: (
                    _priority.get(c.claim_type, 9),
                    sources.get(c.source_id).tier if c.source_id in sources else 99,
                    -(c.created_at.timestamp() if c.created_at else 0),
                ),
            )
            winners[field_name] = sorted_claims[0]
        return winners

    def _build_explanations(
        self,
        *,
        claims: list[Claim],
        winners: dict[str, Claim],
        sources: dict[str, Source],
        conflicts: list[ClaimConflict],
        source_documents: list[SourceDocument] | None = None,
    ) -> list[dict[str, Any]]:
        """
        One explanation row per field with at least one non-superseded
        claim or one open conflict.  Reasons are picked from a small set
        of documented codes — never free-form.
        """
        # Group claims by field for counts
        claims_by_field: dict[str, list[Claim]] = {}
        for c in claims:
            claims_by_field.setdefault(c.field_name, []).append(c)

        # Open conflicts by field
        open_by_field: dict[str, list[ClaimConflict]] = {}
        for cf in conflicts:
            if getattr(cf, "status", None) == "open":
                open_by_field.setdefault(cf.field_name, []).append(cf)

        # Union of fields we should explain
        fields = set(claims_by_field) | set(open_by_field)
        out: list[dict[str, Any]] = []
        for field_name in sorted(fields):
            field_claims = claims_by_field.get(field_name, [])
            field_open_conflicts = open_by_field.get(field_name, [])
            winner = winners.get(field_name)
            disputed_count = sum(
                1 for c in field_claims if c.claim_type == ClaimType.DISPUTED.value
            )
            supporting_count = sum(
                1 for c in field_claims if c.claim_type in _ELIGIBLE_CLAIM_TYPES
            )

            # Reason resolution.  Order matters: an open conflict means
            # we MUST report withheld even if a winner happens to exist
            # via a parallel non-conflicting claim, because the user-
            # visible "displayed value" is the conflict-aware one.
            reason: str
            displayed: Any = None
            selected_claim_id: str | None = None
            selected_source_id: str | None = None
            source_rank: int | None = None

            if field_open_conflicts:
                reason = SelectionReason.WITHHELD_OPEN_DISPUTE
            elif winner is None:
                reason = SelectionReason.WITHHELD_NO_ACTIVE_CLAIM
            else:
                # Winner exists: pick the most specific reason.
                src = sources.get(winner.source_id)
                source_rank = src.tier if src else None
                selected_claim_id = winner.id
                selected_source_id = winner.source_id
                try:
                    displayed = cv.decode(winner.field_value)
                except Exception:
                    displayed = None

                # Decide reason based on what the winner has and what
                # the alternatives looked like.
                competing = [
                    c for c in field_claims
                    if c.id != winner.id and c.claim_type in _ELIGIBLE_CLAIM_TYPES
                ]
                if not competing:
                    reason = SelectionReason.ONLY_ACTIVE_CLAIM
                else:
                    # If any competing claim came from a higher-tier
                    # (lower number) source, our winner is *not* the
                    # higher-tier one — fall back to recency reason.
                    competing_tiers = [
                        sources[c.source_id].tier
                        for c in competing if c.source_id in sources
                    ]
                    if (
                        source_rank is not None
                        and competing_tiers
                        and source_rank < min(competing_tiers)
                    ):
                        reason = SelectionReason.SELECTED_HIGHER_TIER
                    elif self._is_official_final(
                        winner,
                        src,
                        source_documents=source_documents or [],
                    ):
                        reason = SelectionReason.SELECTED_OFFICIAL_FINAL
                    else:
                        reason = SelectionReason.SELECTED_LATEST_OFFICIAL

                # Special-case location_text: NTSB CSV gives only
                # nearest-city granularity, so even when there is a
                # confident winner, the value is approximate.  This is
                # what the frontend already labels as "Approximate" in
                # the location group; we surface the same fact through
                # the structured explanation.
                if (
                    field_name == "location_text"
                    and not any(
                        c.field_name == "location_coordinates"
                        and c.is_winning
                        for c in claims
                    )
                ):
                    reason = SelectionReason.APPROXIMATE_NEAREST_CITY_ONLY

            out.append({
                "field_name": field_name,
                "displayed_value": _jsonable(displayed),
                "selected_claim_id": selected_claim_id,
                "selected_source_id": selected_source_id,
                "source_rank": source_rank,
                "selection_reason": reason,
                "has_open_conflict": bool(field_open_conflicts),
                "supporting_claim_count": supporting_count,
                "disputed_claim_count": disputed_count,
            })
        return out

    @staticmethod
    def _is_official_final(
        claim: Claim,
        src: Source | None,
        *,
        source_documents: list[SourceDocument],
    ) -> bool:
        """
        Return True only when the selected claim comes from an official
        source *and* the event has a verified, available final-report
        document from that same source.

        Source tier alone is not enough.  Tier 1 means the source is
        official; it does not prove the investigation is final or that a
        final report has been linked and verified.  Without document-level
        evidence of finality, projection explanations must use the weaker
        `selected_latest_official` reason.
        """
        if src is None or src.tier != 1:
            return False

        final_document_types = {"final", "final_report"}
        for document in source_documents:
            if document.source_id != claim.source_id:
                continue
            if document.document_type not in final_document_types:
                continue
            if document.url_verified is True and document.is_available is True:
                return True
        return False

    @staticmethod
    def _aggregate_document_status(docs: list[SourceDocument]) -> str:
        """Mirror of the frontend's deriveDocumentStatus, but authoritative.

        Returns one of:
            'none_linked' | 'linked_unverified' | 'verified'
            | 'unavailable' | 'mixed'
        """
        if not docs:
            return "none_linked"
        verified = 0
        unavailable = 0
        for d in docs:
            if d.url_verified and d.is_available:
                verified += 1
            if d.is_available is False:
                unavailable += 1
        if verified == len(docs):
            return "verified"
        if unavailable == len(docs):
            return "unavailable"
        if verified > 0 or unavailable > 0:
            return "mixed"
        return "linked_unverified"

    def _build_record(
        self,
        event_id: str,
        claims: list[Claim],
        winners: dict[str, Claim],
        sources: dict[str, Source],
        conflicts: list[ClaimConflict],
        score: float,
        breakdown: dict[str, Any],
        # v20 additions — defaulted so older callers (notably the unit
        # test suite which constructs minimal projections) keep working.
        # Defaults match the "no v20 data computed" interpretation: an
        # empty explanations list and the conservative "none_linked"
        # document state.
        explanations: list[dict[str, Any]] | None = None,
        document_status: str = "none_linked",
    ) -> dict[str, Any]:
        # Fields with any open conflict — their values must be withheld
        # from the projection regardless of what _select_winners() found.
        # This makes _build_record() authoritative and consistent with
        # _build_explanations(), which already reports WITHHELD_OPEN_DISPUTE
        # for such fields.  Without this check a field could have a winner
        # (e.g. an accepted claim that was just restored to CONFIRMED) while
        # another open conflict for the same field still exists — the field
        # would be projected when it should still be withheld.
        open_conflict_fields: set[str] = {
            cf.field_name
            for cf in conflicts
            if getattr(cf, "status", None) == "open"
            or (getattr(cf, "status", None) is None and cf.resolution is None)
        }

        def get(field: str) -> Any:
            if field in open_conflict_fields:
                return None   # Withhold: at least one open conflict covers this field
            c = winners.get(field)
            if c is None:
                return None
            try:
                return cv.decode(c.field_value)
            except Exception:
                return None

        occurred_at = get("occurred_at")
        occurred_at_precision = get("occurred_at_precision")
        coords = get("location_coordinates")

        # Extract lat/lon as plain floats for API JSON responses
        lat: float | None = None
        lon: float | None = None
        location_point = None
        if isinstance(coords, dict):
            try:
                lat = float(coords["latitude"])
                lon = float(coords["longitude"])
                location_point = f"SRID=4326;{Point(lon, lat).wkt}"
            except (KeyError, ValueError, TypeError):
                pass

        # source_ids: sources behind *projected* (non-withheld) field values only.
        # claim_source_ids: all sources that contributed any non-superseded claim.
        # These diverge when a source contributes only losing or disputed claims.
        # Excluding withheld fields from source_ids keeps winning_source_count
        # consistent with what the projection actually displays.
        projected_winner_source_ids = list({
            c.source_id
            for field, c in winners.items()
            if field not in open_conflict_fields
        })
        claim_source_ids = list({c.source_id for c in claims})  # all non-superseded
        primary_source_id = min(
            (sources[sid] for sid in projected_winner_source_ids if sid in sources),
            key=lambda s: s.tier,
            default=None,
        )

        # has_conflicts is true only for open (unresolved, non-obsolete) conflicts.
        # Resolved or obsolete conflicts no longer block projection trust.
        has_conflicts = any(
            getattr(c, "status", None) == "open"
            or (getattr(c, "status", None) is None and c.resolution is None)
            for c in conflicts
        )

        return {
            "id": event_id,
            "occurred_at": occurred_at,
            "occurred_date": occurred_at.date() if isinstance(occurred_at, datetime) else None,
            "occurred_year": occurred_at.year if isinstance(occurred_at, datetime) else None,
            "occurred_at_precision": occurred_at_precision,
            "location_point": location_point,
            "location_lat": lat,
            "location_lon": lon,
            "location_text": get("location_text"),
            "country_code": get("country_code"),
            "state_code": get("state_code"),
            "aircraft_make": get("aircraft_make"),
            "aircraft_model": get("aircraft_model"),
            "aircraft_registration": get("aircraft_registration"),
            "aircraft_amateur_built": get("aircraft_amateur_built"),
            "operator_name": get("operator_name"),
            "phase_of_flight": get("phase_of_flight"),
            "purpose_of_flight": get("purpose_of_flight"),
            "weather_condition": get("weather_condition"),
            "injury_severity": get("injury_severity"),
            "fatalities_total": get("fatalities_total"),
            "fatalities_crew": get("fatalities_crew"),
            "fatalities_passengers": get("fatalities_passengers"),
            "serious_injuries": get("serious_injuries"),
            "serious_injuries_crew": get("serious_injuries_crew"),
            "serious_injuries_passengers": get("serious_injuries_passengers"),
            "minor_injuries": get("minor_injuries"),
            "minor_injuries_crew": get("minor_injuries_crew"),
            "minor_injuries_passengers": get("minor_injuries_passengers"),
            "uninjured_crew": get("uninjured_crew"),
            "uninjured_passengers": get("uninjured_passengers"),
            "aboard_total": get("aboard_total"),
            "aircraft_damage": get("aircraft_damage"),
            "investigation_status": get("investigation_status"),
            "probable_cause": get("probable_cause"),
            "ntsb_report_number": get("ntsb_report_number"),
            "source_ids": projected_winner_source_ids,  # projected sources only (for winning_source_count)
            "claim_source_ids": claim_source_ids, # all contributing sources (for claim_source_count)
            "primary_source_id": primary_source_id.id if primary_source_id else None,
            "confidence_score": score,
            "confidence_breakdown": breakdown,  # stored so API can expose it
            "has_conflicts": has_conflicts,
            "projection_explanations": explanations or [],
            "document_status": document_status,
            "last_projected_at": datetime.now(tz=UTC),
        }


def _jsonable(value: Any) -> Any:
    """Make a decoded claim value safe to store in a JSONB column.

    Decoded values may include datetime / date instances (from
    claim_value.decode); JSONB serialises strings, numbers, booleans,
    None, lists, and dicts cleanly.  We coerce other types to ISO
    strings so the explanation row remains useful for the frontend.
    """
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    return value
