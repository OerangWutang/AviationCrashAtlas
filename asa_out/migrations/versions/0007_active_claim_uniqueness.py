"""Enforce one active claim per (event, source, field) at the DB level.

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-30

The application logic in ClaimWriter._upsert_claim already supersedes the
old claim before inserting a new one for the same (event, source, field)
triple.  However, that invariant has only been enforced by application
code so far — a buggy code path, a manual SQL insert, or a concurrent
ingest of two snapshots for the same source record could insert a second
active claim for the same field, silently corrupting the projection.

This migration adds a partial unique index that the database enforces:

    UNIQUE (event_id, source_id, field_name)
    WHERE claim_type <> 'superseded'

`superseded` claims are explicitly excluded so historical (audit-trail)
claims remain in the table without violating the invariant.

The index is partial because we want to allow many superseded rows but
exactly one active row per (event, source, field).

If duplicate active claims happen to exist when this migration runs —
they should not, but in case of historical data drift — we mark all but
the most recently created one as `superseded` first so the migration is
safe to apply on existing databases.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Defensive cleanup of any pre-existing duplicate active claims ──
    # If the application has been buggy in the past, two non-superseded
    # claims could exist for the same (event, source, field). Promote all
    # but the newest of each such cluster to 'superseded' so the unique
    # index can be created without a constraint violation.
    op.execute(
        """
        WITH ranked AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY event_id, source_id, field_name
                    ORDER BY created_at DESC, id DESC
                ) AS rn
            FROM claims
            WHERE claim_type <> 'superseded'
        )
        UPDATE claims
        SET claim_type = 'superseded',
            is_winning = false
        WHERE id IN (SELECT id FROM ranked WHERE rn > 1);
        """
    )

    # ── 2. Partial unique index ───────────────────────────────────────────
    # Names match the model invariant: one ACTIVE claim per
    # (event_id, source_id, field_name). Superseded claims (the audit
    # trail) are deliberately excluded from the constraint.
    op.create_index(
        "uq_active_claim_per_event_source_field",
        "claims",
        ["event_id", "source_id", "field_name"],
        unique=True,
        postgresql_where=sa.text("claim_type <> 'superseded'"),
    )


def downgrade() -> None:
    op.drop_index("uq_active_claim_per_event_source_field", table_name="claims")
