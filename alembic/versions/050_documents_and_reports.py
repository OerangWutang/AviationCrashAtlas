"""Add uploaded_documents and report_exports tables.

Revision ID: 050
Revises: 049_fk_covering_indexes
Create Date: 2026-05-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "050"
down_revision = "049_fk_covering_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "uploaded_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sources.id"),
            nullable=True,
        ),
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accident_events.id"),
            nullable=True,
        ),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False, server_default="application/pdf"),
        sa.Column("page_count", sa.Integer, nullable=True),
        sa.Column("parse_status", sa.String(50), nullable=False, server_default="uploaded"),
        sa.Column("object_key", sa.Text, nullable=True),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), nullable=True),
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
        sa.CheckConstraint(
            "parse_status IN ('uploaded','parsing','parsed','parse_failed',"
            "'ingesting','ingested','ingest_failed')",
            name="ck_uploaded_documents_status",
        ),
        sa.CheckConstraint("size_bytes >= 0", name="ck_uploaded_documents_size_nonneg"),
    )
    op.create_index("ix_uploaded_documents_event_id", "uploaded_documents", ["event_id"])
    op.create_index("ix_uploaded_documents_source_id", "uploaded_documents", ["source_id"])
    op.create_index(
        "ix_uploaded_documents_created",
        "uploaded_documents",
        [sa.text("created_at DESC")],
    )

    op.create_table(
        "report_exports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accident_events.id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("projection_version", sa.Integer, nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("object_key", sa.Text, nullable=True),
        sa.Column("generated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("event_id", "version", name="uq_report_exports_event_version"),
    )
    op.create_index("ix_report_exports_event_id", "report_exports", ["event_id"])
    op.create_index(
        "ix_report_exports_generated",
        "report_exports",
        [sa.text("generated_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_report_exports_generated", "report_exports")
    op.drop_index("ix_report_exports_event_id", "report_exports")
    op.drop_table("report_exports")
    op.drop_index("ix_uploaded_documents_created", "uploaded_documents")
    op.drop_index("ix_uploaded_documents_source_id", "uploaded_documents")
    op.drop_index("ix_uploaded_documents_event_id", "uploaded_documents")
    op.drop_table("uploaded_documents")
