"""record duplicate merge operations for reversible reviewer merges

Revision ID: 0017_duplicate_merge_operations
Revises: 0016
Create Date: 2026-05-04
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0017_duplicate_merge_operations"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "duplicate_merge_operations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("duplicate_candidate_id", sa.String(length=36), sa.ForeignKey("duplicate_candidates.id"), nullable=False),
        sa.Column("source_event_id", sa.String(length=36), sa.ForeignKey("accident_events.id"), nullable=False),
        sa.Column("target_event_id", sa.String(length=36), sa.ForeignKey("accident_events.id"), nullable=False),
        sa.Column("moved_claim_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("moved_document_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("moved_revision_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("moved_conflict_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("moved_issue_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("undone_by", sa.String(length=100), nullable=True),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("undo_note", sa.Text(), nullable=True),
    )
    op.create_index("ix_duplicate_merge_operations_candidate", "duplicate_merge_operations", ["duplicate_candidate_id"])
    op.create_index("ix_duplicate_merge_operations_events", "duplicate_merge_operations", ["source_event_id", "target_event_id"])

    op.create_table(
        "claim_source_documents",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("claim_id", sa.String(length=36), sa.ForeignKey("claims.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_document_id", sa.String(length=36), sa.ForeignKey("source_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("link_reason", sa.String(length=80), nullable=False, server_default="source_event_final_report"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("claim_id", "source_document_id", name="uq_claim_source_document_pair"),
    )
    op.create_index("ix_claim_source_documents_claim", "claim_source_documents", ["claim_id"])
    op.create_index("ix_claim_source_documents_doc", "claim_source_documents", ["source_document_id"])


def downgrade() -> None:
    op.drop_index("ix_claim_source_documents_doc", table_name="claim_source_documents")
    op.drop_index("ix_claim_source_documents_claim", table_name="claim_source_documents")
    op.drop_table("claim_source_documents")
    op.drop_index("ix_duplicate_merge_operations_events", table_name="duplicate_merge_operations")
    op.drop_index("ix_duplicate_merge_operations_candidate", table_name="duplicate_merge_operations")
    op.drop_table("duplicate_merge_operations")
