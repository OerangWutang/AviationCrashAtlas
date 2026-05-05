"""add accident_weather_observations and weather_observation_claims tables

Revision ID: 0019_accident_weather_observations
Revises: 0018_accident_timeline_events
Create Date: 2026-05-05
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0019_accident_weather_observations"
down_revision = "0018_accident_timeline_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── accident_weather_observations ──────────────────────────────────────────
    op.create_table(
        "accident_weather_observations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "accident_event_id",
            sa.String(36),
            sa.ForeignKey("accident_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_id",
            sa.String(36),
            sa.ForeignKey("sources.id"),
            nullable=True,
        ),
        # Station
        sa.Column("station_identifier", sa.String(10), nullable=True),
        sa.Column("station_name", sa.String(200), nullable=True),
        sa.Column("station_latitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("station_longitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("distance_to_accident_km", sa.Numeric(8, 3), nullable=True),
        # Temporal
        sa.Column("observation_time_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accident_time_delta_minutes", sa.Numeric(8, 2), nullable=True),
        # Report
        sa.Column(
            "report_type",
            sa.String(20),
            nullable=False,
            server_default="metar",
        ),
        sa.Column("raw_report_text", sa.Text, nullable=True),
        sa.Column("parsed_data", sa.JSON, nullable=True),
        # Canonical parsed fields
        sa.Column("temperature_c", sa.Numeric(5, 2), nullable=True),
        sa.Column("dew_point_c", sa.Numeric(5, 2), nullable=True),
        sa.Column("wind_direction_degrees", sa.Integer, nullable=True),
        sa.Column("wind_speed_kt", sa.Numeric(6, 2), nullable=True),
        sa.Column("wind_gust_kt", sa.Numeric(6, 2), nullable=True),
        sa.Column("visibility_m", sa.Numeric(8, 1), nullable=True),
        sa.Column("ceiling_ft", sa.Integer, nullable=True),
        sa.Column("altimeter_hpa", sa.Numeric(7, 2), nullable=True),
        sa.Column("precipitation_type", sa.String(50), nullable=True),
        sa.Column("thunderstorm_present", sa.Boolean, nullable=True),
        sa.Column("icing_risk", sa.String(20), nullable=True),
        sa.Column("turbulence_risk", sa.String(20), nullable=True),
        sa.Column("flight_rules", sa.String(10), nullable=True),
        # Quality
        sa.Column("confidence_score", sa.Numeric(4, 3), nullable=True),
        sa.Column("is_disputed", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("dispute_summary", sa.Text, nullable=True),
        # Audit
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
    )
    op.create_index(
        "ix_weather_obs_accident", "accident_weather_observations", ["accident_event_id"]
    )
    op.create_index(
        "ix_weather_obs_time", "accident_weather_observations", ["observation_time_utc"]
    )
    op.create_index(
        "ix_weather_obs_station", "accident_weather_observations", ["station_identifier"]
    )

    # ── weather_observation_claims (join table) ────────────────────────────────
    op.create_table(
        "weather_observation_claims",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "weather_observation_id",
            sa.String(36),
            sa.ForeignKey("accident_weather_observations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "claim_id",
            sa.String(36),
            sa.ForeignKey("claims.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "link_reason",
            sa.String(80),
            nullable=False,
            server_default="supporting_claim",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "weather_observation_id", "claim_id", name="uq_weather_obs_claim"
        ),
    )
    op.create_index(
        "ix_weather_obs_claims_obs",
        "weather_observation_claims",
        ["weather_observation_id"],
    )
    op.create_index(
        "ix_weather_obs_claims_claim", "weather_observation_claims", ["claim_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_weather_obs_claims_claim", table_name="weather_observation_claims")
    op.drop_index("ix_weather_obs_claims_obs", table_name="weather_observation_claims")
    op.drop_table("weather_observation_claims")
    op.drop_index("ix_weather_obs_station", table_name="accident_weather_observations")
    op.drop_index("ix_weather_obs_time", table_name="accident_weather_observations")
    op.drop_index("ix_weather_obs_accident", table_name="accident_weather_observations")
    op.drop_table("accident_weather_observations")
