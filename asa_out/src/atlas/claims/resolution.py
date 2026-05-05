"""
ConflictResolutionService

Owns every invariant involved in resolving a pairwise claim conflict:

  1. Validate the request against the conflict row
  2. Apply per-resolution_type logic (require/forbid fields, auto-derive survivor)
  3. Transition conflict status → resolved (with optimistic locking)
  4. Mark rejected claims as REJECTED, write ClaimHistory rows
  5. Call ProjectionService.finalize_accepted_claims_for_field()
  6. Trigger ProjectionService.rebuild_event()

The FastAPI route handler becomes a thin adapter:
  - parse request body (Pydantic)
  - authenticate operator (auth dependency)
  - call ConflictResolutionService.resolve()
  - return ConflictOut

Having the logic here instead of in the route makes it:
  - testable without FastAPI machinery
  - reusable by CLI / batch repair scripts
  - easy to reason about in isolation

Invariants enforced
-------------------
- Conflict must exist.
- Conflict must be open (status == "open"); 409 otherwise.
- accepted_claim_id must belong to the conflict's pair when provided.
- rejected_claim_ids must only reference claims in the conflict's pair.
- accepted_claim_id must not appear in rejected_claim_ids.
- For claim_accepted: accepted_claim_id is required.
- For claim_rejected: rejected_claim_ids is required; survivor auto-derived.
- Rejected claims are set to ClaimType.REJECTED (not left as DISPUTED).
- Claim restoration only happens once all open conflicts on the field are closed.
- Contradictory resolutions (same claim accepted+rejected) are detected and block
  restoration with a warning log.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from atlas.claims.projection import ProjectionService
from atlas.models.orm import Claim, ClaimConflict, ClaimHistory, ClaimType

log = structlog.get_logger(__name__)

# Canonical resolution types — kept here to avoid importing from app.py.
ResolutionType = Literal[
    "claim_accepted",
    "claim_rejected",
    "claims_merged",
    "source_corrected",
    "not_applicable",
    "manual_override",
]


class ConflictNotFoundError(Exception):
    pass


class ConflictAlreadyResolvedError(Exception):
    def __init__(self, conflict_id: str, status: str) -> None:
        self.conflict_id = conflict_id
        self.status = status
        super().__init__(f"Conflict {conflict_id!r} is already {status!r}")


class ConflictValidationError(Exception):
    pass


class ProjectionRebuildError(Exception):
    pass


class ConflictResolutionService:
    """
    Resolves a single claim conflict atomically.

    All public methods use the session passed at construction.  The caller
    (route handler or CLI) is responsible for committing or rolling back.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve(
        self,
        *,
        conflict_id: str,
        resolution_type: ResolutionType,
        operator_id: str,
        accepted_claim_id: str | None = None,
        rejected_claim_ids: list[str] | None = None,
        resolution: str | None = None,
    ) -> ClaimConflict:
        """
        Resolve an open conflict.

        Returns the mutated ClaimConflict row (not yet committed).

        Raises
        ------
        ConflictNotFoundError           — conflict_id does not exist
        ConflictAlreadyResolvedError    — conflict is not open
        ConflictValidationError         — invalid accepted/rejected combination
        ProjectionRebuildError          — projection rebuild failed (resolution
                                          not committed — caller should rollback)
        """
        # ── 1. Load and lock ──────────────────────────────────────────────────
        # SELECT FOR UPDATE prevents a second reviewer from resolving the same
        # conflict concurrently.  The second request will block until the first
        # commits, then find status='resolved' and receive a 409 from the route.
        result = await self._session.execute(
            select(ClaimConflict)
            .where(ClaimConflict.id == conflict_id)
            .with_for_update()
        )
        conflict: ClaimConflict | None = result.scalar_one_or_none()
        if conflict is None:
            raise ConflictNotFoundError(conflict_id)

        if conflict.status != "open":
            raise ConflictAlreadyResolvedError(conflict_id, conflict.status)

        # ── 2. Per-type validation ────────────────────────────────────────────
        conflict_claim_ids = {conflict.claim_a_id, conflict.claim_b_id}

        if resolution_type == "claim_accepted":
            if not accepted_claim_id:
                raise ConflictValidationError(
                    "accepted_claim_id is required when resolution_type is 'claim_accepted'."
                )
            if accepted_claim_id not in conflict_claim_ids:
                raise ConflictValidationError(
                    f"accepted_claim_id {accepted_claim_id!r} does not belong to "
                    f"conflict {conflict_id!r}. Must be one of {sorted(conflict_claim_ids)}."
                )

        elif resolution_type == "claim_rejected":
            if not rejected_claim_ids:
                raise ConflictValidationError(
                    "rejected_claim_ids is required when resolution_type is 'claim_rejected'."
                )
            unknown = set(rejected_claim_ids) - conflict_claim_ids
            if unknown:
                raise ConflictValidationError(
                    f"rejected_claim_ids contains ids not in conflict {conflict_id!r}: "
                    f"{sorted(unknown)}."
                )
            # Auto-derive survivor: if exactly one non-rejected claim remains, it
            # wins implicitly — store as accepted_claim_id so finalization can
            # restore it once all open conflicts on the field are settled.
            survivors = conflict_claim_ids - set(rejected_claim_ids)
            if len(survivors) == 1:
                accepted_claim_id = next(iter(survivors))

        else:
            # claims_merged / source_corrected / not_applicable / manual_override
            if accepted_claim_id and accepted_claim_id not in conflict_claim_ids:
                raise ConflictValidationError(
                    f"accepted_claim_id {accepted_claim_id!r} does not belong to "
                    f"conflict {conflict_id!r}."
                )
            if rejected_claim_ids:
                unknown = set(rejected_claim_ids) - conflict_claim_ids
                if unknown:
                    raise ConflictValidationError(
                        f"rejected_claim_ids contains ids not in conflict {conflict_id!r}: "
                        f"{sorted(unknown)}."
                    )

        if (
            accepted_claim_id
            and rejected_claim_ids
            and accepted_claim_id in rejected_claim_ids
        ):
            raise ConflictValidationError(
                f"accepted_claim_id {accepted_claim_id!r} also appears in "
                "rejected_claim_ids. A claim cannot be both accepted and rejected."
            )

        # ── 3. Apply resolution ───────────────────────────────────────────────
        now = datetime.now(tz=UTC)
        event_id = conflict.event_id
        field_name = conflict.field_name

        conflict.status = "resolved"
        conflict.resolution_type = resolution_type
        conflict.accepted_claim_id = accepted_claim_id
        conflict.rejected_claim_ids = rejected_claim_ids
        conflict.resolution = resolution
        conflict.resolved_by = operator_id
        conflict.resolved_at = now
        conflict.version = (getattr(conflict, "version", 0) or 0) + 1

        # ── 4. Mark rejected claims as REJECTED ───────────────────────────────
        # Moving rejected claims from DISPUTED → REJECTED gives the claim_type
        # column a precise meaning:
        #   DISPUTED  = under active review, no decision yet
        #   REJECTED  = explicitly discarded by a reviewer
        # This makes it unambiguous in provenance views and prevents finalization
        # from ever accidentally restoring a rejected claim.
        for rejected_id in rejected_claim_ids or []:
            rejected_claim: Claim | None = await self._session.get(Claim, rejected_id)
            if rejected_claim is None:
                continue
            old_type = rejected_claim.claim_type
            if old_type == ClaimType.REJECTED.value:
                continue   # already rejected (idempotent)
            rejected_claim.claim_type = ClaimType.REJECTED.value
            self._session.add(ClaimHistory(
                id=str(uuid.uuid4()),
                claim_id=rejected_claim.id,
                old_claim_type=old_type,
                new_claim_type=ClaimType.REJECTED.value,
                change_reason=f"conflict_rejected:{conflict_id}",
                changed_by=operator_id,
            ))
            log.info(
                "resolution.claim_rejected",
                conflict_id=conflict_id,
                claim_id=rejected_id,
                old_type=old_type,
            )

        # ── 5. Flush so finalization sees the resolved status ─────────────────
        await self._session.flush()

        # ── 6. Field-level finalization ───────────────────────────────────────
        svc = ProjectionService(session=self._session)
        await svc.finalize_accepted_claims_for_field(
            event_id=event_id,
            field_name=field_name,
            resolved_by=operator_id,
        )
        await self._session.flush()

        # ── 7. Rebuild projection ─────────────────────────────────────────────
        try:
            await svc.rebuild_event(event_id)
        except Exception as exc:
            log.warning(
                "resolution.projection_rebuild_failed",
                conflict_id=conflict_id,
                event_id=event_id,
                error=str(exc),
            )
            raise ProjectionRebuildError(str(exc)) from exc

        log.info(
            "resolution.resolved",
            conflict_id=conflict_id,
            event_id=event_id,
            field_name=field_name,
            resolution_type=resolution_type,
            operator_id=operator_id,
        )
        return conflict
