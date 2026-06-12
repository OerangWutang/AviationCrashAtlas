"""Integration test: legal hold + retention + compliance ledger.

Marked ``integration``: needs ``TEST_DATABASE_URL`` and skips otherwise.
The test exercises migration 052's schema additions and the
``ApplyLegalHold`` / ``ReleaseLegalHold`` / ``SetRetention`` use cases
against a real PostgreSQL session.  It asserts the guarantees that
matter for legal defensibility:

  * applying a hold flips ``legal_hold`` true and records a
    ``LEGAL_HOLD_APPLIED`` compliance event with actor + reason;
  * releasing a hold flips it false, clears the metadata fields,
    and records a second compliance event — the first row is not
    overwritten;
  * setting ``retention_until`` writes the timestamp and records a
    ``RETENTION_SET`` event whose ``payload_json`` carries the value;
  * the retention sweeper (TICKET-013) consumes the partial index
    ``ix_accident_events_retention_until`` — held rows are filtered
    out by ``WHERE legal_hold = false``.
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

from atlas.application.use_cases.legal_hold import (  # noqa: E402
    ApplyLegalHold,
    ApplyRedaction,
    InvalidEntityTypeError,
    ReleaseLegalHold,
    SetRetention,
)
from atlas.domain.exceptions import NotFoundError  # noqa: E402
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
async def event_id(session_maker) -> uuid.UUID:
    """Create a throwaway accident_events row and clean up after."""
    eid = uuid.uuid4()
    async with session_maker() as session:
        await session.execute(
            text("INSERT INTO accident_events (id) VALUES (:id)"),
            {"id": eid},
        )
        await session.commit()
    try:
        yield eid
    finally:
        async with session_maker() as session:
            await session.execute(
                text(
                    "DELETE FROM compliance_events "
                    "WHERE entity_type = 'accident_event' AND entity_id = :id"
                ),
                {"id": eid},
            )
            await session.execute(
                text("DELETE FROM accident_events WHERE id = :id"),
                {"id": eid},
            )
            await session.commit()


async def _read_event(session: AsyncSession, eid: uuid.UUID) -> dict:
    row = (
        await session.execute(
            text(
                "SELECT legal_hold, legal_hold_reason, legal_hold_set_at, "
                "legal_hold_set_by, retention_until "
                "FROM accident_events WHERE id = :id"
            ),
            {"id": eid},
        )
    ).first()
    assert row is not None
    return {
        "legal_hold": row[0],
        "legal_hold_reason": row[1],
        "legal_hold_set_at": row[2],
        "legal_hold_set_by": row[3],
        "retention_until": row[4],
    }


async def _ledger(
    session: AsyncSession, eid: uuid.UUID
) -> list[tuple[str, str, uuid.UUID | None, dict | str]]:
    result = await session.execute(
        text(
            "SELECT action, reason, actor_id, payload_json "
            "FROM compliance_events "
            "WHERE entity_type = 'accident_event' AND entity_id = :id "
            "ORDER BY created_at, id"
        ),
        {"id": eid},
    )
    return [(r[0], r[1], r[2], r[3]) for r in result.all()]


async def test_apply_legal_hold_flips_column_and_writes_ledger(session_maker, event_id) -> None:
    actor = uuid.uuid4()
    async with session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        await ApplyLegalHold(uow).execute(
            entity_type="accident_event",
            entity_id=event_id,
            reason="Subpoena 2026-CV-0042 — preserve all evidence",
            actor_id=actor,
        )
    async with session_maker() as session:
        state = await _read_event(session, event_id)
        assert state["legal_hold"] is True
        assert state["legal_hold_reason"] == "Subpoena 2026-CV-0042 — preserve all evidence"
        assert state["legal_hold_set_at"] is not None
        assert state["legal_hold_set_by"] == actor

        ledger = await _ledger(session, event_id)
        assert len(ledger) == 1
        action, reason, actor_id_logged, _ = ledger[0]
        assert action == "LEGAL_HOLD_APPLIED"
        assert reason.startswith("Subpoena 2026-CV-0042")
        assert actor_id_logged == actor


async def test_release_legal_hold_clears_metadata_and_appends_ledger(
    session_maker, event_id
) -> None:
    actor = uuid.uuid4()
    async with session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        await ApplyLegalHold(uow).execute(
            entity_type="accident_event",
            entity_id=event_id,
            reason="initial hold",
            actor_id=actor,
        )
    async with session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        await ReleaseLegalHold(uow).execute(
            entity_type="accident_event",
            entity_id=event_id,
            reason="matter closed",
            actor_id=actor,
        )
    async with session_maker() as session:
        state = await _read_event(session, event_id)
        assert state["legal_hold"] is False
        assert state["legal_hold_reason"] is None
        assert state["legal_hold_set_at"] is None
        assert state["legal_hold_set_by"] is None

        ledger = await _ledger(session, event_id)
        assert [row[0] for row in ledger] == [
            "LEGAL_HOLD_APPLIED",
            "LEGAL_HOLD_RELEASED",
        ]
        assert ledger[1][1] == "matter closed"


async def test_set_retention_writes_column_and_payload(session_maker, event_id) -> None:
    actor = uuid.uuid4()
    deadline = datetime.now(UTC) + timedelta(days=365)
    async with session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        await SetRetention(uow).execute(
            entity_type="accident_event",
            entity_id=event_id,
            retention_until=deadline,
            reason="default 1-year retention",
            actor_id=actor,
        )
    async with session_maker() as session:
        state = await _read_event(session, event_id)
        assert state["retention_until"] is not None
        # The DB stores at microsecond resolution; compare via isoformat
        # round-trip rather than direct equality.
        assert abs((state["retention_until"] - deadline).total_seconds()) < 1.0

        ledger = await _ledger(session, event_id)
        assert ledger[-1][0] == "RETENTION_SET"
        payload = ledger[-1][3]
        # JSONB comes back as dict from psycopg/asyncpg
        assert isinstance(payload, dict)
        assert payload.get("retention_until") is not None


async def test_retention_sweeper_excludes_held_rows(session_maker, event_id) -> None:
    """Held rows must be invisible to the sweeper's candidate query.

    The retention sweeper (TICKET-013) will scan
    ``WHERE retention_until <= now() AND legal_hold = false
       AND deleted_at IS NULL``.  This test asserts the partial index
    + filter contract: a held row with a past retention deadline is
    never returned.
    """
    actor = uuid.uuid4()
    deadline = datetime.now(UTC) + timedelta(seconds=2)
    async with session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        await SetRetention(uow).execute(
            entity_type="accident_event",
            entity_id=event_id,
            retention_until=deadline,
            reason="short test deadline",
            actor_id=actor,
        )
        uow2 = SqlAlchemyUnitOfWork(session)
        await ApplyLegalHold(uow2).execute(
            entity_type="accident_event",
            entity_id=event_id,
            reason="hold blocks deletion",
            actor_id=actor,
        )

    # Move past the deadline.
    import asyncio

    await asyncio.sleep(3)

    async with session_maker() as session:
        candidates = (
            await session.execute(
                text(
                    "SELECT id FROM accident_events "
                    "WHERE retention_until <= now() "
                    "  AND legal_hold = false "
                    "  AND deleted_at IS NULL"
                )
            )
        ).all()
        assert event_id not in {row[0] for row in candidates}


async def test_invalid_entity_type_rejected(session_maker) -> None:
    async with session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        with pytest.raises(InvalidEntityTypeError):
            await ApplyLegalHold(uow).execute(
                entity_type="nonexistent",
                entity_id=uuid.uuid4(),
                reason="x",
                actor_id=uuid.uuid4(),
            )


async def test_missing_entity_raises_not_found(session_maker) -> None:
    async with session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        with pytest.raises(NotFoundError):
            await ApplyLegalHold(uow).execute(
                entity_type="accident_event",
                entity_id=uuid.uuid4(),
                reason="x",
                actor_id=uuid.uuid4(),
            )


async def test_apply_redaction_sets_columns_and_writes_ledger(session_maker, event_id) -> None:
    actor = uuid.uuid4()
    async with session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        await ApplyRedaction(uow).execute(
            entity_type="accident_event",
            entity_id=event_id,
            reason="GDPR Art.17 erasure request — subject 4421",
            actor_id=actor,
        )
    async with session_maker() as session:
        row = (
            await session.execute(
                text(
                    "SELECT redacted_at, redacted_by, redaction_reason "
                    "FROM accident_events WHERE id = :id"
                ),
                {"id": event_id},
            )
        ).first()
        assert row is not None
        first_redacted_at, redacted_by, redaction_reason = row
        assert first_redacted_at is not None
        assert redacted_by == actor
        assert redaction_reason.startswith("GDPR")

        ledger = await _ledger(session, event_id)
        assert [r[0] for r in ledger] == ["REDACTION_APPLIED"]


async def test_reapplying_redaction_preserves_original_timestamp(
    session_maker, event_id
) -> None:
    """Idempotency: re-application appends a ledger row but keeps the first ``redacted_at``."""
    actor1 = uuid.uuid4()
    actor2 = uuid.uuid4()
    async with session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        await ApplyRedaction(uow).execute(
            entity_type="accident_event",
            entity_id=event_id,
            reason="first request",
            actor_id=actor1,
        )
    async with session_maker() as session:
        first_redacted_at = (
            await session.execute(
                text("SELECT redacted_at FROM accident_events WHERE id = :id"),
                {"id": event_id},
            )
        ).scalar_one()

    # Different actor + reason re-applies; the row's redacted_at should be unchanged
    # but redacted_by/redaction_reason should reflect the new caller, and a second
    # ledger row should be present.
    import asyncio

    await asyncio.sleep(0.05)  # ensure clock moved
    async with session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        await ApplyRedaction(uow).execute(
            entity_type="accident_event",
            entity_id=event_id,
            reason="second request",
            actor_id=actor2,
        )
    async with session_maker() as session:
        row = (
            await session.execute(
                text(
                    "SELECT redacted_at, redacted_by, redaction_reason "
                    "FROM accident_events WHERE id = :id"
                ),
                {"id": event_id},
            )
        ).first()
        assert row is not None
        redacted_at, redacted_by, redaction_reason = row
        assert redacted_at == first_redacted_at
        assert redacted_by == actor2
        assert redaction_reason == "second request"

        actions = [r[0] for r in await _ledger(session, event_id)]
        assert actions == ["REDACTION_APPLIED", "REDACTION_APPLIED"]


async def test_redaction_rejects_empty_reason(session_maker, event_id) -> None:
    async with session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        with pytest.raises(ValueError):
            await ApplyRedaction(uow).execute(
                entity_type="accident_event",
                entity_id=event_id,
                reason="   ",
                actor_id=uuid.uuid4(),
            )


async def test_redaction_rejects_missing_entity(session_maker) -> None:
    async with session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        with pytest.raises(NotFoundError):
            await ApplyRedaction(uow).execute(
                entity_type="accident_event",
                entity_id=uuid.uuid4(),
                reason="x",
                actor_id=uuid.uuid4(),
            )


async def test_empty_reason_rejected(session_maker, event_id) -> None:
    async with session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        with pytest.raises(ValueError):
            await ApplyLegalHold(uow).execute(
                entity_type="accident_event",
                entity_id=event_id,
                reason="   ",
                actor_id=uuid.uuid4(),
            )
