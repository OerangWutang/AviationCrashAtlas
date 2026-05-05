"""
SystemFailureTrackingService
=============================
Manages AccidentSystemFailure records for aviation accidents.

Design principles
-----------------
- Claims remain the source of truth. System failure rows are curated/derived
  records backed by claim provenance.
- is_causal_factor is NEVER set automatically. It requires an explicit
  reviewer assertion or a source claim that directly states causation.
- Status lifecycle:
    suspected → reported → confirmed
    suspected/reported → ruled_out     (final report clears the issue)
    any → disputed                     (when linked claims conflict)
- Old or contradicted claims are never deleted — the full evidence trail
  is preserved in SystemFailureClaim with link_reason=ruling_out_claim.
- confidence_score weighs: claim count, status certainty, source count,
  whether a final/confirmed finding exists, and dispute penalty.

Confidence scoring factors
--------------------------
  status_factor:   confirmed=1.0 | reported=0.8 | suspected=0.6
                   ruled_out=0.3 | disputed=0.4 | unknown=0.3
  source_factor:   min(source_count / 3, 1.0)
  claim_factor:    min(total_claims / 3, 1.0); confirmed-type claims add 0.2 bonus
  causal_factor:   +0.05 bonus when is_causal_factor (explicit source support)
  dispute_penalty: -0.30 when is_disputed

Status conflict detection (Phase 3)
------------------------------------
When rebuild_failures() runs, it checks whether any linked claim has
link_reason = "ruling_out_claim". If such a claim exists alongside
a "supporting_claim", the failure is automatically marked is_disputed=True
and status → "disputed". This represents the rule:
  "If one source says X failed and another says X was ruled out, dispute."

Extension points
----------------
- Call _extract_candidates(db, accident_event_id) from an NLP pipeline that
  returns structured failure candidates from accident narrative text.
- Link to AccidentTimelineEvent by adding a system_failure_id FK on that table.
- Pull FAA AD / EASA SB references into inspection_finding when available.

TODO:
  - Analytics endpoint: aggregate by category, aircraft model, year
  - Link AccidentSystemFailure → AccidentTimelineEvent via junction table
  - AI-assisted extraction from NTSB probable-cause and narrative fields
  - FAA Airworthiness Directive cross-reference (ad_number field)
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from atlas.models.orm import (
    AccidentEvent,
    AccidentSystemFailure,
    Claim,
    ClaimType,
    FailureCategory,
    FailureStatus,
    Source,
    SystemFailureClaim,
)

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------

_STATUS_FACTOR: dict[str, float] = {
    FailureStatus.CONFIRMED:  1.00,
    FailureStatus.REPORTED:   0.80,
    FailureStatus.SUSPECTED:  0.60,
    FailureStatus.DISPUTED:   0.40,
    FailureStatus.RULED_OUT:  0.30,
    FailureStatus.UNKNOWN:    0.30,
}


def compute_confidence(
    *,
    status: str,
    source_count: int,
    claim_types: list[str],
    is_disputed: bool,
    is_causal_factor: bool,
) -> float:
    """
    Return a 0.0–1.0 confidence score for a system failure record.

    Factors (averaged then adjusted):
    1. status_factor  — how certain the status is
    2. source_factor  — more independent sources → higher confidence
    3. claim_factor   — more confirmed claims → higher confidence

    Dispute penalty: −0.30
    Causal factor bonus: +0.05 (explicit source support adds small weight)
    """
    status_f = _STATUS_FACTOR.get(status, 0.30)
    source_f = min(source_count / 3.0, 1.0) if source_count > 0 else 0.20
    if claim_types:
        confirmed = sum(1 for ct in claim_types if ct == ClaimType.CONFIRMED)
        claim_f = min(len(claim_types) / 3.0, 1.0)
        if confirmed > 0:
            claim_f = min(claim_f + 0.20, 1.0)
    else:
        claim_f = 0.20

    score = (status_f + source_f + claim_f) / 3.0

    if is_disputed:
        score = max(0.0, score - 0.30)
    if is_causal_factor:
        score = min(1.0, score + 0.05)

    return round(score, 3)


# ---------------------------------------------------------------------------
# Status conflict detection (Phase 3)
# ---------------------------------------------------------------------------

def _resolve_dispute_status(
    current_status: str,
    claim_links: list[SystemFailureClaim],
) -> tuple[str, bool]:
    """
    Return (resolved_status, is_disputed).

    Rules:
    - If ruling_out_claim AND supporting_claim both exist → disputed.
    - If only ruling_out_claims exist and no supporting_claims → ruled_out.
    - Otherwise preserve current_status.
    """
    has_supporting = any(lnk.link_reason == "supporting_claim" for lnk in claim_links)
    has_ruling_out = any(lnk.link_reason == "ruling_out_claim" for lnk in claim_links)

    if has_supporting and has_ruling_out:
        return FailureStatus.DISPUTED, True
    if has_ruling_out and not has_supporting:
        return FailureStatus.RULED_OUT, False
    return current_status, False


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class SystemFailureTrackingService:
    """Stateless service — callers own the session and commit."""

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    @staticmethod
    async def get_failures(
        db: AsyncSession,
        accident_event_id: str,
        *,
        category: str | None = None,
        status: str | None = None,
        severity: str | None = None,
        disputed_only: bool = False,
        maintenance_only: bool = False,
        confirmed_only: bool = False,
        include_ruled_out: bool = True,
    ) -> list[AccidentSystemFailure]:
        """Return system failures for an accident, with optional server-side filters."""
        q = (
            select(AccidentSystemFailure)
            .where(AccidentSystemFailure.accident_event_id == accident_event_id)
            .options(
                selectinload(AccidentSystemFailure.claim_links).selectinload(
                    SystemFailureClaim.claim
                ),
                selectinload(AccidentSystemFailure.source),
            )
            .order_by(AccidentSystemFailure.created_at.asc())
        )

        if category:
            q = q.where(AccidentSystemFailure.failure_category == category)
        if status:
            q = q.where(AccidentSystemFailure.status == status)
        if severity:
            q = q.where(AccidentSystemFailure.severity == severity)
        if disputed_only:
            q = q.where(AccidentSystemFailure.is_disputed.is_(True))
        if maintenance_only:
            q = q.where(AccidentSystemFailure.maintenance_related.is_(True))
        if confirmed_only:
            q = q.where(AccidentSystemFailure.status == FailureStatus.CONFIRMED)
        if not include_ruled_out:
            q = q.where(AccidentSystemFailure.status != FailureStatus.RULED_OUT)

        result = await db.execute(q)
        return list(result.scalars().all())

    @staticmethod
    async def get_failure_by_id(
        db: AsyncSession,
        failure_id: str,
    ) -> AccidentSystemFailure | None:
        result = await db.execute(
            select(AccidentSystemFailure)
            .where(AccidentSystemFailure.id == failure_id)
            .options(
                selectinload(AccidentSystemFailure.claim_links).selectinload(
                    SystemFailureClaim.claim
                ),
                selectinload(AccidentSystemFailure.source),
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_supporting_claims(
        db: AsyncSession,
        accident_event_id: str,
    ) -> list[Claim]:
        sf_subq = (
            select(AccidentSystemFailure.id)
            .where(AccidentSystemFailure.accident_event_id == accident_event_id)
            .scalar_subquery()
        )
        result = await db.execute(
            select(Claim)
            .join(SystemFailureClaim, SystemFailureClaim.claim_id == Claim.id)
            .where(SystemFailureClaim.system_failure_id.in_(sf_subq))
            .distinct()
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    @staticmethod
    async def create_failure(
        db: AsyncSession,
        *,
        accident_event_id: str,
        failure_category: str = FailureCategory.UNKNOWN,
        subsystem: str | None = None,
        component_name: str | None = None,
        manufacturer: str | None = None,
        model_number: str | None = None,
        part_number: str | None = None,
        serial_number: str | None = None,
        failure_mode: str | None = None,
        status: str = FailureStatus.UNKNOWN,
        severity: str | None = None,
        is_causal_factor: bool = False,
        occurred_in_flight: bool | None = None,
        detected_before_accident: bool | None = None,
        detected_during_flight: bool | None = None,
        detected_post_accident: bool | None = None,
        maintenance_related: bool | None = None,
        inspection_finding: str | None = None,
        description: str | None = None,
        is_disputed: bool = False,
        dispute_summary: str | None = None,
        source_id: str | None = None,
        claim_ids: list[str] | None = None,
        claim_link_reasons: dict[str, str] | None = None,
    ) -> AccidentSystemFailure:
        """
        Create a system failure record and attach claim links.

        claim_link_reasons maps claim_id → link_reason. If absent, all claims
        default to "supporting_claim".

        Status conflict detection runs after claims are attached so the
        is_disputed flag is set correctly on creation.
        """
        sf_id = str(uuid.uuid4())
        failure = AccidentSystemFailure(
            id=sf_id,
            accident_event_id=accident_event_id,
            source_id=source_id,
            failure_category=failure_category,
            subsystem=subsystem,
            component_name=component_name,
            manufacturer=manufacturer,
            model_number=model_number,
            part_number=part_number,
            serial_number=serial_number,
            failure_mode=failure_mode,
            status=status,
            severity=severity,
            is_causal_factor=is_causal_factor,
            occurred_in_flight=occurred_in_flight,
            detected_before_accident=detected_before_accident,
            detected_during_flight=detected_during_flight,
            detected_post_accident=detected_post_accident,
            maintenance_related=maintenance_related,
            inspection_finding=inspection_finding,
            description=description,
            is_disputed=is_disputed,
            dispute_summary=dispute_summary,
            source_count=0,
        )
        db.add(failure)
        await db.flush()

        # Attach claim links
        reasons = claim_link_reasons or {}
        claim_types: list[str] = []
        source_ids: set[str] = set()

        for cid in (claim_ids or []):
            reason = reasons.get(cid, "supporting_claim")
            lnk = SystemFailureClaim(
                id=str(uuid.uuid4()),
                system_failure_id=sf_id,
                claim_id=cid,
                link_reason=reason,
            )
            db.add(lnk)
            claim_row = await db.get(Claim, cid)
            if claim_row:
                claim_types.append(claim_row.claim_type)
                source_ids.add(claim_row.source_id)

        failure.source_count = len(source_ids)

        # Run conflict detection on the links we just created
        # (We build a lightweight proxy since flush hasn't committed yet)
        link_proxies = [
            _LinkProxy(reason=reasons.get(cid, "supporting_claim"))
            for cid in (claim_ids or [])
        ]
        resolved_status, auto_disputed = _resolve_dispute_status(
            failure.status, link_proxies  # type: ignore[arg-type]
        )
        if auto_disputed and not failure.is_disputed:
            failure.status = resolved_status
            failure.is_disputed = True

        failure.confidence_score = compute_confidence(
            status=failure.status,
            source_count=failure.source_count,
            claim_types=claim_types,
            is_disputed=failure.is_disputed,
            is_causal_factor=failure.is_causal_factor,
        )

        log.info(
            "system_failure.created",
            sf_id=sf_id,
            category=failure_category,
            status=failure.status,
            confidence=failure.confidence_score,
        )
        return failure

    @staticmethod
    async def update_failure(
        db: AsyncSession,
        *,
        failure_id: str,
        updates: dict[str, Any],
    ) -> AccidentSystemFailure | None:
        """Partial update with automatic confidence recomputation."""
        failure = await SystemFailureTrackingService.get_failure_by_id(db, failure_id)
        if failure is None:
            return None

        allowed = {
            "failure_category", "subsystem", "component_name", "manufacturer",
            "model_number", "part_number", "serial_number", "failure_mode",
            "status", "severity", "is_causal_factor", "occurred_in_flight",
            "detected_before_accident", "detected_during_flight",
            "detected_post_accident", "maintenance_related",
            "inspection_finding", "description", "is_disputed", "dispute_summary",
        }
        for k, v in updates.items():
            if k in allowed:
                setattr(failure, k, v)

        # Recheck conflict status with current links
        resolved_status, auto_disputed = _resolve_dispute_status(
            failure.status, failure.claim_links
        )
        if auto_disputed:
            failure.status = resolved_status
            failure.is_disputed = True

        claim_types = [lnk.claim.claim_type for lnk in failure.claim_links if lnk.claim]
        source_ids = {lnk.claim.source_id for lnk in failure.claim_links if lnk.claim}
        failure.source_count = len(source_ids)
        failure.confidence_score = compute_confidence(
            status=failure.status,
            source_count=failure.source_count,
            claim_types=claim_types,
            is_disputed=failure.is_disputed,
            is_causal_factor=failure.is_causal_factor,
        )
        log.info("system_failure.updated", failure_id=failure_id)
        return failure

    @staticmethod
    async def delete_failure(db: AsyncSession, *, failure_id: str) -> bool:
        row = await db.get(AccidentSystemFailure, failure_id)
        if row is None:
            return False
        await db.delete(row)
        log.info("system_failure.deleted", failure_id=failure_id)
        return True

    @staticmethod
    async def rebuild_failures(
        db: AsyncSession,
        *,
        accident_event_id: str,
        operator_id: str,
    ) -> list[AccidentSystemFailure]:
        """
        Recompute confidence, dispute state, and source counts for all failures.

        Extension point: inject AI-extracted failure candidates here before
        the confidence-refresh loop.
        """
        failures = await SystemFailureTrackingService.get_failures(
            db, accident_event_id
        )
        for failure in failures:
            # Conflict detection
            resolved_status, auto_disputed = _resolve_dispute_status(
                failure.status, failure.claim_links
            )
            if auto_disputed:
                failure.status = resolved_status
                failure.is_disputed = True

            claim_types = [lnk.claim.claim_type for lnk in failure.claim_links if lnk.claim]
            source_ids = {lnk.claim.source_id for lnk in failure.claim_links if lnk.claim}
            failure.source_count = len(source_ids)
            failure.confidence_score = compute_confidence(
                status=failure.status,
                source_count=failure.source_count,
                claim_types=claim_types,
                is_disputed=failure.is_disputed,
                is_causal_factor=failure.is_causal_factor,
            )

        log.info(
            "system_failures.rebuilt",
            accident_event_id=accident_event_id,
            count=len(failures),
            operator_id=operator_id,
        )
        return failures

    # ------------------------------------------------------------------
    # Analytics (Phase 6) — simple in-memory aggregation
    # ------------------------------------------------------------------

    @staticmethod
    async def get_analytics(
        db: AsyncSession,
        *,
        accident_event_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Return aggregated analytics over system failure records.

        When accident_event_id is provided, scoped to that accident.
        Otherwise returns platform-wide aggregates.

        TODO: extend with year, aircraft model, engine model, phase_of_flight.
        """
        q = select(AccidentSystemFailure)
        if accident_event_id:
            q = q.where(AccidentSystemFailure.accident_event_id == accident_event_id)
        result = await db.execute(q)
        failures: list[AccidentSystemFailure] = list(result.scalars().all())

        by_category: dict[str, int] = {}
        by_status: dict[str, int] = {}
        by_severity: dict[str, int] = {}
        maintenance_count = 0
        causal_count = 0
        disputed_count = 0
        ruled_out_count = 0

        for f in failures:
            by_category[f.failure_category] = by_category.get(f.failure_category, 0) + 1
            by_status[f.status] = by_status.get(f.status, 0) + 1
            if f.severity:
                by_severity[f.severity] = by_severity.get(f.severity, 0) + 1
            if f.maintenance_related:
                maintenance_count += 1
            if f.is_causal_factor:
                causal_count += 1
            if f.is_disputed:
                disputed_count += 1
            if f.status == FailureStatus.RULED_OUT:
                ruled_out_count += 1

        return {
            "total": len(failures),
            "by_category": by_category,
            "by_status": by_status,
            "by_severity": by_severity,
            "maintenance_related_count": maintenance_count,
            "causal_factor_count": causal_count,
            "disputed_count": disputed_count,
            "ruled_out_count": ruled_out_count,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

class _LinkProxy:
    """Minimal stand-in used during creation before claims are flushed."""
    def __init__(self, reason: str):
        self.link_reason = reason
