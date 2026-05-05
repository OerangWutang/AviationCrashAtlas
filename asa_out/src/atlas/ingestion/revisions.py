"""
Emitters for event_revisions rows.

These helpers wrap the small details of building an EventRevision row so
the ingestion pipeline (and, later, manual review tooling) can record a
human-readable change with a single function call.

Design notes
------------
* Revisions are append-only; we never UPDATE an event_revisions row.
* description is mandatory whenever the row will surface in the UI;
  callers are expected to write a short, neutral, evidence-based phrase.
  When in doubt, prefer fewer revisions over more — every row is a
  promise to the reader that *something* meaningful happened.
* old/new value MUST use the same JSONB envelope produced by
  claim_value.encode() so consumers can render via claim_value.display().
* These functions stage rows on the session; they DO NOT commit.  The
  caller is responsible for the surrounding transaction.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from atlas.models.orm import EventRevision


def _add(
    session: AsyncSession,
    *,
    event_id: str,
    revision_type: str,
    description: str,
    occurred_at: datetime | None = None,
    source_id: str | None = None,
    source_record_id: str | None = None,
    snapshot_id: str | None = None,
    claim_id: str | None = None,
    conflict_id: str | None = None,
    source_document_id: str | None = None,
    ingestion_run_id: str | None = None,
    field_names: Iterable[str] | None = None,
    old_value: dict[str, Any] | None = None,
    new_value: dict[str, Any] | None = None,
) -> EventRevision:
    rev = EventRevision(
        id=str(uuid.uuid4()),
        event_id=event_id,
        revision_type=revision_type,
        occurred_at=occurred_at or datetime.now(tz=UTC),
        source_id=source_id,
        source_record_id=source_record_id,
        snapshot_id=snapshot_id,
        claim_id=claim_id,
        conflict_id=conflict_id,
        source_document_id=source_document_id,
        ingestion_run_id=ingestion_run_id,
        field_names=list(field_names) if field_names else None,
        old_value=old_value,
        new_value=new_value,
        description=description,
    )
    session.add(rev)
    return rev


# ── High-level helpers used by the pipeline ──────────────────────────────────

def emit_source_record_first_seen(
    session: AsyncSession,
    *,
    event_id: str,
    source_id: str,
    source_record_id: str,
    snapshot_id: str,
    ingestion_run_id: str,
    source_short_name: str | None = None,
) -> None:
    name = source_short_name or "A source"
    _add(
        session,
        event_id=event_id,
        revision_type="source_record_first_seen",
        description=f"{name} first published this record.",
        source_id=source_id,
        source_record_id=source_record_id,
        snapshot_id=snapshot_id,
        ingestion_run_id=ingestion_run_id,
    )


def emit_source_snapshot_changed(
    session: AsyncSession,
    *,
    event_id: str,
    source_id: str,
    source_record_id: str,
    snapshot_id: str,
    ingestion_run_id: str,
    changed_fields: list[str],
    source_short_name: str | None = None,
) -> None:
    name = source_short_name or "A source"
    desc = f"{name} updated this record."
    if changed_fields:
        nice = ", ".join(f.replace("_", " ") for f in changed_fields[:5])
        more = f" (+{len(changed_fields) - 5} more)" if len(changed_fields) > 5 else ""
        desc = f"{name} updated this record: {nice}{more}."
    _add(
        session,
        event_id=event_id,
        revision_type="source_snapshot_changed",
        description=desc,
        source_id=source_id,
        source_record_id=source_record_id,
        snapshot_id=snapshot_id,
        ingestion_run_id=ingestion_run_id,
        field_names=changed_fields or None,
    )


def emit_source_record_unchanged(
    session: AsyncSession,
    *,
    event_id: str,
    source_id: str,
    source_record_id: str,
    ingestion_run_id: str,
    source_short_name: str | None = None,
) -> None:
    """
    Used when we re-fetched a source record but its content hash matches
    the previous snapshot.  Surfacing this in the timeline matters because
    "we still see this record, it just hasn't changed" is genuinely
    different from "we lost track of this record".
    """
    name = source_short_name or "A source"
    _add(
        session,
        event_id=event_id,
        revision_type="source_record_unchanged",
        description=f"{name} re-checked this record (no content change).",
        source_id=source_id,
        source_record_id=source_record_id,
        ingestion_run_id=ingestion_run_id,
    )


def emit_source_field_added(
    session: AsyncSession,
    *,
    event_id: str,
    source_id: str,
    snapshot_id: str,
    ingestion_run_id: str,
    fields: list[str],
    source_short_name: str | None = None,
) -> None:
    if not fields:
        return
    name = source_short_name or "A source"
    nice = ", ".join(f.replace("_", " ") for f in fields)
    _add(
        session,
        event_id=event_id,
        revision_type="source_field_added",
        description=f"{name} added field(s): {nice}.",
        source_id=source_id,
        snapshot_id=snapshot_id,
        ingestion_run_id=ingestion_run_id,
        field_names=fields,
    )


def emit_source_field_removed(
    session: AsyncSession,
    *,
    event_id: str,
    source_id: str,
    snapshot_id: str,
    ingestion_run_id: str,
    fields: list[str],
    source_short_name: str | None = None,
) -> None:
    """
    Emit when one or more canonical fields disappeared between a previous
    snapshot and the new one.  This is the v20 "removed/retracted upstream"
    signal — the projection still shows the old value via the surviving
    superseded claim, but the timeline must surface that the source no
    longer asserts it.
    """
    if not fields:
        return
    name = source_short_name or "A source"
    nice = ", ".join(f.replace("_", " ") for f in fields)
    _add(
        session,
        event_id=event_id,
        revision_type="source_field_removed",
        description=f"{name} removed field(s): {nice}.",
        source_id=source_id,
        snapshot_id=snapshot_id,
        ingestion_run_id=ingestion_run_id,
        field_names=fields,
    )


def emit_source_document_linked(
    session: AsyncSession,
    *,
    event_id: str,
    source_id: str,
    source_document_id: str,
    ingestion_run_id: str,
    document_type: str,
    title: str | None,
) -> None:
    label = title or document_type
    _add(
        session,
        event_id=event_id,
        revision_type="source_document_linked",
        description=f"Source document linked: {label}.",
        source_id=source_id,
        source_document_id=source_document_id,
        ingestion_run_id=ingestion_run_id,
    )


def emit_projection_rebuilt(
    session: AsyncSession,
    *,
    event_id: str,
    ingestion_run_id: str | None = None,
) -> None:
    """
    Lightweight marker that the projection was rebuilt during this run.
    Consumers expecting many of these in a single feed should collapse
    consecutive entries client-side rather than asking the DB to skip them.
    """
    _add(
        session,
        event_id=event_id,
        revision_type="projection_rebuilt",
        description="Record rebuilt from current claims.",
        ingestion_run_id=ingestion_run_id,
    )
