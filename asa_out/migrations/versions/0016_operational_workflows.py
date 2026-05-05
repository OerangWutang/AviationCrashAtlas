"""Add duplicate review, data-quality, external-id, and archive manifest tables.

Revision ID: 0016
Revises: 0015
Create Date: 2026-05-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "duplicate_candidates",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("source_event_id", sa.String(length=36), sa.ForeignKey("accident_events.id"), nullable=True),
        sa.Column("candidate_event_id", sa.String(length=36), sa.ForeignKey("accident_events.id"), nullable=False),
        sa.Column("source_id", sa.String(length=36), sa.ForeignKey("sources.id"), nullable=True),
        sa.Column("source_record_id", sa.String(length=200), nullable=True),
        sa.Column("ingestion_run_id", sa.String(length=36), sa.ForeignKey("ingestion_runs.id"), nullable=True),
        sa.Column("match_type", sa.String(length=40), nullable=False, server_default="fuzzy"),
        sa.Column("match_score", sa.Numeric(5, 3), nullable=False),
        sa.Column("match_reasons", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("reviewed_by", sa.String(length=100), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("source_event_id", "candidate_event_id", name="uq_duplicate_candidate_event_pair"),
    )
    op.create_index("ix_duplicate_candidates_status", "duplicate_candidates", ["status", "created_at"])
    op.create_index("ix_duplicate_candidates_events", "duplicate_candidates", ["source_event_id", "candidate_event_id"])

    op.create_table(
        "event_external_ids",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("event_id", sa.String(length=36), sa.ForeignKey("accident_events.id"), nullable=False),
        sa.Column("source_id", sa.String(length=36), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("external_id_type", sa.String(length=50), nullable=False, server_default="source_record_id"),
        sa.Column("external_id", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("source_id", "external_id_type", "external_id", name="uq_event_external_id_source_type_value"),
    )
    op.create_index("ix_event_external_ids_event", "event_external_ids", ["event_id"])

    op.create_table(
        "data_quality_issues",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("event_id", sa.String(length=36), sa.ForeignKey("accident_events.id"), nullable=False),
        sa.Column("source_id", sa.String(length=36), sa.ForeignKey("sources.id"), nullable=True),
        sa.Column("issue_code", sa.String(length=80), nullable=False),
        sa.Column("field_name", sa.String(length=100), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False, server_default="warning"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(length=100), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.UniqueConstraint("event_id", "issue_code", "field_name", "status", name="uq_open_data_quality_issue"),
    )
    op.create_index("ix_data_quality_event_status", "data_quality_issues", ["event_id", "status"])
    op.create_index("ix_data_quality_code", "data_quality_issues", ["issue_code"])

    op.create_table(
        "archive_manifests",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("archive_type", sa.String(length=40), nullable=False, server_default="retention"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="created"),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("output_uri", sa.Text(), nullable=False),
        sa.Column("manifest", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=100), nullable=True),
    )
    op.create_index("ix_archive_manifests_created", "archive_manifests", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_archive_manifests_created", table_name="archive_manifests")
    op.drop_table("archive_manifests")
    op.drop_index("ix_data_quality_code", table_name="data_quality_issues")
    op.drop_index("ix_data_quality_event_status", table_name="data_quality_issues")
    op.drop_table("data_quality_issues")
    op.drop_index("ix_event_external_ids_event", table_name="event_external_ids")
    op.drop_table("event_external_ids")
    op.drop_index("ix_duplicate_candidates_events", table_name="duplicate_candidates")
    op.drop_index("ix_duplicate_candidates_status", table_name="duplicate_candidates")
    op.drop_table("duplicate_candidates")
