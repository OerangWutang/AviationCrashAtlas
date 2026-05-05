"""add accident_timeline_events and timeline_event_claims tables

Revision ID: 0018_accident_timeline_events
Revises: 0017
Create Date: 2026-05-05
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0018_accident_timeline_events"
down_revision = "0017_duplicate_merge_operations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── accident_timeline_events ───────────────────────────────────────────────
    op.create_table(
        "accident_timeline_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "accident_event_id",
            sa.String(36),
            sa.ForeignKey("accident_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Classification
        sa.Column("event_type", sa.String(60), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("category", sa.String(60), nullable=True),
        sa.Column("phase_of_flight", sa.String(60), nullable=True),
        # Temporal — at most one strategy populated per event
        sa.Column("event_time_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_time_local", sa.DateTime(timezone=False), nullable=True),
        sa.Column("relative_offset_seconds", sa.Integer, nullable=True),
        sa.Column("sequence_index", sa.Integer, nullable=True),
        sa.Column(
            "time_precision",
            sa.String(20),
            nullable=False,
            server_default="unknown",
        ),
        # Quality
        sa.Column("severity", sa.String(20), nullable=True),
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
        "ix_timeline_event_accident",
        "accident_timeline_events",
        ["accident_event_id"],
    )
    op.create_index(
        "ix_timeline_event_time_utc",
        "accident_timeline_events",
        ["event_time_utc"],
    )
    op.create_index(
        "ix_timeline_event_seq",
        "accident_timeline_events",
        ["accident_event_id", "sequence_index"],
    )

    # ── timeline_event_claims (join table) ─────────────────────────────────────
    op.create_table(
        "timeline_event_claims",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "timeline_event_id",
            sa.String(36),
            sa.ForeignKey("accident_timeline_events.id", ondelete="CASCADE"),
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
            "timeline_event_id",
            "claim_id",
            name="uq_timeline_event_claim",
        ),
    )
    op.create_index(
        "ix_timeline_event_claims_event",
        "timeline_event_claims",
        ["timeline_event_id"],
    )
    op.create_index(
        "ix_timeline_event_claims_claim",
        "timeline_event_claims",
        ["claim_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_timeline_event_claims_claim", table_name="timeline_event_claims")
    op.drop_index("ix_timeline_event_claims_event", table_name="timeline_event_claims")
    op.drop_table("timeline_event_claims")
    op.drop_index("ix_timeline_event_seq", table_name="accident_timeline_events")
    op.drop_index("ix_timeline_event_time_utc", table_name="accident_timeline_events")
    op.drop_index("ix_timeline_event_accident", table_name="accident_timeline_events")
    op.drop_table("accident_timeline_events")
