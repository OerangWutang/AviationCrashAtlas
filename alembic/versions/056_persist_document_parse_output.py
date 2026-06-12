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

_ACTION_CHECK = (
    "action IN ('LEGAL_HOLD_APPLIED', 'LEGAL_HOLD_RELEASED', "
    "'REDACTION_APPLIED', 'RETENTION_SET', 'DELETION_APPLIED', 'UPLOADED')"
)
_OLD_ACTION_CHECK = (
    "action IN ('LEGAL_HOLD_APPLIED', 'LEGAL_HOLD_RELEASED', "
    "'REDACTION_APPLIED', 'RETENTION_SET', 'DELETION_APPLIED')"
)


def upgrade() -> None:
    op.add_column("uploaded_documents", sa.Column("parse_note", sa.Text(), nullable=True))
    op.add_column("uploaded_documents", sa.Column("extracted_text", sa.Text(), nullable=True))
    op.drop_constraint(
        "ck_compliance_events_action",
        "compliance_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_compliance_events_action",
        "compliance_events",
        _ACTION_CHECK,
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_compliance_events_action",
        "compliance_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_compliance_events_action",
        "compliance_events",
        _OLD_ACTION_CHECK,
    )
    op.drop_column("uploaded_documents", "extracted_text")
    op.drop_column("uploaded_documents", "parse_note")
