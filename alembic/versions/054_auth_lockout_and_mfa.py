"""Brute-force lockout + per-key TOTP MFA.

Revision ID: 054
Revises: 053
Create Date: 2026-06-06

Implements TICKET-009 (Option B — hardening on the existing API-key
surface, no human-login subsystem).

Two additions:

1. ``api_key_attempts`` table — append-only log of every key-auth attempt
   (success or fail) keyed by client IP.  The auth path queries the last
   N minutes to decide whether to short-circuit further attempts from a
   misbehaving IP, returning ``429`` so the caller is told to back off
   rather than receiving the timing signal "valid prefix, wrong hash"
   that bare 401s leak.

2. New columns on ``api_keys`` for per-key TOTP MFA:

   - ``mfa_required`` (bool) — when true, destructive endpoints require an
     ``X-MFA-Code`` header validated against ``mfa_secret_encrypted``.
   - ``mfa_secret_encrypted`` (bytea) — AES-256-GCM ciphertext of the TOTP
     seed.  Nonce + tag are packed into the value so the column is fully
     self-describing (see :func:`atlas.security.mfa.encrypt_seed`).  Never
     decrypt without the application's MFA_KEK; an attacker with DB read
     access cannot derive valid TOTP codes from this column alone.
   - ``mfa_enrolled_at`` / ``mfa_enrolled_by`` — provenance for the
     enrollment, used by the audit trail and surfaced in the CLI.

Both pieces are additive and reversible.  Existing keys default to
``mfa_required = false`` so the migration is no-op for callers that have
not adopted MFA yet.

Why not just a Redis rate-limiter for (1)?
------------------------------------------

Atlas does not require Redis in the default deployment — the free/self-
hosted topology runs Postgres only.  Storing attempts in Postgres keeps
the security-critical path inside the database that already has the
audit chain, RLS, and backups; the trade-off is a small write per auth
attempt.  At Atlas's scale (small operator + admin pool) the row volume
is negligible; the cleanup task (TICKET-013) sweeps old rows alongside
the existing retention sweeps.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "054"
down_revision = "053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_key_attempts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # INET so the auth path can match by exact host, and so range
        # queries (e.g. /24 subnet rollups for forensics) remain cheap.
        sa.Column("client_ip", postgresql.INET(), nullable=False),
        sa.Column(
            "attempted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        # The first eight characters of the submitted key, for forensic
        # correlation only — never enough to reconstruct a valid key.
        # NULL when the request omitted the header entirely.
        sa.Column("key_prefix", sa.String(length=8), nullable=True),
        sa.CheckConstraint(
            "char_length(key_prefix) <= 8",
            name="ck_api_key_attempts_key_prefix_len",
        ),
    )
    # Lookups are always "recent attempts from this IP" so the composite
    # index serves both the lockout check and the cleanup sweeper.
    op.create_index(
        "ix_api_key_attempts_ip_time",
        "api_key_attempts",
        ["client_ip", sa.text("attempted_at DESC")],
    )
    # Separate index on succeeded=false for the lockout query which only
    # cares about failures.  Partial keeps the index small on a healthy
    # system (most attempts succeed).
    op.create_index(
        "ix_api_key_attempts_failures",
        "api_key_attempts",
        ["client_ip", sa.text("attempted_at DESC")],
        postgresql_where=sa.text("succeeded = false"),
    )

    op.add_column(
        "api_keys",
        sa.Column(
            "mfa_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "api_keys",
        sa.Column("mfa_secret_encrypted", postgresql.BYTEA(), nullable=True),
    )
    op.add_column(
        "api_keys",
        sa.Column("mfa_enrolled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "api_keys",
        sa.Column(
            "mfa_enrolled_by",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    # When a key has mfa_required = true the secret column must be
    # populated; otherwise the auth path could not verify codes.  The
    # CHECK keeps the application from drifting into a state where MFA
    # is "on" but no seed exists, which would soft-brick the key.
    op.create_check_constraint(
        "ck_api_keys_mfa_consistency",
        "api_keys",
        "(mfa_required = false) OR (mfa_secret_encrypted IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_api_keys_mfa_consistency", "api_keys", type_="check")
    op.drop_column("api_keys", "mfa_enrolled_by")
    op.drop_column("api_keys", "mfa_enrolled_at")
    op.drop_column("api_keys", "mfa_secret_encrypted")
    op.drop_column("api_keys", "mfa_required")
    op.drop_index("ix_api_key_attempts_failures", table_name="api_key_attempts")
    op.drop_index("ix_api_key_attempts_ip_time", table_name="api_key_attempts")
    op.drop_table("api_key_attempts")
