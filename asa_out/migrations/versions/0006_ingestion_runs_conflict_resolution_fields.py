"""Add ingestion_runs table and structured conflict resolution fields.

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-29

Changes
-------
1. New table: ingestion_runs
   Durable operational ledger for each ingestion run. Replaces the in-memory
   IngestionResult dataclass as the source of truth for run history.

   Columns:
     id, source_id (FK→sources, nullable), source_name, status
     started_at, completed_at
     records_fetched, snapshots_new, snapshots_skipped
     events_created, events_updated, claims_written
     projection_errors, ingestion_errors, errors (JSONB)

2. claim_conflicts: add structured resolution fields
   resolution_type   — VARCHAR(30): claim_accepted | claim_rejected | claims_merged | ...
   accepted_claim_id — VARCHAR(36): which claim was accepted as authoritative
   rejected_claim_ids — TEXT[]: which claims were explicitly rejected

   These fields make auto-reconciliation safe: a claim that appears in
   rejected_claim_ids must not be reinstated, and accepted_claim_id lets
   the system distinguish "accepted" from "rejected without a winner".
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. ingestion_runs table ────────────────────────────────────────────────
    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_id", sa.String(36),
                  sa.ForeignKey("sources.id"), nullable=True),
        sa.Column("source_name", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("records_fetched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("snapshots_new", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("snapshots_skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("events_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("events_updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claims_written", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("projection_errors", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ingestion_errors", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("errors", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index("ix_ingestion_runs_started_at", "ingestion_runs", ["started_at"])
    op.create_index("ix_ingestion_runs_status", "ingestion_runs", ["status"])

    # ── 2. claim_conflicts: structured resolution fields ───────────────────────
    op.add_column(
        "claim_conflicts",
        sa.Column(
            "resolution_type", sa.String(30), nullable=True,
            comment=(
                "claim_accepted | claim_rejected | claims_merged | "
                "source_corrected | not_applicable | manual_override"
            ),
        ),
    )
    op.add_column(
        "claim_conflicts",
        sa.Column(
            "accepted_claim_id", sa.String(36), nullable=True,
            comment="The claim whose value was accepted as authoritative",
        ),
    )
    op.add_column(
        "claim_conflicts",
        sa.Column(
            "rejected_claim_ids",
            postgresql.ARRAY(sa.String(36)),
            nullable=True,
            comment="Claims explicitly rejected during resolution",
        ),
    )


def downgrade() -> None:
    op.drop_column("claim_conflicts", "rejected_claim_ids")
    op.drop_column("claim_conflicts", "accepted_claim_id")
    op.drop_column("claim_conflicts", "resolution_type")
    op.drop_index("ix_ingestion_runs_status", table_name="ingestion_runs")
    op.drop_index("ix_ingestion_runs_started_at", table_name="ingestion_runs")
    op.drop_table("ingestion_runs")
