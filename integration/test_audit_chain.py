"""Integration tests for the tamper-evident audit hash chain (migration 053).

Marked ``integration``: requires ``TEST_DATABASE_URL`` and skips otherwise.
The tests exercise:

* the ``audit_chain_append`` trigger fires on protected INSERTs and
  populates ``prev_hash`` / ``row_hash`` from the per-table anchor;
* the chain links — row N's ``prev_hash`` equals row N-1's ``row_hash``;
* the SQL verifier ``audit_chain_verify`` returns ``ok=true`` for an
  untouched chain and ``ok=false`` after an UPDATE on a hash column;
* missing the ``audit.chain_secret`` session GUC fails the INSERT loudly
  (the trigger raises P0001 rather than silently writing a NULL hash);
* the Python wrapper :mod:`atlas.application.use_cases.audit_chain_verify`
  surfaces the first bad row and a row count per table.

The REVOKE-on-``atlas_tenant_app`` clause is not exercised here because
the test database connects as a superuser (or owner) role; it would be
a no-op against any role with bypass privileges.  That clause is
covered by ops verification on the production cutover, not by tests.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytest_asyncio = pytest.importorskip("pytest_asyncio")
sqlalchemy = pytest.importorskip("sqlalchemy")

from sqlalchemy import text  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from atlas.application.use_cases.audit_chain_verify import (  # noqa: E402
    CHAINED_TABLES,
    verify_all,
    verify_table,
)
from atlas.infrastructure.db.unit_of_work import (  # noqa: E402
    SqlAlchemyUnitOfWork,
    set_audit_chain_secret,
)

pytestmark = pytest.mark.integration

_DSN = os.environ.get("TEST_DATABASE_URL")
_SECRET_HEX = "00" * 32  # matches conftest.TEST_AUDIT_CHAIN_SECRET_HEX


def _async_dsn(dsn: str) -> str:
    if dsn.startswith("postgresql+asyncpg://"):
        return dsn
    if dsn.startswith("postgresql://"):
        return dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    if dsn.startswith("postgres://"):
        return dsn.replace("postgres://", "postgresql+asyncpg://", 1)
    return dsn


@pytest_asyncio.fixture
async def session_maker():
    if not _DSN:
        pytest.skip("TEST_DATABASE_URL not set")
    engine = create_async_engine(_async_dsn(_DSN))
    try:
        yield async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    finally:
        await engine.dispose()


async def _reset_anchors_and_table(session: AsyncSession, table: str) -> None:
    """Make the chain on ``table`` start from empty for a single test.

    Truncate the table itself plus reset its anchor row.  Other protected
    tables are left alone so an earlier test's chain state cannot bleed
    into the assertions here.
    """
    await session.execute(text(f"DELETE FROM {table}"))
    await session.execute(
        text(
            "UPDATE audit_chain_anchors "
            "SET latest_row_id = NULL, latest_row_hash = NULL, row_count = 0 "
            "WHERE table_name = :t"
        ),
        {"t": table},
    )
    await session.commit()


async def _insert_usage_event(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
) -> uuid.UUID:
    """Insert a minimal ``usage_events`` row.

    ``usage_events`` is the smallest chained table (low FK fanout) so the
    test does not pull in tenant/source setup just to exercise the chain.
    """
    eid = uuid.uuid4()
    await session.execute(
        text(
            """
            INSERT INTO usage_events (
                id, tenant_id, user_id, event_type, recorded_at
            ) VALUES (
                :id, :tenant_id, :user_id, 'TEST_EVENT', now()
            )
            """
        ),
        {"id": eid, "tenant_id": tenant_id, "user_id": user_id},
    )
    return eid


async def test_trigger_populates_hashes_on_insert(session_maker) -> None:
    """First INSERT seeds the chain: prev_hash NULL, row_hash computed."""
    async with session_maker() as session:
        await _reset_anchors_and_table(session, "usage_events")
        await set_audit_chain_secret(session, _SECRET_HEX)

        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        eid = await _insert_usage_event(session, tenant_id=tenant_id, user_id=user_id)
        await session.commit()

        row = (
            await session.execute(
                text("SELECT prev_hash, row_hash FROM usage_events WHERE id = :id"),
                {"id": eid},
            )
        ).first()
        assert row is not None
        assert row.prev_hash is None  # head of chain
        assert row.row_hash is not None and len(row.row_hash) == 32  # SHA-256

        anchor = (
            await session.execute(
                text(
                    "SELECT latest_row_id, latest_row_hash, row_count "
                    "FROM audit_chain_anchors WHERE table_name = 'usage_events'"
                )
            )
        ).first()
        assert anchor is not None
        assert anchor.latest_row_id == eid
        assert bytes(anchor.latest_row_hash) == bytes(row.row_hash)
        assert anchor.row_count == 1


async def test_chain_links_consecutive_inserts(session_maker) -> None:
    """Row N's ``prev_hash`` equals row N-1's ``row_hash``."""
    async with session_maker() as session:
        await _reset_anchors_and_table(session, "usage_events")
        await set_audit_chain_secret(session, _SECRET_HEX)

        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        first_id = await _insert_usage_event(session, tenant_id=tenant_id, user_id=user_id)
        second_id = await _insert_usage_event(session, tenant_id=tenant_id, user_id=user_id)
        await session.commit()

        first = (
            await session.execute(
                text("SELECT row_hash FROM usage_events WHERE id = :id"),
                {"id": first_id},
            )
        ).one()
        second = (
            await session.execute(
                text("SELECT prev_hash, row_hash FROM usage_events WHERE id = :id"),
                {"id": second_id},
            )
        ).one()
        assert bytes(second.prev_hash) == bytes(first.row_hash)
        assert bytes(second.row_hash) != bytes(first.row_hash)


async def test_verifier_reports_ok_for_untouched_chain(session_maker) -> None:
    async with session_maker() as session:
        await _reset_anchors_and_table(session, "usage_events")
        await set_audit_chain_secret(session, _SECRET_HEX)
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        for _ in range(3):
            await _insert_usage_event(session, tenant_id=tenant_id, user_id=user_id)
        await session.commit()
        # GUC was cleared by COMMIT; the verifier needs it back.
        await set_audit_chain_secret(session, _SECRET_HEX)

        uow = SqlAlchemyUnitOfWork(session, audit_chain_secret_hex=_SECRET_HEX)
        result = await verify_table(uow, "usage_events")
        assert result.ok is True
        assert result.row_count == 3
        assert result.first_bad_row is None


async def test_verifier_detects_tampered_row(session_maker) -> None:
    """UPDATE on a chain row's ``row_hash`` makes the verifier flag that row.

    Production deployments REVOKE UPDATE/DELETE from the tenant role so
    this UPDATE only succeeds as the migration owner or in tests; the
    point of the verifier is to detect tamper that bypasses that role
    boundary (DBA mistakes, restore-from-old-snapshot, malicious
    superuser).
    """
    async with session_maker() as session:
        await _reset_anchors_and_table(session, "usage_events")
        await set_audit_chain_secret(session, _SECRET_HEX)
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        ids = [
            await _insert_usage_event(session, tenant_id=tenant_id, user_id=user_id)
            for _ in range(3)
        ]
        await session.commit()

        # Corrupt the middle row's stored hash.  Verifier should mark
        # row 2 (position 2) bad while reporting row_count == 3.
        await session.execute(
            text(
                r"UPDATE usage_events SET row_hash = '\xdeadbeef'::bytea "
                r"WHERE id = :id"
            ),
            {"id": ids[1]},
        )
        await session.commit()

        await set_audit_chain_secret(session, _SECRET_HEX)
        uow = SqlAlchemyUnitOfWork(session, audit_chain_secret_hex=_SECRET_HEX)
        result = await verify_table(uow, "usage_events")
        assert result.ok is False
        assert result.row_count == 3
        assert result.first_bad_row is not None
        assert result.first_bad_row.row_id == ids[1]
        assert result.first_bad_row.row_position == 2


async def test_insert_without_secret_guc_raises(session_maker) -> None:
    """Without ``audit.chain_secret`` set, the trigger refuses the INSERT."""
    async with session_maker() as session:
        await _reset_anchors_and_table(session, "usage_events")
        # Intentionally do NOT call set_audit_chain_secret here.
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        with pytest.raises(DBAPIError) as exc_info:
            await _insert_usage_event(session, tenant_id=tenant_id, user_id=user_id)
            await session.commit()
        # The trigger raises P0001 with a clear message; surface it.
        assert "audit.chain_secret" in str(exc_info.value)
        await session.rollback()


async def test_verify_all_covers_every_protected_table(session_maker) -> None:
    """``verify_all`` returns one result per table in ``CHAINED_TABLES``."""
    async with session_maker() as session:
        await set_audit_chain_secret(session, _SECRET_HEX)
        uow = SqlAlchemyUnitOfWork(session, audit_chain_secret_hex=_SECRET_HEX)
        results = await verify_all(uow)
        assert [r.table_name for r in results] == list(CHAINED_TABLES)
        # An empty chain (no rows) verifies OK trivially.
        for r in results:
            assert r.ok is True


async def test_verifier_rejects_unknown_table(session_maker) -> None:
    async with session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        with pytest.raises(ValueError, match="is not chain-protected"):
            await verify_table(uow, "sources")  # real table, not chained
