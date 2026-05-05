"""Add partial B-tree index on location_lat, location_lon for map bbox queries.

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-04

Without this index every bounding-box map query performs a full scan of
accident_records.  On a dataset of 100k+ rows this becomes a blocking
problem.  A partial B-tree index on (location_lat, location_lon) WHERE
both columns are non-NULL supports range predicates on lat/lon directly.

A PostGIS GIST index on a geometry column would be better long-term (for
distance queries and clustering), but a B-tree partial index is sufficient
for the BETWEEN predicates used in the current map endpoint and requires no
PostGIS extension beyond what is already installed.
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_accident_records_lat_lon",
        "accident_records",
        ["location_lat", "location_lon"],
        postgresql_where=sa.text(
            "location_lat IS NOT NULL AND location_lon IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_accident_records_lat_lon",
        table_name="accident_records",
    )
