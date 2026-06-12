"""Add legal_hold + retention + redaction columns + compliance_events ledger.

Revision ID: 052
Revises: 051
Create Date: 2026-06-06

Implements F-P0-2 from the Phase 1 audit: schema-level support for
legal holds, retention deadlines, and redaction metadata, plus an
append-only ledger of compliance-relevant actions.

Schema changes are purely additive and nullable so the migration
applies safely against populated tables without backfill.  The
retention sweeper (TICKET-013) consumes ``retention_until`` and
honors ``legal_hold``; until that worker is shipped the columns
sit unused.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "052"
down_revision = "051"
branch_labels = None
depends_on = None

_TABLES: tuple[str, ...] = (
    "accident_events",
    "claims",
    "tenant_claims",
    "uploaded_documents",
    "usage_events",
)


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column(
                "legal_hold",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )
        op.add_column(table, sa.Column("legal_hold_reason", sa.Text(), nullable=True))
        op.add_column(
            table,
            sa.Column("legal_hold_set_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.add_column(
            table,
            sa.Column("legal_hold_set_by", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.add_column(
            table,
            sa.Column("retention_until", sa.DateTime(timezone=True), nullable=True),
        )
        op.add_column(
            table,
            sa.Column("redacted_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.add_column(
            table,
            sa.Column("redacted_by", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.add_column(table, sa.Column("redaction_reason", sa.Text(), nullable=True))
        op.add_column(
            table,
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        )
        # Partial index: rows under legal hold (expected to be a small
        # fraction of the table, so a partial index stays tiny).
        op.create_index(
            f"ix_{table}_legal_hold",
            table,
            ["id"],
            postgresql_where=sa.text("legal_hold = true"),
        )
        # Partial index: rows the retention sweeper still needs to
        # consider (deadline set, not yet deleted).
        op.create_index(
            f"ix_{table}_retention_until",
            table,
            ["retention_until"],
            postgresql_where=sa.text("retention_until IS NOT NULL AND deleted_at IS NULL"),
        )

    op.create_table(
        "compliance_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("actor_type", sa.String(20), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "payload_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "entity_type IN ('accident_event', 'claim', 'tenant_claim', "
            "'uploaded_document', 'usage_event')",
            name="ck_compliance_events_entity_type",
        ),
        sa.CheckConstraint(
            "action IN ('LEGAL_HOLD_APPLIED', 'LEGAL_HOLD_RELEASED', "
            "'REDACTION_APPLIED', 'RETENTION_SET', 'DELETION_APPLIED')",
            name="ck_compliance_events_action",
        ),
        sa.CheckConstraint(
            "actor_type IN ('USER', 'SYSTEM')",
            name="ck_compliance_events_actor_type",
        ),
        sa.CheckConstraint(
            "(actor_type = 'SYSTEM') OR (actor_id IS NOT NULL)",
            name="ck_compliance_events_actor_id_required_for_user",
        ),
    )
    op.create_index(
        "ix_compliance_events_entity",
        "compliance_events",
        ["entity_type", "entity_id", "created_at"],
    )
    op.create_index(
        "ix_compliance_events_action_created",
        "compliance_events",
        ["action", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_compliance_events_action_created", "compliance_events")
    op.drop_index("ix_compliance_events_entity", "compliance_events")
    op.drop_table("compliance_events")
    for table in _TABLES:
        op.drop_index(f"ix_{table}_retention_until", table)
        op.drop_index(f"ix_{table}_legal_hold", table)
        for col in (
            "deleted_at",
            "redaction_reason",
            "redacted_by",
            "redacted_at",
            "retention_until",
            "legal_hold_set_by",
            "legal_hold_set_at",
            "legal_hold_reason",
            "legal_hold",
        ):
            op.drop_column(table, col)
