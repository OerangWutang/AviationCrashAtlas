"""Persist uploaded document parse output.

Revision ID: 056
Revises: 055
Create Date: 2026-06-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "056"
down_revision = "055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("uploaded_documents", sa.Column("parse_note", sa.Text(), nullable=True))
    op.add_column("uploaded_documents", sa.Column("extracted_text", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("uploaded_documents", "extracted_text")
    op.drop_column("uploaded_documents", "parse_note")
