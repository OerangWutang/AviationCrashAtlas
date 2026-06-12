"""ExportCaseReport use case.

Pure read + assembly — no new evidence data written.  Reads the projection,
provenance, conflict history, and source metadata from the existing UoW and
assembles a ``ReportModel``.

The renderer (HTML/PDF) is a separate infrastructure concern; this use case
only returns the value object.

Read-only: never commits.  Callers must rollback after use.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from atlas.application.unit_of_work import UnitOfWork
from atlas.domain.entities import Claim
from atlas.domain.exceptions import EventNotFoundError
from atlas.domain.publication.report_model import (
    ReportAuditEntry,
    ReportConflictResolution,
    ReportFact,
    ReportModel,
    ReportSourceRef,
)
from atlas.security import sign_evidence_hash

logger = logging.getLogger(__name__)


class ExportCaseReport:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def execute(
        self,
        event_id: UUID,
        report_version: int,
        generated_by: UUID | None = None,
    ) -> ReportModel:
        """Assemble a ReportModel for ``event_id``.

        Parameters
        ----------
        event_id:
            The accident event to generate the report for.
        report_version:
            The version number for this export (caller manages incrementing).
        generated_by:
            Atlas user_id of the reviewer generating the report.
        """
        uow = self._uow

        # ── Load event ────────────────────────────────────────────────────
        event = await uow.events.get(event_id)
        if event is None:
            raise EventNotFoundError(f"Event {event_id} not found")

        # ── Load projection ───────────────────────────────────────────────
        projection = await uow.projections.get(event_id)
        if projection is None:
            # No projection yet — return a minimal report with a warning
            fields: dict[str, Any] = {}
            projection_version = 0
            completeness_score = 0.0
            unresolved_conflict_fields: list[str] = []
        else:
            fields = projection.fields or {}
            projection_version = projection.projection_version
            completeness_score = float(projection.completeness_score)
            unresolved_conflict_fields = list(projection.unresolved_conflict_fields or [])

        # ── Load claims for field→source mapping ──────────────────────────
        claims = await uow.claims.find_all_by_event(event_id, limit=500)
        # Build field → winning claim map
        winning_claims = {c.field_name: c for c in claims if c.is_active and c.can_win()}
        # Deduplicate: keep the most-trusted claim per field
        field_to_claim: dict[str, Claim] = {}
        for c in claims:
            if not c.is_active:
                continue
            existing = field_to_claim.get(c.field_name)
            if existing is None:
                field_to_claim[c.field_name] = c
            # Prefer the winning claim; otherwise keep first active
            elif c.field_name in winning_claims and winning_claims[c.field_name].id == c.id:
                field_to_claim[c.field_name] = c

        # ── Load sources ──────────────────────────────────────────────────
        source_ids = list({c.source_id for c in field_to_claim.values()})
        sources_list = await uow.sources.get_by_ids(source_ids)
        sources_by_id = {s.id: s for s in sources_list}

        # Build deduplicated source refs for the sources appendix
        sources_seen: dict[UUID, ReportSourceRef] = {}
        for source in sources_list:
            sources_seen[source.id] = ReportSourceRef(
                source_id=source.id,
                source_name=source.name,
                source_kind=source.kind.value if hasattr(source.kind, "value") else str(source.kind),
                reliability_tier=source.reliability_tier,
            )

        # ── Assemble facts ────────────────────────────────────────────────
        facts: list[ReportFact] = []
        footnote_counter = 1

        for field_name, field_value in sorted(fields.items()):
            if isinstance(field_value, dict) and field_value.get("status") == "DISPUTED":
                is_disputed = True
                display_value = "DISPUTED"
            else:
                is_disputed = field_name in unresolved_conflict_fields
                display_value = field_value

            claim = field_to_claim.get(field_name)
            source_ref: ReportSourceRef | None = None
            claim_type = "RAW"
            is_manual_override = False

            if claim is not None:
                src = sources_by_id.get(claim.source_id)
                if src:
                    source_ref = sources_seen.get(src.id)
                claim_type = claim.claim_type.value if hasattr(claim.claim_type, "value") else str(claim.claim_type)
                is_manual_override = claim_type == "MANUAL_OVERRIDE"

            fact = ReportFact(
                field_name=field_name,
                field_value=display_value,
                claim_type=claim_type,
                is_disputed=is_disputed,
                is_manually_overridden=is_manual_override,
                source_ref=source_ref,
                footnote_number=footnote_counter if source_ref else None,
            )
            if source_ref:
                footnote_counter += 1
            facts.append(fact)

        # ── Load conflict resolution history ──────────────────────────────
        conflicts = await uow.conflicts.find_by_event(event_id, limit=200)
        resolution_history: list[ReportConflictResolution] = []

        for conflict in conflicts:
            if conflict.status.value != "RESOLVED" and str(conflict.status) != "RESOLVED":
                continue
            # Get the winning value
            winning_claim = None
            if conflict.winning_claim_id:
                matching = [c for c in claims if c.id == conflict.winning_claim_id]
                winning_claim = matching[0] if matching else None

            # Get superseded values
            conflict_claim_ids = set(conflict.claim_ids or [])
            superseded = [
                c.field_value
                for c in claims
                if c.id in conflict_claim_ids
                and c.id != conflict.winning_claim_id
                and not (c.is_active and c.can_win())
            ]

            resolver_display = (
                str(conflict.resolved_by)[:8] + "…"
                if conflict.resolved_by
                else "System"
            )

            resolution_history.append(
                ReportConflictResolution(
                    field_name=conflict.field_name,
                    resolved_at=conflict.resolved_at,
                    resolved_by=resolver_display,
                    rationale=conflict.last_modified_reason or "",
                    winning_value=winning_claim.field_value if winning_claim else None,
                    superseded_values=superseded,
                )
            )

        # ── Extract case metadata from projection fields ──────────────────
        def _field(name: str) -> str | None:
            v = fields.get(name)
            return str(v) if v is not None else None

        return ReportModel(
            event_id=event_id,
            case_title=_field("narrative") or f"Event {str(event_id)[:8]}",
            case_slug=str(event_id),
            occurrence_date=_field("event_date"),
            location=_field("location"),
            operator=_field("operator"),
            aircraft_type=_field("aircraft_type"),
            report_version=report_version,
            projection_version=projection_version,
            generated_at=datetime.now(UTC),
            generated_by=str(generated_by)[:8] + "…" if generated_by else None,
            facts=facts,
            unresolved_conflict_fields=unresolved_conflict_fields,
            completeness_score=completeness_score,
            resolution_history=resolution_history,
            sources=list(sources_seen.values()),
            audit_entries=[
                ReportAuditEntry(
                    description=f"Projection v{projection_version} — current projected state",
                    hash_value="(hash-chain verification in v1)",
                    timestamp=datetime.now(UTC),
                ),
            ],
        )

    async def build_manifest(
        self,
        event_id: UUID,
        report_version: int,
        report_content_sha256: str,
        projection_version: int,
        generated_by: UUID | None = None,
    ) -> dict[str, object]:
        """Build a verifiable export manifest for a report.

        The manifest includes:
        - Report metadata (event, version, projection)
        - SHA-256 hashes of all documents linked to this event
        - The report content hash
        - A HMAC signature over the manifest hash

        Returns a dict suitable for JSON serialization.  Callers can
        store this alongside the report or return it in the API response.
        """
        from sqlalchemy import text as sa_text

        session = self._uow.session  # type: ignore[attr-defined]
        doc_rows = await session.execute(
            sa_text(
                """SELECT id, filename, content_sha256, created_at
                   FROM uploaded_documents
                   WHERE event_id = :eid
                   ORDER BY created_at"""
            ),
            {"eid": str(event_id)},
        )
        document_hashes = [
            {
                "document_id": str(row[0]),
                "filename": row[1],
                "content_sha256": row[2],
                "uploaded_at": row[3].isoformat() if row[3] else None,
            }
            for row in doc_rows.fetchall()
        ]

        manifest: dict[str, object] = {
            "event_id": str(event_id),
            "report_version": report_version,
            "projection_version": projection_version,
            "generated_at": datetime.now(UTC).isoformat(),
            "generated_by": str(generated_by)[:8] + "…" if generated_by else None,
            "report_content_sha256": report_content_sha256,
            "document_count": len(document_hashes),
            "documents": document_hashes,
            "manifest_version": 1,
        }

        # Sign the serialized manifest
        manifest_json = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
        manifest_hash = hashlib.sha256(manifest_json.encode()).hexdigest()
        manifest["manifest_hash"] = manifest_hash
        manifest["manifest_signature"] = sign_evidence_hash(manifest_hash)

        return manifest