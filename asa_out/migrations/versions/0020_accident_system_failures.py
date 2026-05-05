"""add accident_system_failures and system_failure_claims tables

Revision ID: 0020_accident_system_failures
Revises: 0019_accident_weather_observations
Create Date: 2026-05-05
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0020_accident_system_failures"
down_revision = "0019_accident_weather_observations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── accident_system_failures ───────────────────────────────────────────────
    op.create_table(
        "accident_system_failures",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "accident_event_id",
            sa.String(36),
            sa.ForeignKey("accident_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_id", sa.String(36), sa.ForeignKey("sources.id"), nullable=True),
        # Classification
        sa.Column(
            "failure_category", sa.String(30), nullable=False, server_default="unknown"
        ),
        sa.Column("subsystem", sa.String(100), nullable=True),
        # Component detail
        sa.Column("component_name", sa.String(200), nullable=True),
        sa.Column("manufacturer", sa.String(200), nullable=True),
        sa.Column("model_number", sa.String(100), nullable=True),
        sa.Column("part_number", sa.String(100), nullable=True),
        sa.Column("serial_number", sa.String(100), nullable=True),
        # Failure characterization
        sa.Column("failure_mode", sa.String(30), nullable=True),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default="unknown"
        ),
        sa.Column("severity", sa.String(20), nullable=True),
        sa.Column("is_causal_factor", sa.Boolean, nullable=False, server_default=sa.false()),
        # Detection timing
        sa.Column("occurred_in_flight", sa.Boolean, nullable=True),
        sa.Column("detected_before_accident", sa.Boolean, nullable=True),
        sa.Column("detected_during_flight", sa.Boolean, nullable=True),
        sa.Column("detected_post_accident", sa.Boolean, nullable=True),
        # Maintenance context
        sa.Column("maintenance_related", sa.Boolean, nullable=True),
        sa.Column("inspection_finding", sa.Text, nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        # Quality
        sa.Column("confidence_score", sa.Numeric(4, 3), nullable=True),
        sa.Column("is_disputed", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("dispute_summary", sa.Text, nullable=True),
        sa.Column("source_count", sa.Integer, nullable=False, server_default="0"),
        # Audit
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_system_failure_accident",
        "accident_system_failures",
        ["accident_event_id"],
    )
    op.create_index(
        "ix_system_failure_category",
        "accident_system_failures",
        ["failure_category"],
    )
    op.create_index(
        "ix_system_failure_status",
        "accident_system_failures",
        ["status"],
    )

    # ── system_failure_claims ─────────────────────────────────────────────────
    op.create_table(
        "system_failure_claims",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "system_failure_id",
            sa.String(36),
            sa.ForeignKey("accident_system_failures.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "claim_id",
            sa.String(36),
            sa.ForeignKey("claims.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "link_reason",
            sa.String(80),
            nullable=False,
            server_default="supporting_claim",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "system_failure_id", "claim_id", name="uq_system_failure_claim"
        ),
    )
    op.create_index(
        "ix_system_failure_claims_failure",
        "system_failure_claims",
        ["system_failure_id"],
    )
    op.create_index(
        "ix_system_failure_claims_claim", "system_failure_claims", ["claim_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_system_failure_claims_claim", table_name="system_failure_claims")
    op.drop_index("ix_system_failure_claims_failure", table_name="system_failure_claims")
    op.drop_table("system_failure_claims")
    op.drop_index("ix_system_failure_status", table_name="accident_system_failures")
    op.drop_index("ix_system_failure_category", table_name="accident_system_failures")
    op.drop_index("ix_system_failure_accident", table_name="accident_system_failures")
    op.drop_table("accident_system_failures")
