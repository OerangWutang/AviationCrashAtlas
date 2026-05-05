"""Add conflict lifecycle: status, obsolete_reason, obsolete_at.

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-29

Changes
-------
1. claim_conflicts.status  — new VARCHAR(20) column, default 'open'
   Lifecycle states:
     open     — unresolved field disagreement, blocks projection trust
     resolved — manually accepted one claim; resolution + resolved_at populated
     obsolete — both conflicting claims superseded; no longer relevant

   Existing rows (all previously null-resolution) are backfilled to 'open'.

2. claim_conflicts.obsolete_reason  — TEXT, nullable
   Human-readable explanation when status → 'obsolete'.

3. claim_conflicts.obsolete_at  — TIMESTAMPTZ, nullable
   Timestamp when status → 'obsolete'.

has_conflicts in AccidentRecord now reflects only 'open' conflicts (see
ProjectionService._build_record).  Resolved or obsolete conflicts no longer
set the warning flag.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "claim_conflicts",
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
    )
    op.add_column(
        "claim_conflicts",
        sa.Column("obsolete_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "claim_conflicts",
        sa.Column("obsolete_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Backfill existing rows — all were previously "open" (resolution IS NULL)
    op.execute(
        "UPDATE claim_conflicts SET status = 'open' WHERE status IS NULL OR status = ''"
    )
    # Index for efficient open-conflict queries (projection, has_conflicts)
    op.create_index("ix_conflict_status", "claim_conflicts", ["status"])


def downgrade() -> None:
    op.drop_index("ix_conflict_status", table_name="claim_conflicts")
    op.drop_column("claim_conflicts", "obsolete_at")
    op.drop_column("claim_conflicts", "obsolete_reason")
    op.drop_column("claim_conflicts", "status")
