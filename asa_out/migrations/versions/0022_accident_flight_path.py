"""add accident flight path reconstruction tables

Revision ID: 0022_accident_flight_path
Revises: 0021_advanced_analytics
Create Date: 2026-05-05

Tables created:
  accident_flight_path_points       — individual position fixes
  flight_path_point_claims          — point ↔ claim provenance
  accident_flight_path_segments     — derived segments between points
  accident_flight_path_annotations  — event annotations along the path
  flight_path_annotation_claims     — annotation ↔ claim provenance
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0022_accident_flight_path"
down_revision = "0021_advanced_analytics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── accident_flight_path_points ────────────────────────────────────────────
    op.create_table(
        "accident_flight_path_points",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "accident_event_id", sa.String(36),
            sa.ForeignKey("accident_events.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("source_id", sa.String(36), sa.ForeignKey("sources.id"), nullable=True),
        # Temporal
        sa.Column("sequence_index", sa.Integer, nullable=True),
        sa.Column("recorded_time_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("relative_offset_seconds", sa.Integer, nullable=True),
        sa.Column("time_precision", sa.String(20), nullable=False, server_default="unknown"),
        # Position
        sa.Column("latitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("longitude", sa.Numeric(10, 7), nullable=True),
        # Altitude
        sa.Column("altitude_ft", sa.Numeric(8, 1), nullable=True),
        sa.Column("altitude_reference", sa.String(20), nullable=True),
        sa.Column("radio_altitude_ft", sa.Numeric(8, 1), nullable=True),
        # Speed / motion
        sa.Column("ground_speed_kt", sa.Numeric(6, 1), nullable=True),
        sa.Column("indicated_airspeed_kt", sa.Numeric(6, 1), nullable=True),
        sa.Column("vertical_speed_fpm", sa.Numeric(7, 1), nullable=True),
        sa.Column("heading_degrees", sa.Numeric(5, 2), nullable=True),
        sa.Column("track_degrees", sa.Numeric(5, 2), nullable=True),
        # Derived
        sa.Column("distance_to_impact_km", sa.Numeric(8, 3), nullable=True),
        sa.Column("uncertainty_radius_m", sa.Numeric(9, 1), nullable=True),
        # Classification
        sa.Column("point_type", sa.String(30), nullable=False, server_default="unknown"),
        sa.Column("source_method", sa.String(30), nullable=True),
        # Quality
        sa.Column("confidence_score", sa.Numeric(4, 3), nullable=True),
        sa.Column("is_disputed", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("dispute_summary", sa.Text, nullable=True),
        sa.Column("raw_data", sa.JSON, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        # Audit
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_fp_point_accident", "accident_flight_path_points", ["accident_event_id"])
    op.create_index("ix_fp_point_time", "accident_flight_path_points", ["recorded_time_utc"])
    op.create_index("ix_fp_point_type", "accident_flight_path_points", ["point_type"])
    op.create_index("ix_fp_point_seq", "accident_flight_path_points", ["accident_event_id", "sequence_index"])

    # ── flight_path_point_claims ───────────────────────────────────────────────
    op.create_table(
        "flight_path_point_claims",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "flight_path_point_id", sa.String(36),
            sa.ForeignKey("accident_flight_path_points.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "claim_id", sa.String(36),
            sa.ForeignKey("claims.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("link_reason", sa.String(80), nullable=False, server_default="supporting_claim"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("flight_path_point_id", "claim_id", name="uq_fp_point_claim"),
    )
    op.create_index("ix_fp_point_claims_point", "flight_path_point_claims", ["flight_path_point_id"])
    op.create_index("ix_fp_point_claims_claim", "flight_path_point_claims", ["claim_id"])

    # ── accident_flight_path_segments ──────────────────────────────────────────
    op.create_table(
        "accident_flight_path_segments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "accident_event_id", sa.String(36),
            sa.ForeignKey("accident_events.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "start_point_id", sa.String(36),
            sa.ForeignKey("accident_flight_path_points.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "end_point_id", sa.String(36),
            sa.ForeignKey("accident_flight_path_points.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("segment_type", sa.String(20), nullable=False, server_default="unknown"),
        sa.Column("length_km", sa.Numeric(8, 3), nullable=True),
        sa.Column("bearing_degrees", sa.Numeric(5, 2), nullable=True),
        sa.Column("confidence_score", sa.Numeric(4, 3), nullable=True),
        sa.Column("is_disputed", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("uncertainty_summary", sa.Text, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_fp_segment_accident", "accident_flight_path_segments", ["accident_event_id"])

    # ── accident_flight_path_annotations ──────────────────────────────────────
    op.create_table(
        "accident_flight_path_annotations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "accident_event_id", sa.String(36),
            sa.ForeignKey("accident_events.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "flight_path_point_id", sa.String(36),
            sa.ForeignKey("accident_flight_path_points.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "timeline_event_id", sa.String(36),
            sa.ForeignKey("accident_timeline_events.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("source_id", sa.String(36), sa.ForeignKey("sources.id"), nullable=True),
        # Temporal
        sa.Column("annotation_time_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("relative_offset_seconds", sa.Integer, nullable=True),
        sa.Column("time_precision", sa.String(20), nullable=False, server_default="unknown"),
        # Content
        sa.Column("annotation_type", sa.String(30), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        # Position snapshot
        sa.Column("altitude_ft", sa.Numeric(8, 1), nullable=True),
        sa.Column("radio_altitude_ft", sa.Numeric(8, 1), nullable=True),
        # Quality
        sa.Column("confidence_score", sa.Numeric(4, 3), nullable=True),
        sa.Column("is_disputed", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("dispute_summary", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_fp_annotation_accident", "accident_flight_path_annotations", ["accident_event_id"])
    op.create_index("ix_fp_annotation_type", "accident_flight_path_annotations", ["annotation_type"])
    op.create_index("ix_fp_annotation_time", "accident_flight_path_annotations", ["annotation_time_utc"])

    # ── flight_path_annotation_claims ──────────────────────────────────────────
    op.create_table(
        "flight_path_annotation_claims",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "annotation_id", sa.String(36),
            sa.ForeignKey("accident_flight_path_annotations.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "claim_id", sa.String(36),
            sa.ForeignKey("claims.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("link_reason", sa.String(80), nullable=False, server_default="supporting_claim"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("annotation_id", "claim_id", name="uq_fp_annotation_claim"),
    )
    op.create_index("ix_fp_annotation_claims_ann", "flight_path_annotation_claims", ["annotation_id"])
    op.create_index("ix_fp_annotation_claims_claim", "flight_path_annotation_claims", ["claim_id"])


def downgrade() -> None:
    op.drop_index("ix_fp_annotation_claims_claim", table_name="flight_path_annotation_claims")
    op.drop_index("ix_fp_annotation_claims_ann", table_name="flight_path_annotation_claims")
    op.drop_table("flight_path_annotation_claims")
    op.drop_index("ix_fp_annotation_time", table_name="accident_flight_path_annotations")
    op.drop_index("ix_fp_annotation_type", table_name="accident_flight_path_annotations")
    op.drop_index("ix_fp_annotation_accident", table_name="accident_flight_path_annotations")
    op.drop_table("accident_flight_path_annotations")
    op.drop_index("ix_fp_segment_accident", table_name="accident_flight_path_segments")
    op.drop_table("accident_flight_path_segments")
    op.drop_index("ix_fp_point_claims_claim", table_name="flight_path_point_claims")
    op.drop_index("ix_fp_point_claims_point", table_name="flight_path_point_claims")
    op.drop_table("flight_path_point_claims")
    op.drop_index("ix_fp_point_seq", table_name="accident_flight_path_points")
    op.drop_index("ix_fp_point_type", table_name="accident_flight_path_points")
    op.drop_index("ix_fp_point_time", table_name="accident_flight_path_points")
    op.drop_index("ix_fp_point_accident", table_name="accident_flight_path_points")
    op.drop_table("accident_flight_path_points")
