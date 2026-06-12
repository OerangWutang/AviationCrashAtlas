"""Audit explanations API router (Phase 11).

Three endpoints, all read-only, all reader-gated:

- field-level explanation
- claim-level explanation
- source-verification view

The per-page summary lives on the public router as
``GET /public/events/{slug}/audit`` to keep slug-keyed surfaces in
one place; event-id-keyed endpoints live here.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from atlas.application.dto import CurrentUser
from atlas.application.unit_of_work import UnitOfWork
from atlas.application.use_cases.audit import (
    GetClaimExplanation,
    GetFieldExplanation,
    GetSourceVerification,
)
from atlas.application.use_cases.audit_chain_verify import (
    CHAINED_TABLES,
    verify_all,
    verify_table,
)
from atlas.domain.enums import Role
from atlas.presentation.api.dependencies import get_uow, require_role
from atlas.presentation.api.responses import offloaded_json_response
from atlas.presentation.api.schemas.audit import (
    ClaimExplanationResponse,
    FieldExplanationResponse,
    SourceVerificationResponse,
)

router = APIRouter(prefix="/audit", tags=["audit"])

# Reader-and-above gate.  These endpoints surface evidence that is
# already public via the projection and provenance endpoints — adding
# them to the reader role does not expose new information, it just
# makes the existing evidence chain legible to non-engineers.
_READERS = (Role.ADMIN, Role.REVIEWER, Role.ANALYST)


class _DetailMode(StrEnum):
    SUMMARY = "summary"
    EXPERT = "expert"


@router.get(
    "/events/{event_id}/fields/{field_name}/explanation",
    response_model=FieldExplanationResponse,
)
async def field_explanation(
    event_id: UUID,
    field_name: str,
    detail: _DetailMode = Query(default=_DetailMode.SUMMARY),
    uow: UnitOfWork = Depends(get_uow, scope="function"),
    _user: CurrentUser = Depends(require_role(*_READERS)),
) -> Response:
    """Explain how a single field got its current value.

    ``detail`` controls depth:
    - ``summary``: one-paragraph explanation suitable for high-level reports
    - ``expert``: detailed narrative covering each source, conflict, and review
    """
    response = await GetFieldExplanation(uow, expert=(detail == _DetailMode.EXPERT)).execute(event_id, field_name)
    await uow.rollback()
    payload = FieldExplanationResponse.model_validate(response)
    return await offloaded_json_response(payload.model_dump(mode="json"))


@router.get(
    "/claims/{claim_id}/explanation",
    response_model=ClaimExplanationResponse,
)
async def claim_explanation(
    claim_id: UUID,
    uow: UnitOfWork = Depends(get_uow, scope="function"),
    _user: CurrentUser = Depends(require_role(*_READERS)),
) -> Response:
    """Explain how a specific claim reached its current state.

    Traces the claim's lifecycle: creation, conflicts, reviews,
    supersessions, and final disposition.
    """
    response = await GetClaimExplanation(uow).execute(claim_id)
    await uow.rollback()
    payload = ClaimExplanationResponse.model_validate(response)
    return await offloaded_json_response(payload.model_dump(mode="json"))


@router.get(
    "/sources/{snapshot_id}/verification",
    response_model=SourceVerificationResponse,
)
async def source_verification(
    snapshot_id: UUID,
    uow: UnitOfWork = Depends(get_uow, scope="function"),
    _user: CurrentUser = Depends(require_role(*_READERS)),
) -> Response:
    response = await GetSourceVerification(uow).execute(snapshot_id)
    await uow.rollback()
    payload = SourceVerificationResponse(
        snapshot_id=response.snapshot_id,
        source_name=response.source_name,
        source_kind=response.source_kind,
        source_record_id=response.source_record_id,
        raw_payload_hash=response.raw_payload_hash,
        captured_at=response.captured_at,
        recipe_version=response.recipe_version,
        recipe_steps=response.recipe_steps,
        verification_note=response.verification_note,
    )
    return await offloaded_json_response(payload.model_dump(mode="json"))


# ── Audit chain verification ────────────────────────────────────────────────


@router.get("/chain/tables")
async def list_chained_tables() -> list[str]:
    """List all tables protected by the audit chain."""
    return list(CHAINED_TABLES)


@router.post("/chain/verify")
async def verify_audit_chain(
    table: str | None = Query(
        default=None,
        description="Single table to verify. Omit to verify all chained tables.",
    ),
    uow: UnitOfWork = Depends(get_uow, scope="function"),
    _user: CurrentUser = Depends(require_role(*_READERS)),
) -> dict:
    """Verify the integrity of the audit hash chain.

    Recomputes the HMAC chain for one or all protected tables and reports
    whether each table's rows are internally consistent.  Returns a list
    of per-table results with the first bad row (if any).
    """
    if table is not None:
        results = [await verify_table(uow, table)]
    else:
        results = await verify_all(uow)

    await uow.rollback()

    return {
        "ok": all(r.ok for r in results),
        "tables": [
            {
                "table_name": r.table_name,
                "row_count": r.row_count,
                "ok": r.ok,
                "first_bad_row_id": (
                    str(r.first_bad_row.row_id) if r.first_bad_row else None
                ),
                "first_bad_position": (
                    r.first_bad_row.row_position if r.first_bad_row else None
                ),
            }
            for r in results
        ],
    }