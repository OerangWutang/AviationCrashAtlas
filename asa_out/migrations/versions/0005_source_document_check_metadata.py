"""Add HTTP check metadata to source_documents.

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-29

Changes
-------
1. source_documents.last_http_status  — INTEGER, nullable
   HTTP status code from the most recent check-links run (e.g. 200, 404, 403).
   A boolean is_available is not enough to debug why a document is unavailable.

2. source_documents.last_check_error  — TEXT, nullable
   Error message or failure reason when the HTTP check fails (e.g. timeout,
   connection refused, SSL error, GET fallback error).

3. source_documents.last_check_method — VARCHAR(10), nullable
   HTTP method used: "HEAD" (default) or "GET" (HEAD→GET fallback for servers
   that block HEAD with 403/405/501).

These three fields are populated by the `atlas check-links` CLI command.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "source_documents",
        sa.Column("last_http_status", sa.Integer(), nullable=True,
                  comment="HTTP status code from most recent check (e.g. 200, 404, 403)"),
    )
    op.add_column(
        "source_documents",
        sa.Column("last_check_error", sa.Text(), nullable=True,
                  comment="Error message or failure reason from most recent check"),
    )
    op.add_column(
        "source_documents",
        sa.Column("last_check_method", sa.String(10), nullable=True,
                  comment="HTTP method used: HEAD or GET (HEAD→GET fallback)"),
    )


def downgrade() -> None:
    op.drop_column("source_documents", "last_check_method")
    op.drop_column("source_documents", "last_check_error")
    op.drop_column("source_documents", "last_http_status")
