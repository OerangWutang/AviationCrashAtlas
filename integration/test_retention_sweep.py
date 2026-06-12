"""Integration test: retention sweeper + auth-attempt prune.

Marked ``integration``: needs ``TEST_DATABASE_URL`` and skips otherwise.
Exercises ``RetentionSweep`` and ``prune_auth_attempts`` against a real
PostgreSQL session.  Verifies:

  * rows past their ``retention_until`` and not on hold are soft-deleted
    and a ``DELETION_APPLIED`` row appears in ``compliance_events``;
  * rows under legal hold are skipped, including when their deadline has
    long since passed;
  * rows without a ``retention_until`` deadline are untouched;
  * already-soft-deleted rows are not re-swept (the sweeper is
    idempotent across runs);
  * ``prune_auth_attempts`` drops only rows older than the supplied
    ``max_age_minutes`` and leaves recent rows intact.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytest_asyncio = pytest.importorskip("pytest_asyncio")
sqlalchemy = pytest.importorskip("sqlalchemy")

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from atlas.application.use_cases.retention_sweep import (  # noqa: E402
    RetentionSweep,
    prune_auth_attempts,
)
from atlas.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork  # noqa: E402

pytestmark = pytest.mark.integration

_DSN = os.environ.get("TEST_DATABASE_URL")


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


@pytest_asyncio.fixture
async def event_rows(session_maker) -> list[uuid.UUID]:
    """Three accident_events: due+unheld, due+held, no-deadline.

    Each test gets its own UUIDs and cleans them up in the finally block
    so a previous test's leftovers can't bleed in.
    """
    due_unheld = uuid.uuid4()
    due_held = uuid.uuid4()
    no_deadline = uuid.uuid4()
    past = datetime.now(UTC) - timedelta(days=1)
    actor = uuid.uuid4()
    async with session_maker() as session:
        await session.execute(
            text("INSERT INTO accident_events (id, retention_until) VALUES (:id, :ru)"),
            {"id": due_unheld, "ru": past},
        )
        await session.execute(
            text(
                "INSERT INTO accident_events (id, retention_until, legal_hold, "
                "legal_hold_reason, legal_hold_set_at, legal_hold_set_by) "
                "VALUES (:id, :ru, true, 'subpoena', now(), :actor)"
            ),
            {"id": due_held, "ru": past, "actor": actor},
        )
        await session.execute(
            text("INSERT INTO accident_events (id) VALUES (:id)"),
            {"id": no_deadline},
        )
        await session.commit()
    try:
        yield [due_unheld, due_held, no_deadline]
    finally:
        async with session_maker() as session:
            await session.execute(
                text(
                    "DELETE FROM compliance_events "
                    "WHERE entity_type = 'accident_event' AND entity_id = ANY(:ids)"
                ),
                {"ids": [due_unheld, due_held, no_deadline]},
            )
            await session.execute(
                text("DELETE FROM accident_events WHERE id = ANY(:ids)"),
                {"ids": [due_unheld, due_held, no_deadline]},
            )
            await session.commit()


async def _deleted_at(session: AsyncSession, eid: uuid.UUID) -> datetime | None:
    row = (
        await session.execute(
            text("SELECT deleted_at FROM accident_events WHERE id = :id"),
            {"id": eid},
        )
    ).first()
    assert row is not None
    return row[0]


async def _ledger_actions(session: AsyncSession, eid: uuid.UUID) -> list[str]:
    rows = (
        await session.execute(
            text(
                "SELECT action FROM compliance_events "
                "WHERE entity_type = 'accident_event' AND entity_id = :id "
                "ORDER BY created_at, id"
            ),
            {"id": eid},
        )
    ).all()
    return [r[0] for r in rows]


async def test_sweep_soft_deletes_due_unheld_rows(session_maker, event_rows) -> None:
    due_unheld, due_held, no_deadline = event_rows
    async with session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        result = await RetentionSweep(uow).execute(reason="test sweep")
    assert result.swept_per_table.get("accident_event", 0) == 1

    async with session_maker() as session:
        assert await _deleted_at(session, due_unheld) is not None
        assert await _deleted_at(session, due_held) is None
        assert await _deleted_at(session, no_deadline) is None

        assert await _ledger_actions(session, due_unheld) == ["DELETION_APPLIED"]
        assert await _ledger_actions(session, due_held) == []
        assert await _ledger_actions(session, no_deadline) == []


async def test_sweep_is_idempotent(session_maker, event_rows) -> None:
    """Re-running the sweep must not re-delete or double-log."""
    async with session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        first = await RetentionSweep(uow).execute(reason="test sweep")
    async with session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        second = await RetentionSweep(uow).execute(reason="test sweep")
    assert first.swept_per_table.get("accident_event", 0) == 1
    assert second.swept_per_table.get("accident_event", 0) == 0

    due_unheld = event_rows[0]
    async with session_maker() as session:
        actions = await _ledger_actions(session, due_unheld)
        assert actions == ["DELETION_APPLIED"]


async def test_sweep_rejects_zero_batch(session_maker) -> None:
    async with session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        with pytest.raises(ValueError):
            await RetentionSweep(uow).execute(batch_per_table=0)


async def test_prune_auth_attempts_drops_only_old_rows(session_maker) -> None:
    """A 5-min-old failure stays; a 2-hour-old failure goes when the cutoff is 60min."""
    fresh_id = uuid.uuid4()
    old_id = uuid.uuid4()
    async with session_maker() as session:
        await session.execute(
            text(
                "INSERT INTO api_key_attempts (id, client_ip, attempted_at, succeeded) "
                "VALUES (:id, '10.0.0.1'::inet, now() - interval '5 minutes', false)"
            ),
            {"id": fresh_id},
        )
        await session.execute(
            text(
                "INSERT INTO api_key_attempts (id, client_ip, attempted_at, succeeded) "
                "VALUES (:id, '10.0.0.1'::inet, now() - interval '2 hours', false)"
            ),
            {"id": old_id},
        )
        await session.commit()

    try:
        async with session_maker() as session:
            uow = SqlAlchemyUnitOfWork(session)
            deleted = await prune_auth_attempts(uow, max_age_minutes=60)
        assert deleted == 1

        async with session_maker() as session:
            survivors = (
                await session.execute(
                    text("SELECT id FROM api_key_attempts WHERE id = ANY(:ids)"),
                    {"ids": [fresh_id, old_id]},
                )
            ).all()
            ids = {r[0] for r in survivors}
            assert fresh_id in ids
            assert old_id not in ids
    finally:
        async with session_maker() as session:
            await session.execute(
                text("DELETE FROM api_key_attempts WHERE id = ANY(:ids)"),
                {"ids": [fresh_id, old_id]},
            )
            await session.commit()


async def test_prune_auth_attempts_rejects_zero_age(session_maker) -> None:
    async with session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        with pytest.raises(ValueError):
            await prune_auth_attempts(uow, max_age_minutes=0)
