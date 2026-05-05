"""add analytics_snapshots, accident_pattern_tags, accident_similarity_scores

Revision ID: 0021_advanced_analytics
Revises: 0020_accident_system_failures
Create Date: 2026-05-05
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0021_advanced_analytics"
down_revision = "0020_accident_system_failures"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── analytics_snapshots ────────────────────────────────────────────────────
    op.create_table(
        "analytics_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("snapshot_type", sa.String(40), nullable=False),
        sa.Column("parameters", sa.JSON, nullable=True),
        sa.Column("result", sa.JSON, nullable=True),
        sa.Column(
            "generated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("generated_by", sa.String(100), nullable=True),
        sa.Column("data_version", sa.String(50), nullable=True),
        sa.Column("source_record_count", sa.Integer, nullable=True),
        sa.Column("low_confidence_count", sa.Integer, nullable=True),
        sa.Column("disputed_count", sa.Integer, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index("ix_analytics_snapshot_type", "analytics_snapshots", ["snapshot_type"])
    op.create_index("ix_analytics_snapshot_generated", "analytics_snapshots", ["generated_at"])

    # ── accident_pattern_tags ──────────────────────────────────────────────────
    op.create_table(
        "accident_pattern_tags",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "accident_event_id", sa.String(36),
            sa.ForeignKey("accident_events.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("tag_type", sa.String(30), nullable=False),
        sa.Column("tag_value", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="unknown"),
        sa.Column("confidence_score", sa.Numeric(4, 3), nullable=True),
        sa.Column("source_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_disputed", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint(
            "accident_event_id", "tag_type", "tag_value", "status",
            name="uq_pattern_tag",
        ),
    )
    op.create_index("ix_pattern_tag_accident", "accident_pattern_tags", ["accident_event_id"])
    op.create_index("ix_pattern_tag_type", "accident_pattern_tags", ["tag_type"])
    op.create_index("ix_pattern_tag_value", "accident_pattern_tags", ["tag_value"])

    # ── accident_similarity_scores ─────────────────────────────────────────────
    op.create_table(
        "accident_similarity_scores",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "accident_event_id", sa.String(36),
            sa.ForeignKey("accident_events.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "similar_accident_id", sa.String(36),
            sa.ForeignKey("accident_events.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("similarity_score", sa.Numeric(5, 4), nullable=False),
        sa.Column("similarity_reasons", sa.JSON, nullable=True),
        sa.Column("shared_factors", sa.JSON, nullable=True),
        sa.Column("differing_factors", sa.JSON, nullable=True),
        sa.Column("confidence_score", sa.Numeric(4, 3), nullable=True),
        sa.Column("low_confidence_warning", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column(
            "generated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index("ix_similarity_accident", "accident_similarity_scores", ["accident_event_id"])
    op.create_index("ix_similarity_score", "accident_similarity_scores", ["similarity_score"])


def downgrade() -> None:
    op.drop_index("ix_similarity_score", table_name="accident_similarity_scores")
    op.drop_index("ix_similarity_accident", table_name="accident_similarity_scores")
    op.drop_table("accident_similarity_scores")
    op.drop_index("ix_pattern_tag_value", table_name="accident_pattern_tags")
    op.drop_index("ix_pattern_tag_type", table_name="accident_pattern_tags")
    op.drop_index("ix_pattern_tag_accident", table_name="accident_pattern_tags")
    op.drop_table("accident_pattern_tags")
    op.drop_index("ix_analytics_snapshot_generated", table_name="analytics_snapshots")
    op.drop_index("ix_analytics_snapshot_type", table_name="analytics_snapshots")
    op.drop_table("analytics_snapshots")
