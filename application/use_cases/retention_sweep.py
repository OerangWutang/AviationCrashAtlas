"""Retention sweeper — TICKET-013.

Soft-deletes rows whose ``retention_until`` deadline has passed while
honoring ``legal_hold`` and the existing ``deleted_at`` tombstone.  Each
swept row gets a ``DELETION_APPLIED`` row in ``compliance_events`` so the
audit chain (migration 053) records what disappeared and why.

Why soft-delete?
----------------

Hard ``DELETE`` would break the existing event-sourced projections that
join through ``accident_events`` / ``claims`` to render history.  Setting
``deleted_at`` lets the projections filter the row out while leaving the
ledger and any cross-references intact for forensic replay.  A future
ticket can purge ``deleted_at IS NOT NULL`` rows once the operator has
confirmed downstream systems no longer reference them.

Concurrency
-----------

Each batch uses ``FOR UPDATE SKIP LOCKED`` so two sweepers running in
parallel (e.g. cron + manual) cannot double-process the same row.  The
five tables are swept sequentially within a single transaction; on error
the whole batch rolls back, so partial state is impossible.

Auth-attempt pruning
--------------------

Migration 054 introduced ``api_key_attempts`` for brute-force tracking.
The lockout query only needs the last ``LOGIN_LOCKOUT_WINDOW_MINUTES``
of history, so rows older than ``max_age_minutes`` (default 24h) are
prunable.  The sweeper handles this alongside compliance retention so
operators run one cron entry, not two.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from atlas.application.unit_of_work import UnitOfWork
from atlas.application.use_cases.legal_hold import ENTITY_TYPE_TO_TABLE

logger = logging.getLogger(__name__)


# Mirror of ENTITY_TYPE_TO_TABLE flipped to (entity_type, table) pairs so
# the sweeper can emit the matching compliance ledger entry for each
# soft-delete.
_SWEEP_TABLES: Final[tuple[tuple[str, str], ...]] = tuple(sorted(ENTITY_TYPE_TO_TABLE.items()))


# 24h default keeps ~96 lockout windows of forensic history (the default
# window is 15 minutes) while still bounding the table on a long-lived instance.
DEFAULT_AUTH_ATTEMPT_MAX_AGE_MINUTES: Final[int] = 60 * 24


@dataclass
class RetentionSweepResult:
    """How much each table contributed to the sweep.

    The CLI surface and integration tests both look at this; keeping it
    a dataclass rather than a dict lets type-checkers catch typos.
    """

    swept_per_table: dict[str, int] = field(default_factory=dict)
    pruned_auth_attempts: int = 0

    @property
    def total_swept(self) -> int:
        return sum(self.swept_per_table.values())


def _session_of(uow: UnitOfWork) -> AsyncSession:
    return cast(AsyncSession, uow.session)  # type: ignore[attr-defined]


async def _record_deletion_event(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_id: uuid.UUID,
    reason: str,
) -> None:
    """Append a DELETION_APPLIED row.

    Sweeper deletions are ``actor_type = 'SYSTEM'`` so ``actor_id`` may
    be NULL — the CHECK constraint in migration 052 enforces this.
    """
    await session.execute(
        text(
            """
            INSERT INTO compliance_events (
                id, entity_type, entity_id, action, reason,
                actor_type, actor_id, payload_json, created_at
            ) VALUES (
                :id, :entity_type, :entity_id, 'DELETION_APPLIED', :reason,
                'SYSTEM', NULL, CAST(:payload AS jsonb), :now
            )
            """
        ),
        {
            "id": uuid.uuid4(),
            "entity_type": entity_type,
            "entity_id": entity_id,
            "reason": reason,
            "payload": json.dumps({"sweeper": "retention_sweep"}),
            "now": datetime.now(UTC),
        },
    )


class RetentionSweep:
    """Sweep all five compliance tables for due-and-unheld rows.

    Idempotent across runs: a row that was already soft-deleted no longer
    matches ``deleted_at IS NULL`` and is skipped.  Re-applying a legal
    hold after the sweep does not resurrect the row — the ledger entry
    is the authoritative record.
    """

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def execute(
        self,
        *,
        batch_per_table: int = 100,
        reason: str = "automatic retention sweep",
    ) -> RetentionSweepResult:
        if batch_per_table <= 0:
            raise ValueError("batch_per_table must be positive")
        session = _session_of(self._uow)
        result = RetentionSweepResult()

        for entity_type, table in _SWEEP_TABLES:
            swept = await self._sweep_table(
                session,
                entity_type=entity_type,
                table=table,
                batch=batch_per_table,
                reason=reason,
            )
            result.swept_per_table[entity_type] = swept

        await self._uow.commit()
        logger.info(
            "retention_sweep.completed total=%d per_table=%s",
            result.total_swept,
            result.swept_per_table,
        )
        return result

    async def _sweep_table(
        self,
        session: AsyncSession,
        *,
        entity_type: str,
        table: str,
        batch: int,
        reason: str,
    ) -> int:
        # SKIP LOCKED guards against parallel sweepers; the partial index
        # ``ix_{table}_retention_until`` from migration 052 covers this
        # exact WHERE clause.
        rows = (
            await session.execute(
                text(
                    f"""
                    WITH candidates AS (
                        SELECT id FROM {table}
                         WHERE retention_until IS NOT NULL
                           AND retention_until <= now()
                           AND legal_hold = false
                           AND deleted_at IS NULL
                         ORDER BY retention_until
                         LIMIT :batch
                         FOR UPDATE SKIP LOCKED
                    )
                    UPDATE {table}
                       SET deleted_at = now()
                     WHERE id IN (SELECT id FROM candidates)
                    RETURNING id
                    """
                ),
                {"batch": batch},
            )
        ).all()
        ids = [row[0] for row in rows]
        for entity_id in ids:
            await _record_deletion_event(
                session,
                entity_type=entity_type,
                entity_id=entity_id,
                reason=reason,
            )
        if ids:
            logger.info(
                "retention_sweep.swept entity=%s count=%d",
                entity_type,
                len(ids),
            )
        return len(ids)


async def prune_auth_attempts(
    uow: UnitOfWork,
    *,
    max_age_minutes: int = DEFAULT_AUTH_ATTEMPT_MAX_AGE_MINUTES,
) -> int:
    """Drop ``api_key_attempts`` rows older than ``max_age_minutes``.

    The lockout query in ``dependencies._check_lockout`` only looks at
    the last ``LOGIN_LOCKOUT_WINDOW_MINUTES`` of history, so older rows
    serve only as forensic data; the migration 054 docstring explicitly
    pairs this prune with the retention sweep.
    """
    if max_age_minutes <= 0:
        raise ValueError("max_age_minutes must be positive")
    session = _session_of(uow)
    result = await session.execute(
        text(
            "DELETE FROM api_key_attempts WHERE attempted_at < now() - make_interval(mins => :age)"
        ),
        {"age": max_age_minutes},
    )
    await uow.commit()
    deleted = result.rowcount or 0
    if deleted:
        logger.info("retention_sweep.pruned_auth_attempts deleted=%d", deleted)
    return deleted


__all__ = [
    "DEFAULT_AUTH_ATTEMPT_MAX_AGE_MINUTES",
    "RetentionSweep",
    "RetentionSweepResult",
    "prune_auth_attempts",
]
