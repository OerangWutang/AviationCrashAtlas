"""Add pg_trgm search indexes, api_keys.expires_at, and cursor pagination index.

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-03

Changes
-------
1. pg_trgm extension
   Enables trigram-based fuzzy text search on the accident_records table.
   The extension is idempotent (CREATE EXTENSION IF NOT EXISTS).

2. Trigram GIN indexes on accident_records
   Four indexes for the most queried text columns: location_text,
   aircraft_make, operator_name, probable_cause.  These replace the
   current LIKE '%term%' full-table scan with an index-accelerated
   trgm similarity scan, making free-text search viable on large datasets.

   Index choice: GIN over GiST because:
   - GIN is faster for LIKE/ILIKE queries (the dominant search pattern here).
   - GiST is faster for similarity operators (%); we don't use those.
   - GIN indexes are larger but we have disk headroom.

3. api_keys.expires_at (TIMESTAMPTZ, nullable)
   NULL = key never expires.  The auth middleware now checks this on every
   request.  Existing rows get NULL (never expire) automatically via the
   column default.

4. ix_record_cursor — composite index on (occurred_at DESC, id ASC)
   Supports keyset (cursor) pagination: WHERE (occurred_at, id) < (:at, :id).
   Using DESC/ASC ordering that matches the default sort so the index is
   forward-compatible with the date_desc cursor query.

5. ix_record_year_sev — composite index on (occurred_year, injury_severity)
   Supports the common "filter by year and severity" query pattern, which
   is currently a scan even when both filters are present.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. pg_trgm extension ──────────────────────────────────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ── 2. Trigram GIN indexes ────────────────────────────────────────────────
    # GIN indexes on text search columns.  All partial on non-NULL to keep
    # index size small (NULL location_text is common for early records).
    #
    # IMPORTANT: PostgreSQL rejects CREATE INDEX CONCURRENTLY inside a
    # transaction block.  The Alembic environment wraps migrations in
    # context.begin_transaction(), so these statements must run in an explicit
    # autocommit block.  Without this, a clean migration to head fails before the
    # application can start.
    ctx = op.get_context()
    with ctx.autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_record_location_trgm "
            "ON accident_records USING gin(location_text gin_trgm_ops) "
            "WHERE location_text IS NOT NULL"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_record_make_trgm "
            "ON accident_records USING gin(aircraft_make gin_trgm_ops) "
            "WHERE aircraft_make IS NOT NULL"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_record_operator_trgm "
            "ON accident_records USING gin(operator_name gin_trgm_ops) "
            "WHERE operator_name IS NOT NULL"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_record_cause_trgm "
            "ON accident_records USING gin(probable_cause gin_trgm_ops) "
            "WHERE probable_cause IS NOT NULL"
        )

    # ── 3. api_keys.expires_at ────────────────────────────────────────────────
    op.add_column(
        "api_keys",
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Optional expiry. NULL = never expires.",
        ),
    )
    op.create_index(
        "ix_api_key_expires",
        "api_keys",
        ["expires_at"],
        postgresql_where=sa.text("expires_at IS NOT NULL"),
    )

    # ── 4. Cursor pagination composite index ──────────────────────────────────
    op.create_index(
        "ix_record_cursor",
        "accident_records",
        [sa.text("occurred_at DESC NULLS LAST"), sa.text("id ASC")],
    )

    # ── 5. Year + severity composite index ───────────────────────────────────
    op.create_index(
        "ix_record_year_sev",
        "accident_records",
        ["occurred_year", "injury_severity"],
        postgresql_where=sa.text("occurred_year IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_record_year_sev", table_name="accident_records")
    op.drop_index("ix_record_cursor", table_name="accident_records")
    op.drop_index("ix_api_key_expires", table_name="api_keys")
    op.drop_column("api_keys", "expires_at")
    op.execute("DROP INDEX IF EXISTS ix_record_cause_trgm")
    op.execute("DROP INDEX IF EXISTS ix_record_operator_trgm")
    op.execute("DROP INDEX IF EXISTS ix_record_make_trgm")
    op.execute("DROP INDEX IF EXISTS ix_record_location_trgm")
    # Note: do not drop the pg_trgm extension — other schemas may depend on it.
