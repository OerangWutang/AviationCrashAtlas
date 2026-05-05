"""Add event_revisions timeline + source_record_state aggregate.

Revision ID: 0008
Revises: 0007
Create Date: 2026-04-30

This migration introduces two related tables that together let the UI
answer the question "how did this record evolve, and which changes were
local rebuilds vs. real source changes?" without conflating the two.

1. event_revisions
   --------------
   Append-only log of human-readable changes for an accident event.
   Distinct from claim_history (which is per-claim, structured-only) —
   event_revisions is per-event, designed to be displayed as a timeline.

   Each row records a single semantic event: a snapshot first seen, a
   snapshot whose payload_hash changed, a claim value changed or
   superseded, a conflict opened/resolved/obsoleted, a source document
   first linked or marked unavailable, or a projection rebuild.

   We deliberately keep this separate from claim_history because:
     - claim_history is row-level audit, never deleted, joined by claim_id
     - event_revisions is the timeline view, joined by event_id and
       intentionally human-readable (description column)
     - the two will diverge: a single source-snapshot change can produce
       N claim_history rows but should produce 1 event_revisions row at
       the snapshot level plus M at the claim level if relevant

2. source_record_state
   ------------------
   Aggregate (source_id, source_record_id) state derived from
   raw_snapshots.  raw_snapshots itself remains immutable — each unique
   payload_hash gets its own row, so the same source record over time
   has one row per content version.  This aggregate is the rolling
   "current state" view of a source record:

     - first_seen_at:   when the source record was first ingested
     - last_seen_at:    the most recent ingest, even when nothing changed
     - last_changed_at: the most recent ingest where payload_hash differed
                        from the previous one
     - current_payload_hash / current_snapshot_id: the latest snapshot
     - parser_version:  the parser version that produced the current
                        canonical extraction (used to detect when a new
                        parser would re-extract the same raw payload
                        differently)

   This table is what the ingestion pipeline updates on every fetch,
   regardless of whether a new raw_snapshot row was created.  The "I saw
   this record again but nothing changed" signal that the prompt asks
   for lives here.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. event_revisions ───────────────────────────────────────────────
    op.create_table(
        "event_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "event_id",
            sa.String(36),
            sa.ForeignKey("accident_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Discriminator — keep loose (string) so new revision types don't
        # require a migration.  Documented values:
        #   source_record_first_seen
        #   source_snapshot_changed
        #   source_record_unchanged    (bumped last_seen_at, no content diff)
        #   source_field_added         (field appeared in a new snapshot)
        #   source_field_removed       (field was in an older snapshot but
        #                               disappeared from the latest one)
        #   source_field_value_changed
        #   claim_superseded
        #   conflict_opened
        #   conflict_resolved
        #   conflict_obsoleted
        #   source_document_linked
        #   source_document_verified
        #   source_document_unavailable
        #   projection_rebuilt
        sa.Column("revision_type", sa.String(50), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            comment="When the revision was recorded (server clock).",
        ),
        # Optional pointers to the upstream cause.  Both nullable because
        # not every revision type has a single source/snapshot (e.g. a
        # purely local projection rebuild).
        sa.Column(
            "source_id",
            sa.String(36),
            sa.ForeignKey("sources.id"),
            nullable=True,
        ),
        sa.Column(
            "source_record_id",
            sa.String(200),
            nullable=True,
            comment="Source-side stable id (e.g. NTSB EventId), if known",
        ),
        sa.Column(
            "snapshot_id",
            sa.String(36),
            sa.ForeignKey("raw_snapshots.id"),
            nullable=True,
        ),
        sa.Column(
            "claim_id",
            sa.String(36),
            sa.ForeignKey("claims.id"),
            nullable=True,
        ),
        sa.Column(
            "conflict_id",
            sa.String(36),
            sa.ForeignKey("claim_conflicts.id"),
            nullable=True,
        ),
        sa.Column(
            "source_document_id",
            sa.String(36),
            sa.ForeignKey("source_documents.id"),
            nullable=True,
        ),
        sa.Column(
            "ingestion_run_id",
            sa.String(36),
            sa.ForeignKey("ingestion_runs.id"),
            nullable=True,
        ),
        # Field name(s) involved (when applicable).  ARRAY(TEXT) so a
        # single revision can summarise "fields removed: x, y, z" without
        # exploding into N rows.
        sa.Column(
            "field_names",
            postgresql.ARRAY(sa.Text()),
            nullable=True,
        ),
        # Old/new value stored as the same JSON envelope used by claims
        # (claim_value.encode/decode), so the UI can render via the same
        # display() helper.  Both nullable.
        sa.Column("old_value", postgresql.JSONB(), nullable=True),
        sa.Column("new_value", postgresql.JSONB(), nullable=True),
        # Human-readable summary.  The frontend MAY use this verbatim for
        # the timeline; if it wants to localize / restyle it can use
        # revision_type + field_names + old/new value instead.
        sa.Column("description", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_event_revisions_event_at",
        "event_revisions",
        ["event_id", "occurred_at"],
    )
    op.create_index(
        "ix_event_revisions_run",
        "event_revisions",
        ["ingestion_run_id"],
    )

    # ── 2. source_record_state ───────────────────────────────────────────
    op.create_table(
        "source_record_state",
        sa.Column(
            "source_id",
            sa.String(36),
            sa.ForeignKey("sources.id"),
            primary_key=True,
        ),
        sa.Column(
            "source_record_id",
            sa.String(200),
            primary_key=True,
            comment="Stable source-side identifier (e.g. NTSB EventId)",
        ),
        sa.Column(
            "event_id",
            sa.String(36),
            sa.ForeignKey("accident_events.id"),
            nullable=True,
            comment=(
                "The accident event this source record currently maps to. "
                "Nullable because canonical mapping can change."
            ),
        ),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            comment="Updated on every ingest, even when content is unchanged",
        ),
        sa.Column(
            "last_changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            comment="Updated only when payload_hash differs from previous",
        ),
        sa.Column(
            "current_payload_hash",
            sa.String(64),
            nullable=False,
        ),
        sa.Column(
            "current_snapshot_id",
            sa.String(36),
            sa.ForeignKey("raw_snapshots.id"),
            nullable=True,
        ),
        sa.Column(
            "previous_payload_hash",
            sa.String(64),
            nullable=True,
            comment="Set when content changes — lets revision builders diff",
        ),
        sa.Column(
            "parser_version",
            sa.String(40),
            nullable=False,
            server_default=sa.text("'1'"),
        ),
        # Field-set bookkeeping for missing/removed-field detection.
        # Stored as a sorted list of canonical field names emitted by the
        # parser for the current snapshot.  When the field list shrinks
        # between ingests the ingestion pipeline emits source_field_removed
        # revision rows.
        sa.Column(
            "current_field_names",
            postgresql.ARRAY(sa.Text()),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_source_record_state_event",
        "source_record_state",
        ["event_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_source_record_state_event", table_name="source_record_state")
    op.drop_table("source_record_state")
    op.drop_index("ix_event_revisions_run", table_name="event_revisions")
    op.drop_index("ix_event_revisions_event_at", table_name="event_revisions")
    op.drop_table("event_revisions")
