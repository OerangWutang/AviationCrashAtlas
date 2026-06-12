"""Add html_content column to report_exports for immutable artifact storage.

Revision ID: 051
Revises: 050
Create Date: 2026-06-03
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "051"
down_revision = "050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "report_exports",
        sa.Column("html_content", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("report_exports", "html_content")
