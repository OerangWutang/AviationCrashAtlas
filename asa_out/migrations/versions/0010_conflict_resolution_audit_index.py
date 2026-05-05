"""Add audit indexes for conflict resolution fields.

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-02

Changes
-------
1. ix_conflict_resolved_by  — partial index on claim_conflicts(resolved_by)
   WHERE status = 'resolved'.  Supports operator-level audit queries such as
   "show all conflicts this reviewer resolved" without scanning open/obsolete rows.

2. ix_conflict_resolved_at  — partial index on claim_conflicts(resolved_at DESC)
   WHERE status = 'resolved'.  Supports chronological audit log queries
   ("show resolutions since date X") and admin dashboards.

Both indexes are partial (WHERE status = 'resolved') because:
  - Open conflicts have NULL resolved_by / resolved_at — including them
    would bloat the index with NULLs that are never queried through these
    predicates.
  - Obsolete conflicts also never have populated resolution fields.
  - A partial index on a minority subset is dramatically smaller and faster
    than a full-table index, especially as open-conflict rows dominate.

These indexes complement the existing ix_conflict_status (added in migration
0004) which covers the WHERE clause itself.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Partial index: only resolved rows have meaningful resolved_by values.
    op.create_index(
        "ix_conflict_resolved_by",
        "claim_conflicts",
        ["resolved_by"],
        postgresql_where=sa.text("status = 'resolved'"),
    )
    # Descending: audit log queries always sort newest-first.
    op.create_index(
        "ix_conflict_resolved_at",
        "claim_conflicts",
        [sa.text("resolved_at DESC")],
        postgresql_where=sa.text("status = 'resolved'"),
    )


def downgrade() -> None:
    op.drop_index("ix_conflict_resolved_at", table_name="claim_conflicts")
    op.drop_index("ix_conflict_resolved_by", table_name="claim_conflicts")
