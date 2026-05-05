"""Add crew/passenger fatality and injury split projection fields.

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-04

The read projection can now preserve source-provided crew/passenger split
counts for fatalities, serious injuries, minor injuries, and uninjured people.
These columns are nullable because many sources only provide totals; NULL means
"not sourced", not zero.
"""

from alembic import op
import sqlalchemy as sa


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


_SPLIT_COLUMNS = (
    "fatalities_crew",
    "fatalities_passengers",
    "serious_injuries_crew",
    "serious_injuries_passengers",
    "minor_injuries_crew",
    "minor_injuries_passengers",
    "uninjured_crew",
    "uninjured_passengers",
)


def upgrade() -> None:
    for column_name in _SPLIT_COLUMNS:
        op.add_column("accident_records", sa.Column(column_name, sa.Integer(), nullable=True))


def downgrade() -> None:
    for column_name in reversed(_SPLIT_COLUMNS):
        op.drop_column("accident_records", column_name)
