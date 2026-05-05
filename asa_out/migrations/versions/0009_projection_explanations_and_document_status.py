"""Add projection_explanations and document_status to accident_records.

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-01

Two new columns on the read-projection table accident_records, both
populated by ProjectionService and both read by the API.

1. projection_explanations (JSONB)
   ------------------------------
   Per-field rationale for the displayed value.  Stored as a JSONB
   array of objects, one per field that the projection touched, with
   the shape:

       {
         "field_name": "fatalities_total",
         "displayed_value": null | <decoded value>,
         "selected_claim_id": "..." | null,
         "selected_source_id": "..." | null,
         "source_rank": 1 | null,
         "selection_reason": "withheld_open_dispute",
         "has_open_conflict": true,
         "supporting_claim_count": 2,
         "disputed_claim_count": 2
       }

   selection_reason is one of a small set of documented machine codes
   (see ProjectionService for the canonical list).  The frontend
   humanises these — the backend never produces a free-form string.

   We store the explanations on the projection row, not in a separate
   table, because they are 1:1 with the projection rebuild — a stale
   explanation table is worse than no table at all, and updating both
   in lockstep is exactly what the projection service already does for
   confidence_breakdown.

2. document_status (TEXT)
   ----------------------
   Aggregate label over the SourceDocument rows for the event.  One of:

       'none_linked'
       'linked_unverified'
       'verified'
       'unavailable'
       'mixed'

   The label exists because the frontend's evidence-status bar needs a
   single value, and recomputing the aggregate from the docs array on
   every render is wasteful AND prone to drift between the API
   response and the frontend's heuristic.  Storing the backend's view
   makes it the source of truth.

Both columns are nullable because the migration cannot reasonably
backfill every existing row at deploy time; the next projection
rebuild populates them.  The API treats nulls as "not yet computed"
and falls back to the same conservative defaults the frontend uses.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "accident_records",
        sa.Column(
            "projection_explanations",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment=(
                "JSONB array of per-field projection rationales. "
                "See ProjectionService for shape and selection_reason codes."
            ),
        ),
    )
    op.add_column(
        "accident_records",
        sa.Column(
            "document_status",
            sa.Text(),
            nullable=True,
            comment=(
                "Aggregate document state. One of: "
                "none_linked | linked_unverified | verified | unavailable | mixed."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("accident_records", "document_status")
    op.drop_column("accident_records", "projection_explanations")
