"""Add api_keys table, conflict version column, and document REJECTED claim type.

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-03

Changes
-------
1. api_keys table
   Stores hashed API keys for reviewer/admin authentication.  Raw keys are
   never stored; only the SHA-256 hex digest is kept.  is_active=FALSE revokes
   a key without losing the audit row.

2. claim_conflicts.version  (INTEGER NOT NULL DEFAULT 0)
   Optimistic-lock counter.  Incremented by ConflictResolutionService.resolve()
   on every state transition.  The resolve endpoint loads the conflict with
   SELECT FOR UPDATE; a concurrent second resolver will block on the lock and
   then find status='resolved', returning 409 instead of silently overwriting.

3. REJECTED claim type (documentation only — no schema change required)
   ClaimType.REJECTED = 'rejected' is a new enum value added to the Python
   ORM.  The underlying column is TEXT without a CHECK constraint, so this
   value is already storable without an ALTER TABLE.  This migration documents
   the addition so it is traceable in the migration history.

4. ix_api_key_hash (UNIQUE INDEX on api_keys.key_hash)
   Enables O(1) lookup during authentication without exposing the hash as a
   primary key.

5. ix_conflict_version (INDEX on claim_conflicts.version)
   Supports admin dashboards that query "show all conflicts at version > N
   since the last audit sweep".  Lightweight because version is a monotone
   counter — cardinality is low for open conflicts and spreads out for
   resolved ones.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. api_keys table ─────────────────────────────────────────────────────
    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("key_hash", sa.String(64), nullable=False,
                  comment="SHA-256 hex digest of the raw key"),
        sa.Column("operator_id", sa.String(100), nullable=False,
                  comment="Human-readable identity (username/email)"),
        sa.Column("role", sa.String(20), nullable=False, server_default="reviewer",
                  comment="reviewer | admin"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("description", sa.Text(), nullable=True,
                  comment="Optional note about this key's purpose"),
    )
    op.create_index(
        "ix_api_key_hash",
        "api_keys",
        ["key_hash"],
        unique=True,
    )

    # ── 2. claim_conflicts.version ────────────────────────────────────────────
    op.add_column(
        "claim_conflicts",
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Optimistic-lock counter; incremented on every state transition",
        ),
    )
    op.create_index(
        "ix_conflict_version",
        "claim_conflicts",
        ["version"],
    )

    # ── 3. REJECTED claim type ────────────────────────────────────────────────
    # No DDL change required: claims.claim_type is TEXT, not a constrained enum.
    # This is a documentation-only marker so the addition is traceable.
    # Existing code paths that write claim_type='rejected' work without any
    # further migration.


def downgrade() -> None:
    op.drop_index("ix_conflict_version", table_name="claim_conflicts")
    op.drop_column("claim_conflicts", "version")
    op.drop_index("ix_api_key_hash", table_name="api_keys")
    op.drop_table("api_keys")
