"""Align trigram indexes with the lower(...).LIKE search query.

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-03

The /api/v1/accidents search predicate is intentionally case-insensitive:

    lower(column) LIKE '%term%'

Revision 0012 added pg_trgm GIN indexes on the raw text columns.  PostgreSQL
cannot reliably use a raw-column trigram index for an expression predicate like
lower(column) LIKE ...; the indexed expression must match the queried
expression.  This migration replaces the raw trigram indexes with lower(...)
expression trigram indexes and adds the missing aircraft_model index.

CREATE/DROP INDEX CONCURRENTLY must run outside Alembic's normal transaction.
"""
from __future__ import annotations

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

# Old v0012 raw-column indexes.  They do not match lower(column).LIKE queries.
_RAW_TRGM_INDEXES = (
    "ix_record_location_trgm",
    "ix_record_make_trgm",
    "ix_record_operator_trgm",
    "ix_record_cause_trgm",
)

# New expression indexes.  Names include "lower" so schema inspection makes the
# indexed expression obvious and future migrations do not repeat the mismatch.
_LOWER_TRGM_INDEXES = (
    (
        "ix_record_location_lower_trgm",
        "location_text",
    ),
    (
        "ix_record_make_lower_trgm",
        "aircraft_make",
    ),
    (
        "ix_record_model_lower_trgm",
        "aircraft_model",
    ),
    (
        "ix_record_operator_lower_trgm",
        "operator_name",
    ),
    (
        "ix_record_cause_lower_trgm",
        "probable_cause",
    ),
)


def upgrade() -> None:
    ctx = op.get_context()
    with ctx.autocommit_block():
        for name in _RAW_TRGM_INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")

        for name, column in _LOWER_TRGM_INDEXES:
            op.execute(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} "
                f"ON accident_records USING gin (lower({column}) gin_trgm_ops)"
            )


def downgrade() -> None:
    ctx = op.get_context()
    with ctx.autocommit_block():
        for name, _column in _LOWER_TRGM_INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")

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
