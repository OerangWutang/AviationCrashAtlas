"""Split source_count into winning/claim counts; add conflict uniqueness; drop fatalities_crew.

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-29

Changes
-------
1. accident_records.claim_source_ids  — new TEXT[] column
   Stores all source IDs that contributed any non-superseded claim,
   including those whose claims did not win.  source_ids continues to
   store only winning-claim source IDs.

   Motivation: source_ids (now renamed conceptually to winning_source_ids
   in the API) was exposed as source_count, which could be 1 even when a
   second source contributed five losing claims.  The split makes the
   distinction explicit without breaking existing data (existing rows get
   NULL for claim_source_ids; projection rebuild populates it).

2. claim_conflicts — add UNIQUE(event_id, field_name, claim_a_id, claim_b_id)
   Without this constraint, re-running projection for the same event could
   produce duplicate conflict rows.  Claim IDs are sorted (min first) by
   ClaimWriter before insert so (A,B) and (B,A) are treated as the same
   pair.

   Existing rows may already contain duplicates; this migration deduplicates
   them before adding the constraint (keeping the row with the lowest id).

3. accident_records.fatalities_crew  — column DROPPED
   The NTSB CSV has no crew-only fatality count.  The column was never
   populated during ingestion; every row was NULL.  NULL is ambiguous:
   it means "not implemented", not "confirmed zero" or "unknown".  For
   safety data that distinction matters, so the column is removed until
   a real source field is wired in the normalizer.

   A future migration can re-add it with a NOT NULL default and a clear
   sentinel (e.g. -1 for "unknown") once the data pipeline is ready.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Add claim_source_ids ────────────────────────────────────────────────
    op.add_column(
        "accident_records",
        sa.Column(
            "claim_source_ids",
            postgresql.ARRAY(sa.String(36)),
            nullable=True,
            comment="All sources with non-superseded claims, including non-winning",
        ),
    )

    # ── 2. Deduplicate claim_conflicts before adding the unique constraint ─────
    # Keep the row with the lowest UUID (arbitrary but deterministic) for each
    # (event_id, field_name, claim_a_id, claim_b_id) combination.
    op.execute(
        """
        DELETE FROM claim_conflicts
        WHERE id NOT IN (
            SELECT MIN(id)
            FROM claim_conflicts
            GROUP BY event_id, field_name, claim_a_id, claim_b_id
        )
        """
    )
    op.create_unique_constraint(
        "uq_conflict_claim_pair",
        "claim_conflicts",
        ["event_id", "field_name", "claim_a_id", "claim_b_id"],
    )

    # ── 3. Drop fatalities_crew ────────────────────────────────────────────────
    op.drop_column("accident_records", "fatalities_crew")


def downgrade() -> None:
    # Re-add fatalities_crew (will be NULL everywhere after downgrade)
    op.add_column(
        "accident_records",
        sa.Column("fatalities_crew", sa.Integer(), nullable=True),
    )
    op.drop_constraint("uq_conflict_claim_pair", "claim_conflicts", type_="unique")
    op.drop_column("accident_records", "claim_source_ids")
