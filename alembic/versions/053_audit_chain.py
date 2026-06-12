"""Tamper-evident hash chain on audit tables.

Revision ID: 053
Revises: 052
Create Date: 2026-06-06

Implements F-P0-3 (TICKET-004) from the Phase 1 audit: a per-row HMAC
chain on the six append-only audit tables.

Tables protected:

- ``claim_history``
- ``conflict_activity_log``
- ``accident_projection_history``
- ``public_event_page_revisions``
- ``usage_events``
- ``report_exports``

How the chain works
-------------------

Each protected table gains two columns:

- ``prev_hash bytea`` — the previous row's ``row_hash`` (NULL for the
  chain head).
- ``row_hash bytea`` — ``HMAC-SHA256(secret, prev_hash || canonical(NEW))``,
  computed inside a ``BEFORE INSERT`` trigger.

The "canonical" payload is ``to_jsonb(NEW) - 'prev_hash' - 'row_hash'``
serialised as text.  PostgreSQL stores JSONB with sorted keys, so the
serialisation is deterministic for the same column set.

The chain head per table is tracked in ``audit_chain_anchors``; the
trigger ``SELECT ... FOR UPDATE``s the anchor row, so concurrent inserts
on the same table serialise on a single PK lock.  That is the cost of
tamper-evidence.

Why both columns, not just ``prev_hash``
----------------------------------------

The backlog spec called for ``prev_hash`` only and recomputing each
row's hash on demand during verification.  Storing ``row_hash`` makes
the trigger O(1) per insert (reads anchor, hashes once) instead of
O(prior_row_columns) (must re-fetch and re-serialise the prior row).
It also lets the verifier compare stored vs recomputed hashes in a
single pass.  One extra bytea per row is a cheap trade.

The secret
----------

The HMAC key lives in the ``audit.chain_secret`` session GUC (set per
transaction via ``set_config(..., true)`` from the application's
``UnitOfWork``).  The migration reads it from the ``AUDIT_CHAIN_SECRET``
env var to backfill existing rows; if any of the six tables already
contain rows, the env var is required.

Empty-DB deploy
---------------

If all six tables are empty, the migration applies cleanly without
``AUDIT_CHAIN_SECRET``; only the schema and trigger machinery are
created.  The chain begins at the first runtime INSERT once the
session GUC is being set.

REVOKE
------

The migration includes a REVOKE block guarded by ``IF EXISTS`` on a
``atlas_tenant_app`` role so it runs only in deployments that have
already provisioned that role.  Tests and CI run as a role that retains
ALL PRIVILEGES; the chain still detects tamper because the verifier
recomputes from row contents regardless of who wrote them.
"""

from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "053"
down_revision = "052"
branch_labels = None
depends_on = None

# Tables to protect, paired with the column used to order the chain at
# verification time.  Verification ordering is (order_col, id), so the
# id PK breaks ties when many rows share the same timestamp.
_CHAINED_TABLES: tuple[tuple[str, str], ...] = (
    ("claim_history", "created_at"),
    ("conflict_activity_log", "created_at"),
    ("accident_projection_history", "created_at"),
    ("public_event_page_revisions", "created_at"),
    ("usage_events", "recorded_at"),
    ("report_exports", "generated_at"),
)


def upgrade() -> None:
    # pgcrypto provides the ``hmac()`` function used by the trigger and
    # the verifier function.  Idempotent.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "audit_chain_anchors",
        sa.Column("table_name", sa.Text(), primary_key=True),
        sa.Column("order_column", sa.Text(), nullable=False),
        sa.Column("latest_row_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("latest_row_hash", postgresql.BYTEA(), nullable=True),
        sa.Column(
            "row_count",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "table_name IN ('claim_history', 'conflict_activity_log', "
            "'accident_projection_history', 'public_event_page_revisions', "
            "'usage_events', 'report_exports')",
            name="ck_audit_chain_anchors_table",
        ),
    )

    # Seed one anchor row per chained table.  Empty hash/id until first
    # INSERT (or until the migration's backfill runs below).
    for table, order_col in _CHAINED_TABLES:
        op.execute(
            sa.text(
                "INSERT INTO audit_chain_anchors (table_name, order_column) VALUES (:t, :o)"
            ).bindparams(t=table, o=order_col)
        )

    # Add the chain columns to each protected table.
    for table, _ in _CHAINED_TABLES:
        op.add_column(table, sa.Column("prev_hash", postgresql.BYTEA(), nullable=True))
        op.add_column(table, sa.Column("row_hash", postgresql.BYTEA(), nullable=True))

    # The trigger function and the verifier function.  Created BEFORE the
    # backfill so the backfill can call the same canonicalisation logic.
    op.execute(_AUDIT_CHAIN_APPEND_FN)
    op.execute(_AUDIT_CHAIN_VERIFY_FN)
    op.execute(_AUDIT_CHAIN_BACKFILL_FN)

    # Backfill existing rows.  Requires AUDIT_CHAIN_SECRET if any table
    # has rows; the function raises a clear error otherwise.
    secret_hex = _read_secret_hex()
    if secret_hex is not None:
        op.execute(
            sa.text("SELECT set_config('audit.chain_secret', :s, false)").bindparams(s=secret_hex)
        )
        for table, _ in _CHAINED_TABLES:
            op.execute(sa.text("SELECT audit_chain_backfill(:t)").bindparams(t=table))
    else:
        # If any table has rows we cannot proceed; the chain would be
        # unverifiable.  This check is per-table to give a precise error.
        for table, _ in _CHAINED_TABLES:
            conn = op.get_bind()
            count = conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
            if count and count > 0:
                raise RuntimeError(
                    f"Table {table!r} contains {count} row(s) but "
                    "AUDIT_CHAIN_SECRET is not set.  Export a hex-encoded "
                    "32-byte secret (e.g. `openssl rand -hex 32`) and rerun "
                    "the migration.  The same value must be configured in "
                    "the application's AUDIT_CHAIN_SECRET setting."
                )

    # Attach the trigger to each protected table.  AFTER backfill so the
    # backfill itself does not trigger recursion.
    for table, _ in _CHAINED_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_audit_chain
              BEFORE INSERT ON {table}
              FOR EACH ROW EXECUTE FUNCTION audit_chain_append()
            """
        )

    # Conditional REVOKE: tenant app role must not be able to mutate
    # already-written audit rows.  No-op in deployments without that role.
    op.execute(_REVOKE_TENANT_APP_WRITE)


def downgrade() -> None:
    for table, _ in _CHAINED_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_audit_chain ON {table}")
    op.execute("DROP FUNCTION IF EXISTS audit_chain_backfill(text)")
    op.execute("DROP FUNCTION IF EXISTS audit_chain_verify(text)")
    op.execute("DROP FUNCTION IF EXISTS audit_chain_append()")
    for table, _ in _CHAINED_TABLES:
        op.drop_column(table, "row_hash")
        op.drop_column(table, "prev_hash")
    op.drop_table("audit_chain_anchors")
    # pgcrypto is left installed — other migrations may use it.


def _read_secret_hex() -> str | None:
    """Return the hex-encoded HMAC secret from the environment, or None.

    Accepts either:
      * ``AUDIT_CHAIN_SECRET`` — raw bytes (any length), hex-encoded by
        this function.
      * ``AUDIT_CHAIN_SECRET_HEX`` — already-hex-encoded value passed
        through unchanged.

    Returns None when neither is set; the caller decides whether that
    is an error (tables non-empty) or acceptable (fresh DB).
    """
    hex_val = os.environ.get("AUDIT_CHAIN_SECRET_HEX")
    if hex_val:
        return hex_val.strip()
    raw = os.environ.get("AUDIT_CHAIN_SECRET")
    if raw:
        return raw.encode("utf-8").hex()
    return None


_AUDIT_CHAIN_APPEND_FN = r"""
CREATE OR REPLACE FUNCTION audit_chain_append() RETURNS trigger AS $$
DECLARE
  secret_hex text;
  secret_bytes bytea;
  prev_h bytea;
  payload text;
  computed bytea;
BEGIN
  secret_hex := current_setting('audit.chain_secret', true);
  IF secret_hex IS NULL OR length(secret_hex) = 0 THEN
    RAISE EXCEPTION 'audit.chain_secret session GUC not set; cannot append to audit chain'
      USING ERRCODE = 'P0001';
  END IF;
  secret_bytes := decode(secret_hex, 'hex');

  -- Lock the anchor row.  Concurrent inserts on the same table block
  -- here so the chain has a single linear order.
  SELECT latest_row_hash INTO prev_h
    FROM audit_chain_anchors
   WHERE table_name = TG_TABLE_NAME
   FOR UPDATE;

  NEW.prev_hash := prev_h;

  -- Canonical payload: JSONB serialisation of NEW with the hash columns
  -- themselves excluded so the hash covers the application payload only.
  -- PostgreSQL stores JSONB with sorted keys, so the text cast is
  -- deterministic.
  payload := ((to_jsonb(NEW) - 'prev_hash') - 'row_hash')::text;

  computed := hmac(
    COALESCE(prev_h, '\x'::bytea) || convert_to(payload, 'UTF8'),
    secret_bytes,
    'sha256'
  );

  NEW.row_hash := computed;

  UPDATE audit_chain_anchors
     SET latest_row_id   = NEW.id,
         latest_row_hash = computed,
         row_count       = row_count + 1,
         updated_at      = now()
   WHERE table_name = TG_TABLE_NAME;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


_AUDIT_CHAIN_VERIFY_FN = r"""
CREATE OR REPLACE FUNCTION audit_chain_verify(t_name text)
RETURNS TABLE(
  row_id uuid,
  row_position bigint,
  ok boolean,
  expected_hash bytea,
  stored_hash bytea
) AS $$
DECLARE
  secret_hex text;
  secret_bytes bytea;
  order_col text;
  prev_h bytea := NULL;
  rec record;
  payload text;
  computed bytea;
  pos bigint := 0;
BEGIN
  secret_hex := current_setting('audit.chain_secret', true);
  IF secret_hex IS NULL OR length(secret_hex) = 0 THEN
    RAISE EXCEPTION 'audit.chain_secret session GUC not set';
  END IF;
  secret_bytes := decode(secret_hex, 'hex');

  SELECT order_column INTO order_col
    FROM audit_chain_anchors
   WHERE table_name = t_name;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'No audit chain registered for table %', t_name;
  END IF;

  FOR rec IN EXECUTE format(
    'SELECT * FROM %I ORDER BY %I ASC, id ASC',
    t_name, order_col
  ) LOOP
    pos := pos + 1;
    payload := ((to_jsonb(rec) - 'prev_hash') - 'row_hash')::text;
    computed := hmac(
      COALESCE(prev_h, '\x'::bytea) || convert_to(payload, 'UTF8'),
      secret_bytes,
      'sha256'
    );
    row_id := rec.id;
    row_position := pos;
    expected_hash := computed;
    stored_hash := rec.row_hash;
    ok := (computed = rec.row_hash)
      AND ((prev_h IS NULL AND rec.prev_hash IS NULL)
           OR (prev_h IS NOT DISTINCT FROM rec.prev_hash));
    RETURN NEXT;
    prev_h := computed;
  END LOOP;
END;
$$ LANGUAGE plpgsql;
"""


_AUDIT_CHAIN_BACKFILL_FN = r"""
CREATE OR REPLACE FUNCTION audit_chain_backfill(t_name text)
RETURNS bigint AS $$
DECLARE
  secret_hex text;
  secret_bytes bytea;
  order_col text;
  prev_h bytea := NULL;
  rec record;
  payload text;
  computed bytea;
  count bigint := 0;
  last_id uuid := NULL;
BEGIN
  secret_hex := current_setting('audit.chain_secret', true);
  IF secret_hex IS NULL OR length(secret_hex) = 0 THEN
    RAISE EXCEPTION 'audit.chain_secret session GUC not set';
  END IF;
  secret_bytes := decode(secret_hex, 'hex');

  SELECT order_column INTO order_col
    FROM audit_chain_anchors
   WHERE table_name = t_name;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'No audit chain registered for table %', t_name;
  END IF;

  FOR rec IN EXECUTE format(
    'SELECT * FROM %I ORDER BY %I ASC, id ASC',
    t_name, order_col
  ) LOOP
    payload := ((to_jsonb(rec) - 'prev_hash') - 'row_hash')::text;
    computed := hmac(
      COALESCE(prev_h, '\x'::bytea) || convert_to(payload, 'UTF8'),
      secret_bytes,
      'sha256'
    );
    EXECUTE format(
      'UPDATE %I SET prev_hash = $1, row_hash = $2 WHERE id = $3',
      t_name
    ) USING prev_h, computed, rec.id;
    prev_h := computed;
    last_id := rec.id;
    count := count + 1;
  END LOOP;

  UPDATE audit_chain_anchors
     SET latest_row_id   = last_id,
         latest_row_hash = prev_h,
         row_count       = count,
         updated_at      = now()
   WHERE table_name = t_name;

  RETURN count;
END;
$$ LANGUAGE plpgsql;
"""


_REVOKE_TENANT_APP_WRITE = r"""
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atlas_tenant_app') THEN
    REVOKE UPDATE, DELETE ON claim_history FROM atlas_tenant_app;
    REVOKE UPDATE, DELETE ON conflict_activity_log FROM atlas_tenant_app;
    REVOKE UPDATE, DELETE ON accident_projection_history FROM atlas_tenant_app;
    REVOKE UPDATE, DELETE ON public_event_page_revisions FROM atlas_tenant_app;
    REVOKE UPDATE, DELETE ON usage_events FROM atlas_tenant_app;
    REVOKE UPDATE, DELETE ON report_exports FROM atlas_tenant_app;
    -- The chain anchor row is read+updated by the trigger, so the app
    -- role needs SELECT/UPDATE.  No INSERT — anchors are seeded by the
    -- migration only.
    GRANT SELECT, UPDATE ON audit_chain_anchors TO atlas_tenant_app;
  END IF;
END $$;
"""
