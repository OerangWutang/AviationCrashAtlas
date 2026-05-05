"""
Claim writer — fixes two bugs from the review:

Bug 1 (serialization): Raw Python types (datetime, etc.) were written
  directly into JSONB field_value. Now all values go through
  claim_value.encode() before insert.

Bug 2 (conflict detection): Conflict detection checked only
  Claim.is_winning == True, but is_winning is set by ProjectionService
  AFTER writing, so new claims always found zero conflicts. Fixed by
  comparing against all active (non-superseded, non-pending) same-field
  claims from other sources.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from atlas.models import claim_value as cv
from atlas.models.orm import Claim, ClaimConflict, ClaimHistory, ClaimType

log = structlog.get_logger(__name__)


class ClaimWriter:
    def __init__(
        self,
        session: AsyncSession,
        event_id: str,
        source_id: str,
        run_id: str | None = None,
    ) -> None:
        self._session = session
        self._event_id = event_id
        self._source_id = source_id
        self._run_id = run_id

    async def write_fields(
        self,
        fields: dict[str, Any],
        snapshot_id: str | None = None,
        claim_type: str = ClaimType.CONFIRMED.value,
        effective_at: Any = None,
    ) -> list[str]:
        """
        Write one Claim per field. All Python values are encoded via
        claim_value.encode() before touching the DB.

        Coordinates are merged from location_coordinates dict into one claim.
        Returns list of claim IDs written/updated.
        """
        claim_ids: list[str] = []

        for field_name, raw_value in fields.items():
            if cv.contains_envelope(raw_value):
                raise ValueError(
                    "ClaimWriter.write_fields expects raw Python values, not "
                    f"pre-encoded claim envelopes; field={field_name!r}"
                )

            # Encode Python type → JSON-safe envelope (fixes serialization bug).
            # This is the single encoding boundary for ingestion pipelines.
            envelope = cv.encode(raw_value)

            cid = await self._upsert_claim(
                field_name=field_name,
                envelope=envelope,
                snapshot_id=snapshot_id,
                claim_type=claim_type,
                effective_at=effective_at,
            )
            if cid:
                claim_ids.append(cid)

        log.info(
            "claims.written",
            event_id=self._event_id,
            source_id=self._source_id,
            count=len(claim_ids),
        )
        return claim_ids

    async def _upsert_claim(
        self,
        field_name: str,
        envelope: dict[str, Any],
        snapshot_id: str | None,
        claim_type: str,
        effective_at: Any,
    ) -> str | None:
        """
        Idempotent claim upsert:
          1. If identical claim from same source exists → skip
          2. If different value from same source → supersede old, write new
          3. After writing → detect conflicts against other-source active claims
        """
        stmt = (
            select(Claim)
            .where(
                Claim.event_id == self._event_id,
                Claim.source_id == self._source_id,
                Claim.field_name == field_name,
                Claim.claim_type != ClaimType.SUPERSEDED.value,
            )
            .order_by(Claim.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing is not None:
            if existing.field_value == envelope:
                return None  # Identical — idempotent skip

            # Different value from same source — supersede
            old_value = existing.field_value
            old_type = existing.claim_type
            existing.claim_type = ClaimType.SUPERSEDED.value
            existing.is_winning = False
            self._session.add(ClaimHistory(
                id=str(uuid.uuid4()),
                claim_id=existing.id,
                old_value=old_value,
                new_value=envelope,
                old_claim_type=old_type,
                new_claim_type=ClaimType.SUPERSEDED.value,
                change_reason=f"superseded_by_run:{self._run_id}",
                changed_by=self._run_id,
            ))
            # Mark any open conflicts involving the now-superseded claim as
            # obsolete.  The superseded claim is no longer active, so its
            # conflicts are no longer actionable.  If the new claim still
            # disagrees with another source, _detect_conflicts will create a
            # fresh open conflict for the new claim pair.
            await mark_conflicts_obsolete_for_claim(
                self._session,
                existing.id,
                reason=f"claim superseded by run:{self._run_id}",
            )

        claim_id = str(uuid.uuid4())
        new_claim = Claim(
            id=claim_id,
            event_id=self._event_id,
            source_id=self._source_id,
            snapshot_id=snapshot_id,
            field_name=field_name,
            field_value=envelope,
            claim_type=claim_type,
            effective_at=effective_at if not callable(effective_at) else None,
            is_winning=False,  # ProjectionService sets this, not the writer
        )
        self._session.add(new_claim)
        # Flush immediately so the new Claim row exists in the DB before
        # conflict insertion.  The session uses autoflush=False, which means
        # SQLAlchemy will NOT automatically flush before the raw pg_insert()
        # in _detect_conflicts.  Without this flush, claim_conflicts FK
        # references (claim_a_id / claim_b_id → claims.id) can fail with an
        # IntegrityError when new_claim.id is not yet persisted.
        # The flush also ensures _try_reconcile_disputed_claims sees the new
        # claim when querying active confirmed/inferred claims.
        await self._session.flush()

        # Conflict detection — fixed: compare against ALL active claims
        # from other sources, not just is_winning ones
        await self._detect_conflicts(new_claim, envelope, field_name)

        # After writing the new claim and detecting new conflicts, check whether
        # any remaining DISPUTED claims for this field can now be reconciled.
        # Example: A=4 and B=5 conflict → both DISPUTED. A is superseded by A2=5.
        # A's conflict is obsoleted above. A2 agrees with B, so no new conflict is
        # created. B remains DISPUTED forever unless we check here.
        # This call reinstates DISPUTED claims that have no remaining open conflicts.
        await self._try_reconcile_disputed_claims(field_name)

        return claim_id

    async def _try_reconcile_disputed_claims(self, field_name: str) -> None:
        """
        After writing a new claim and updating conflicts, check whether any
        DISPUTED claims for this event+field can be reinstated to CONFIRMED.

        A DISPUTED claim is reinstated only when ALL three conditions hold:
          1. No remaining OPEN conflicts reference it.
          2. Its value does not conflict with any current CONFIRMED or INFERRED
             claim for the same field (value-agreement check).
          3. None of its conflicts were manually resolved against it — i.e. the
             claim was not explicitly rejected via a resolution decision.

        Condition 2 is essential: absence of open conflicts alone is not enough.
        A conflict can become obsolete because a claim was superseded, but if
        the disputed claim's value still disagrees with the winning confirmed set
        it should not be silently reinstated.

        Condition 3 prevents reinstating a claim that a human operator deliberately
        rejected during manual conflict resolution.
        """
        # Step 1: Find all DISPUTED claims for this field
        r = await self._session.execute(
            select(Claim).where(
                Claim.event_id == self._event_id,
                Claim.field_name == field_name,
                Claim.claim_type == ClaimType.DISPUTED.value,
            )
        )
        disputed = list(r.scalars().all())
        if not disputed:
            return

        # Step 2: Load current CONFIRMED and INFERRED claims for value comparison
        r2 = await self._session.execute(
            select(Claim).where(
                Claim.event_id == self._event_id,
                Claim.field_name == field_name,
                Claim.claim_type.in_([
                    ClaimType.CONFIRMED.value,
                    ClaimType.INFERRED.value,
                ]),
            )
        )
        active_claims = list(r2.scalars().all())

        for claim in disputed:
            # Condition 1: no open conflicts
            open_conflict_r = await self._session.execute(
                select(ClaimConflict).where(
                    ClaimConflict.status == "open",
                    (ClaimConflict.claim_a_id == claim.id)
                    | (ClaimConflict.claim_b_id == claim.id),
                ).limit(1)
            )
            if open_conflict_r.scalar_one_or_none() is not None:
                continue  # Still has open conflicts — leave DISPUTED

            # Condition 3: not manually rejected.
            # A resolved conflict where accepted_claim_id is a different claim
            # means this claim was explicitly not chosen — do not auto-reinstate.
            # Legacy fallback: if resolution text exists but no structured fields,
            # treat any resolved conflict involving this claim as a rejection guard.
            rejected_r = await self._session.execute(
                select(ClaimConflict).where(
                    ClaimConflict.status == "resolved",
                    (ClaimConflict.claim_a_id == claim.id)
                    | (ClaimConflict.claim_b_id == claim.id),
                    # Either explicitly accepted a different claim ...
                    (ClaimConflict.accepted_claim_id.isnot(None) &
                     (ClaimConflict.accepted_claim_id != claim.id))
                    # ... or legacy resolution text with no structured fields
                    | (ClaimConflict.accepted_claim_id.is_(None) &
                       ClaimConflict.resolution.isnot(None)),
                ).limit(1)
            )
            if rejected_r.scalar_one_or_none() is not None:
                # A human resolved a conflict against this claim — do not
                # auto-reinstate. Manual resolution takes precedence.
                continue

            # Condition 2: value-agreement — must not conflict with any active claim
            conflicts_with_active = any(
                cv.values_conflict(claim.field_value, active.field_value, field_name)
                for active in active_claims
                if active.id != claim.id
            )
            if conflicts_with_active:
                continue  # Still disagrees with active claims — leave DISPUTED

            # All three conditions met — reinstate to original type.
            # Do NOT blindly upgrade to CONFIRMED: a claim that was originally
            # INFERRED should be restored to INFERRED, not promoted.
            # Recover original type from the ClaimHistory row that recorded the
            # DISPUTED transition — its old_claim_type is the pre-dispute type.
            original_type = ClaimType.CONFIRMED.value  # safe default
            history_r = await self._session.execute(
                select(ClaimHistory)
                .where(
                    ClaimHistory.claim_id == claim.id,
                    ClaimHistory.new_claim_type == ClaimType.DISPUTED.value,
                )
                .order_by(ClaimHistory.changed_at.asc())
                .limit(1)
            )
            first_dispute_entry = history_r.scalar_one_or_none()
            if first_dispute_entry and first_dispute_entry.old_claim_type in (
                ClaimType.CONFIRMED.value,
                ClaimType.INFERRED.value,
            ):
                original_type = first_dispute_entry.old_claim_type

            old_type = claim.claim_type
            claim.claim_type = original_type
            self._session.add(ClaimHistory(
                id=str(uuid.uuid4()),
                claim_id=claim.id,
                old_claim_type=old_type,
                new_claim_type=original_type,
                change_reason=f"reconciled_no_open_conflicts:run:{self._run_id}",
                changed_by=self._run_id,
            ))
            log.info(
                "claims.reconciled",
                event_id=self._event_id,
                field=field_name,
                claim_id=claim.id,
                source_id=claim.source_id,
            )


    async def _detect_conflicts(
        self,
        new_claim: Claim,
        new_envelope: dict[str, Any],
        field_name: str,
    ) -> None:
        """
        Detect conflicts between new_claim and all other active claims for
        this event+field from other sources.

        Checks CONFIRMED, INFERRED, and DISPUTED claims.  Including DISPUTED
        is critical: without it a third source can arrive after Sources A and B
        have been marked DISPUTED, find no eligible opponents, and project its
        own value unchallenged — even though the field has an unresolved
        conflict.  Any source that disagrees with a disputed value should itself
        become disputed until the conflict is resolved.
        """
        stmt = select(Claim).where(
            Claim.event_id == self._event_id,
            Claim.field_name == field_name,
            Claim.source_id != self._source_id,
            Claim.claim_type.in_([
                ClaimType.CONFIRMED.value,
                ClaimType.INFERRED.value,
                ClaimType.DISPUTED.value,   # include disputed — see docstring
            ]),
        )
        result = await self._session.execute(stmt)
        others = result.scalars().all()

        # Capture the original type once — on the second conflicting claim the
        # new_claim will already be DISPUTED, so capturing inside the loop
        # would produce a misleading DISPUTED→DISPUTED history entry.
        original_new_type = new_claim.claim_type

        for other in others:
            if cv.values_conflict(new_envelope, other.field_value, field_name):
                # Canonicalise ordering so (A,B) and (B,A) are the same row —
                # required by uq_conflict_claim_pair unique constraint.
                cid_a, cid_b = sorted([new_claim.id, other.id])
                # Use ON CONFLICT DO NOTHING + RETURNING so we know whether the
                # row was actually inserted or skipped on a retry.  This lets us
                # gate history writes behind real state changes (below).
                conflict_result = await self._session.execute(
                    pg_insert(ClaimConflict.__table__).values(
                        id=str(uuid.uuid4()),
                        event_id=self._event_id,
                        field_name=field_name,
                        claim_a_id=cid_a,
                        claim_b_id=cid_b,
                        status="open",
                    ).on_conflict_do_nothing(constraint="uq_conflict_claim_pair")
                    .returning(ClaimConflict.__table__.c.id)
                )
                conflict_inserted = conflict_result.fetchone() is not None

                # Only write ClaimHistory when the claim type *actually* changes.
                # On a retry the conflict row is skipped (ON CONFLICT DO NOTHING)
                # and the claims are already DISPUTED — writing history again
                # would create duplicate "confirmed → disputed" audit entries.
                if new_claim.claim_type != ClaimType.DISPUTED.value:
                    new_claim.claim_type = ClaimType.DISPUTED.value
                    self._session.add(ClaimHistory(
                        id=str(uuid.uuid4()),
                        claim_id=new_claim.id,
                        old_claim_type=original_new_type,
                        new_claim_type=ClaimType.DISPUTED.value,
                        change_reason=f"conflict_with:{other.id}",
                        changed_by=self._run_id,
                    ))

                if other.claim_type != ClaimType.DISPUTED.value:
                    old_other_type = other.claim_type
                    other.claim_type = ClaimType.DISPUTED.value
                    self._session.add(ClaimHistory(
                        id=str(uuid.uuid4()),
                        claim_id=other.id,
                        old_claim_type=old_other_type,
                        new_claim_type=ClaimType.DISPUTED.value,
                        change_reason=f"conflict_with:{new_claim.id}",
                        changed_by=self._run_id,
                    ))

                if conflict_inserted:
                    log.warning(
                        "claims.conflict",
                        event_id=self._event_id,
                        field=field_name,
                        source_a=self._source_id,
                        source_b=other.source_id,
                    )


async def mark_conflicts_obsolete_for_claim(
    session: AsyncSession,
    claim_id: str,
    reason: str,
) -> int:
    """
    Mark all open conflicts involving claim_id as obsolete.

    Called when a claim is superseded — conflicts that referenced the
    now-superseded claim are no longer actionable and should not affect
    has_conflicts or block projection for the event.

    Returns the number of conflict rows updated.
    """
    result = await session.execute(
        update(ClaimConflict)
        .where(
            ClaimConflict.status == "open",
            (ClaimConflict.claim_a_id == claim_id)
            | (ClaimConflict.claim_b_id == claim_id),
        )
        .values(
            status="obsolete",
            obsolete_reason=reason,
            obsolete_at=datetime.now(tz=UTC),
        )
        .returning(ClaimConflict.id)
    )
    rows = result.fetchall()
    if rows:
        log.info(
            "conflicts.obsoleted",
            claim_id=claim_id,
            count=len(rows),
            reason=reason,
        )
    return len(rows)
