"""Standardise ingestion_runs.status to UPPERCASE; remove legacy 'completed' value.

Revision ID: 055
Revises: 054
Create Date: 2026-06-09

Migrates all four lowercase status values in ``ingestion_runs`` to their
UPPERCASE equivalents, unifying IngestionRunStatus with every other StrEnum in
the codebase.  'completed' is remapped to 'FINISHED' (its documented synonym)
and then dropped from the CHECK constraint.

Down migration is not provided: reverting a status-rename migration on live
data is unsafe without a full data audit.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "055"
down_revision = "054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE ingestion_runs SET status = 'RUNNING'  WHERE status = 'running'"))
    conn.execute(sa.text("UPDATE ingestion_runs SET status = 'FINISHED' WHERE status = 'finished'"))
    conn.execute(sa.text("UPDATE ingestion_runs SET status = 'FAILED'   WHERE status = 'failed'"))
    # 'completed' is a deprecated synonym for FINISHED; collapse it now.
    conn.execute(sa.text("UPDATE ingestion_runs SET status = 'FINISHED' WHERE status = 'completed'"))

    op.drop_constraint("ck_ingestion_runs_status", "ingestion_runs", type_="check")
    op.create_check_constraint(
        "ck_ingestion_runs_status",
        "ingestion_runs",
        "status IN ('RUNNING', 'FINISHED', 'FAILED')",
    )

    conn.execute(
        sa.text("ALTER TABLE ingestion_runs ALTER COLUMN status SET DEFAULT 'RUNNING'")
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Downgrading ingestion_run status values is unsafe; restore from backup."
    )
