"""initial schema

Revision ID: 0001
Revises:
Create Date: 2024-01-01 00:00:00.000000

Creates the full Aviation Safety Atlas schema:
  sources               — source registry (tier, license, ingestion config)
  raw_snapshots         — immutable ingest archive (dedup via payload_hash)
  accident_events       — one row per real-world event (canonical ID, PostGIS)
  claims                — field-level assertions per source per event
  claim_history         — immutable audit trail for every claim mutation
  claim_conflicts       — detected disagreements between source claims
  accident_records      — denormalised read-projection rebuilt from winning claims
  source_documents      — verified links to official reports/dockets

PostGIS geometry columns use SRID 4326 (WGS-84).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── PostGIS extension (idempotent) ────────────────────────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    # ── sources ───────────────────────────────────────────────────────────────
    op.create_table(
        "sources",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("short_name", sa.String(20), nullable=False, unique=True),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("tier", sa.Integer(), nullable=False),
        sa.Column("license_type", sa.String(50), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("ingestion_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # ── accident_events ───────────────────────────────────────────────────────
    # location_point (PostGIS geometry) is added via raw SQL after table
    # creation — see the "PostGIS geometry columns" section below.
    op.create_table(
        "accident_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("canonical_id", sa.String(100), nullable=False, unique=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("occurred_at_precision", sa.String(10), nullable=False, server_default="day"),
        sa.Column("location_text", sa.Text(), nullable=True),
        sa.Column("country_code", sa.String(3), nullable=True),
        sa.Column("overall_confidence_score", sa.Numeric(4, 3), nullable=True),
        sa.Column("record_status", sa.String(20), nullable=False, server_default="active"),
        sa.Column(
            "merged_into_id",
            sa.String(36),
            sa.ForeignKey("accident_events.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # ── raw_snapshots ─────────────────────────────────────────────────────────
    op.create_table(
        "raw_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "source_id",
            sa.String(36),
            sa.ForeignKey("sources.id"),
            nullable=False,
        ),
        sa.Column("source_record_id", sa.String(200), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("ingestion_run_id", sa.String(36), nullable=True),
        sa.UniqueConstraint("source_id", "payload_hash", name="uq_snapshot_source_hash"),
    )
    op.create_index(
        "ix_snapshot_source_record",
        "raw_snapshots",
        ["source_id", "source_record_id"],
    )

    # ── claims ────────────────────────────────────────────────────────────────
    op.create_table(
        "claims",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "event_id",
            sa.String(36),
            sa.ForeignKey("accident_events.id"),
            nullable=False,
        ),
        sa.Column(
            "source_id",
            sa.String(36),
            sa.ForeignKey("sources.id"),
            nullable=False,
        ),
        sa.Column(
            "snapshot_id",
            sa.String(36),
            sa.ForeignKey("raw_snapshots.id"),
            nullable=True,
        ),
        sa.Column("field_name", sa.String(100), nullable=False),
        sa.Column("field_value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("claim_type", sa.String(20), nullable=False, server_default="confirmed"),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_winning", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_claim_event_field", "claims", ["event_id", "field_name"])
    op.create_index("ix_claim_winning", "claims", ["event_id", "is_winning"])

    # ── claim_history ─────────────────────────────────────────────────────────
    op.create_table(
        "claim_history",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "claim_id",
            sa.String(36),
            sa.ForeignKey("claims.id"),
            nullable=False,
        ),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("old_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("new_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("old_claim_type", sa.String(20), nullable=True),
        sa.Column("new_claim_type", sa.String(20), nullable=True),
        sa.Column("change_reason", sa.Text(), nullable=True),
        sa.Column("changed_by", sa.String(100), nullable=True),
    )

    # ── claim_conflicts ───────────────────────────────────────────────────────
    op.create_table(
        "claim_conflicts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "event_id",
            sa.String(36),
            sa.ForeignKey("accident_events.id"),
            nullable=False,
        ),
        sa.Column("field_name", sa.String(100), nullable=False),
        sa.Column(
            "claim_a_id",
            sa.String(36),
            sa.ForeignKey("claims.id"),
            nullable=False,
        ),
        sa.Column(
            "claim_b_id",
            sa.String(36),
            sa.ForeignKey("claims.id"),
            nullable=False,
        ),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_conflict_event_field", "claim_conflicts", ["event_id", "field_name"]
    )

    # ── accident_records ──────────────────────────────────────────────────────
    op.create_table(
        "accident_records",
        sa.Column(
            "id",
            sa.String(36),
            sa.ForeignKey("accident_events.id"),
            primary_key=True,
        ),
        # Temporal
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("occurred_date", sa.Date(), nullable=True),
        sa.Column("occurred_year", sa.Integer(), nullable=True),
        # Spatial
        sa.Column("location_text", sa.Text(), nullable=True),
        sa.Column("location_lat", sa.Numeric(9, 6), nullable=True),
        sa.Column("location_lon", sa.Numeric(9, 6), nullable=True),
        sa.Column("country_code", sa.String(3), nullable=True),
        sa.Column("state_code", sa.String(10), nullable=True),
        # Aircraft
        sa.Column("aircraft_make", sa.String(200), nullable=True),
        sa.Column("aircraft_model", sa.String(200), nullable=True),
        sa.Column("aircraft_registration", sa.String(20), nullable=True),
        sa.Column("aircraft_amateur_built", sa.Boolean(), nullable=True),
        # Operator
        sa.Column("operator_name", sa.String(300), nullable=True),
        # Flight
        sa.Column("phase_of_flight", sa.String(50), nullable=True),
        sa.Column("purpose_of_flight", sa.String(100), nullable=True),
        sa.Column("weather_condition", sa.String(20), nullable=True),
        # Outcome
        sa.Column("injury_severity", sa.String(20), nullable=True),
        sa.Column("fatalities_total", sa.Integer(), nullable=True),
        sa.Column("fatalities_crew", sa.Integer(), nullable=True),
        sa.Column("serious_injuries", sa.Integer(), nullable=True),
        sa.Column("minor_injuries", sa.Integer(), nullable=True),
        sa.Column("aboard_total", sa.Integer(), nullable=True),
        sa.Column("aircraft_damage", sa.String(20), nullable=True),
        # Investigation
        sa.Column("investigation_status", sa.String(30), nullable=True),
        sa.Column("probable_cause", sa.Text(), nullable=True),
        sa.Column("contributing_factors", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("ntsb_report_number", sa.String(50), nullable=True),
        # Provenance
        sa.Column("source_ids", postgresql.ARRAY(sa.String(36)), nullable=True),
        sa.Column(
            "primary_source_id",
            sa.String(36),
            sa.ForeignKey("sources.id"),
            nullable=True,
        ),
        sa.Column("confidence_score", sa.Numeric(4, 3), nullable=True),
        sa.Column(
            "confidence_breakdown",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="Stored factor list so API can expose full score explanation",
        ),
        sa.Column("has_conflicts", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "last_projected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_record_occurred_at", "accident_records", ["occurred_at"])
    op.create_index("ix_record_severity", "accident_records", ["injury_severity"])
    op.create_index("ix_record_confidence", "accident_records", ["confidence_score"])

    # ── source_documents ──────────────────────────────────────────────────────
    op.create_table(
        "source_documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "event_id",
            sa.String(36),
            sa.ForeignKey("accident_events.id"),
            nullable=False,
        ),
        sa.Column(
            "source_id",
            sa.String(36),
            sa.ForeignKey("sources.id"),
            nullable=False,
        ),
        sa.Column("document_type", sa.String(30), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column(
            "url_verified",
            sa.Boolean(),
            nullable=False,
            server_default="false",
            comment="True only after a successful HTTP HEAD check",
        ),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("published_at", sa.Date(), nullable=True),
        sa.Column("is_available", sa.Boolean(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # ── PostGIS geometry columns ───────────────────────────────────────────────
    # Added after table creation because SQLAlchemy DDL rendering for GeoAlchemy2
    # geometry columns is dialect-specific and safest via raw SQL.
    op.execute(
        "ALTER TABLE accident_events ADD COLUMN location_point geometry(POINT,4326)"
    )
    op.execute(
        "ALTER TABLE accident_records ADD COLUMN location_point geometry(POINT,4326)"
    )

    # ── Spatial indexes (GiST) ────────────────────────────────────────────────
    op.execute(
        "CREATE INDEX ix_event_location ON accident_events USING gist (location_point)"
    )
    op.execute(
        "CREATE INDEX ix_record_location ON accident_records USING gist (location_point)"
    )
    op.create_index("ix_event_occurred_at", "accident_events", ["occurred_at"])

    # ── Required seed data ────────────────────────────────────────────────────
    # Source registry rows are FK targets for raw_snapshots and claims.
    # Without them ingestion fails immediately. They live here — not only in
    # the CLI seed command — so 'alembic upgrade head' produces a usable DB.
    op.execute("""
        INSERT INTO sources (
            id, short_name, display_name, tier, license_type,
            base_url, description, ingestion_enabled
        ) VALUES (
            'src-ntsb-001',
            'NTSB',
            'National Transportation Safety Board',
            1,
            'public_domain',
            'https://www.ntsb.gov',
            'Primary US aviation accident investigation authority. '
            'Data is public domain under 49 U.S.C. § 1154.',
            true
        ) ON CONFLICT (id) DO NOTHING
    """)


def downgrade() -> None:
    op.drop_table("source_documents")
    op.drop_table("claim_history")
    op.drop_table("claim_conflicts")
    op.drop_table("accident_records")
    op.drop_table("claims")
    op.drop_table("raw_snapshots")
    op.drop_table("accident_events")
    op.drop_table("sources")
    op.execute("DROP EXTENSION IF EXISTS postgis")
