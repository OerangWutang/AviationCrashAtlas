"""Add occurred_at_precision to accident_records; fix occurred_at timezone.

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-29

Changes
-------
1. accident_events.occurred_at  — TIMESTAMPTZ → TIMESTAMP (no tz)
   accident_records.occurred_at — TIMESTAMPTZ → TIMESTAMP (no tz)

   NTSB accident times are *local* to the accident site.  Storing them as
   TIMESTAMPTZ falsely implies we know the UTC offset.  TIMESTAMP (no tz)
   is the honest representation of "local time, offset unknown."

   The ALTER TYPE ... USING cast is safe: PostgreSQL stores TIMESTAMPTZ as
   UTC internally; casting to TIMESTAMP strips the offset and leaves the
   wall-clock value, which is exactly what we want (we treat the stored
   value as local regardless of what offset psycopg2 may have attached).

2. accident_records.occurred_at_precision — new VARCHAR(10) column, nullable.
   Values: "exact" | "day" | "year"  (populated by ProjectionService).
   Nullable so existing rows are not broken; NULL means "precision unknown."
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. accident_events.occurred_at: TIMESTAMPTZ → TIMESTAMP ──────────────
    op.execute(
        """
        ALTER TABLE accident_events
            ALTER COLUMN occurred_at
            TYPE TIMESTAMP WITHOUT TIME ZONE
            USING occurred_at AT TIME ZONE 'UTC'
        """
    )

    # ── 2. accident_records.occurred_at: TIMESTAMPTZ → TIMESTAMP ─────────────
    op.execute(
        """
        ALTER TABLE accident_records
            ALTER COLUMN occurred_at
            TYPE TIMESTAMP WITHOUT TIME ZONE
            USING occurred_at AT TIME ZONE 'UTC'
        """
    )

    # ── 3. accident_records.occurred_at_precision: new column ─────────────────
    op.add_column(
        "accident_records",
        sa.Column(
            "occurred_at_precision",
            sa.String(10),
            nullable=True,
            comment=(
                "Time precision from source: 'exact' (date+time), "
                "'day' (date only), 'year' (year only). "
                "NULL means precision was not recorded."
            ),
        ),
    )


def downgrade() -> None:
    # ── 3. drop occurred_at_precision ─────────────────────────────────────────
    op.drop_column("accident_records", "occurred_at_precision")

    # ── 2. accident_records.occurred_at: TIMESTAMP → TIMESTAMPTZ ─────────────
    op.execute(
        """
        ALTER TABLE accident_records
            ALTER COLUMN occurred_at
            TYPE TIMESTAMP WITH TIME ZONE
            USING occurred_at AT TIME ZONE 'UTC'
        """
    )

    # ── 1. accident_events.occurred_at: TIMESTAMP → TIMESTAMPTZ ──────────────
    op.execute(
        """
        ALTER TABLE accident_events
            ALTER COLUMN occurred_at
            TYPE TIMESTAMP WITH TIME ZONE
            USING occurred_at AT TIME ZONE 'UTC'
        """
    )
