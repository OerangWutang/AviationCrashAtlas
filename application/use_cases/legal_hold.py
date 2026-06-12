"""Legal hold, retention, and redaction control.

Implements the admin-side mutations that turn the schema-level
columns from migration 052 into actionable controls:

- :class:`ApplyLegalHold` flags an entity as under legal hold so the
  retention sweeper (TICKET-013) refuses to delete it, regardless of
  how stale ``retention_until`` becomes.
- :class:`ReleaseLegalHold` clears the hold.  The column write itself
  is reversible; the ledger row is not.
- :class:`SetRetention` writes ``retention_until``.  Without a value
  the sweeper leaves the row alone.

Every action writes a row to ``compliance_events`` so the audit
trail is independent of the entity row.  That table is intended to
be append-only; TICKET-004 will revoke UPDATE/DELETE on it from the
tenant role and add a hash chain.  Until then the invariant is
by-convention.

The entity-type column is a string discriminator rather than a
polymorphic FK so the ledger can record events about rows in tables
the application boundary normally treats as separate domains
(payload, metering, uploads).  The CHECK constraint in migration
052 keeps the discriminator honest.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Final, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from atlas.application.unit_of_work import UnitOfWork
from atlas.domain.exceptions import NotFoundError

logger = logging.getLogger(__name__)


def _session_of(uow: UnitOfWork) -> AsyncSession:
    """Reach into the SQLAlchemy backend for raw ``text()`` SQL.

    Legal hold spans five entity tables and writes to ``compliance_events``;
    adding five repository methods for a single use case would inflate the
    boundary surface for no isolation gain.  The cast is contained here so
    the abstract :class:`UnitOfWork` stays free of a SQLAlchemy dependency.
    """
    return cast(AsyncSession, uow.session)  # type: ignore[attr-defined]


# Tables whose rows can be the target of compliance actions.  Mirrors
# ``ck_compliance_events_entity_type`` in migration 052.
ENTITY_TYPE_TO_TABLE: Final[dict[str, str]] = {
    "accident_event": "accident_events",
    "claim": "claims",
    "tenant_claim": "tenant_claims",
    "uploaded_document": "uploaded_documents",
    "usage_event": "usage_events",
}


class InvalidEntityTypeError(ValueError):
    """Caller supplied an entity_type the schema does not allow."""


def _table_for(entity_type: str) -> str:
    try:
        return ENTITY_TYPE_TO_TABLE[entity_type]
    except KeyError as exc:
        raise InvalidEntityTypeError(
            f"entity_type {entity_type!r} is not one of {sorted(ENTITY_TYPE_TO_TABLE)}"
        ) from exc


async def _row_exists(session: AsyncSession, table: str, entity_id: uuid.UUID) -> bool:
    result = await session.execute(
        text(f"SELECT 1 FROM {table} WHERE id = :id"),
        {"id": entity_id},
    )
    return result.first() is not None


async def _record_compliance_event(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_id: uuid.UUID,
    action: str,
    reason: str,
    actor_id: uuid.UUID | None,
    actor_type: str,
    payload: dict[str, Any] | None = None,
) -> uuid.UUID:
    event_id = uuid.uuid4()
    await session.execute(
        text(
            """
            INSERT INTO compliance_events (
                id, entity_type, entity_id, action, reason,
                actor_type, actor_id, payload_json, created_at
            ) VALUES (
                :id, :entity_type, :entity_id, :action, :reason,
                :actor_type, :actor_id, CAST(:payload AS jsonb), :now
            )
            """
        ),
        {
            "id": event_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "action": action,
            "reason": reason,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "payload": json.dumps(payload or {}),
            "now": datetime.now(UTC),
        },
    )
    return event_id


class ApplyLegalHold:
    """Set ``legal_hold = true`` on an entity and log the action.

    Idempotent: re-applying refreshes the reason/actor metadata and
    writes a new ledger row, so the history retains every assertion.
    """

    def __init__(self, uow: UnitOfWork):
        self._uow = uow

    async def execute(
        self,
        *,
        entity_type: str,
        entity_id: uuid.UUID,
        reason: str,
        actor_id: uuid.UUID,
    ) -> uuid.UUID:
        if not reason.strip():
            raise ValueError("reason must not be empty")
        table = _table_for(entity_type)
        session = _session_of(self._uow)
        if not await _row_exists(session, table, entity_id):
            raise NotFoundError(f"{entity_type} {entity_id} not found")
        now = datetime.now(UTC)
        await session.execute(
            text(
                f"""
                UPDATE {table}
                   SET legal_hold = true,
                       legal_hold_reason = :reason,
                       legal_hold_set_at = :now,
                       legal_hold_set_by = :actor
                 WHERE id = :id
                """
            ),
            {"reason": reason, "now": now, "actor": actor_id, "id": entity_id},
        )
        event_id = await _record_compliance_event(
            session,
            entity_type=entity_type,
            entity_id=entity_id,
            action="LEGAL_HOLD_APPLIED",
            reason=reason,
            actor_id=actor_id,
            actor_type="USER",
        )
        await self._uow.commit()
        logger.info(
            "legal_hold.applied entity=%s id=%s actor=%s",
            entity_type,
            entity_id,
            actor_id,
        )
        return event_id


class ReleaseLegalHold:
    """Clear ``legal_hold`` on an entity and log the action.

    The release also clears the metadata fields so a subsequent hold
    starts from a clean slate.  The ledger retains the full history.
    """

    def __init__(self, uow: UnitOfWork):
        self._uow = uow

    async def execute(
        self,
        *,
        entity_type: str,
        entity_id: uuid.UUID,
        reason: str,
        actor_id: uuid.UUID,
    ) -> uuid.UUID:
        if not reason.strip():
            raise ValueError("reason must not be empty")
        table = _table_for(entity_type)
        session = _session_of(self._uow)
        if not await _row_exists(session, table, entity_id):
            raise NotFoundError(f"{entity_type} {entity_id} not found")
        await session.execute(
            text(
                f"""
                UPDATE {table}
                   SET legal_hold = false,
                       legal_hold_reason = NULL,
                       legal_hold_set_at = NULL,
                       legal_hold_set_by = NULL
                 WHERE id = :id
                """
            ),
            {"id": entity_id},
        )
        event_id = await _record_compliance_event(
            session,
            entity_type=entity_type,
            entity_id=entity_id,
            action="LEGAL_HOLD_RELEASED",
            reason=reason,
            actor_id=actor_id,
            actor_type="USER",
        )
        await self._uow.commit()
        logger.info(
            "legal_hold.released entity=%s id=%s actor=%s",
            entity_type,
            entity_id,
            actor_id,
        )
        return event_id


class SetRetention:
    """Set ``retention_until`` on an entity and log the action.

    Passing ``retention_until=None`` clears the deadline so the row
    is retained indefinitely until an admin sets a new one.
    """

    def __init__(self, uow: UnitOfWork):
        self._uow = uow

    async def execute(
        self,
        *,
        entity_type: str,
        entity_id: uuid.UUID,
        retention_until: datetime | None,
        reason: str,
        actor_id: uuid.UUID,
    ) -> uuid.UUID:
        if not reason.strip():
            raise ValueError("reason must not be empty")
        if retention_until is not None and retention_until <= datetime.now(UTC):
            raise ValueError("retention_until must be in the future")
        table = _table_for(entity_type)
        session = _session_of(self._uow)
        if not await _row_exists(session, table, entity_id):
            raise NotFoundError(f"{entity_type} {entity_id} not found")
        await session.execute(
            text(f"UPDATE {table} SET retention_until = :ru WHERE id = :id"),
            {"ru": retention_until, "id": entity_id},
        )
        event_id = await _record_compliance_event(
            session,
            entity_type=entity_type,
            entity_id=entity_id,
            action="RETENTION_SET",
            reason=reason,
            actor_id=actor_id,
            actor_type="USER",
            payload={
                "retention_until": (retention_until.isoformat() if retention_until else None),
            },
        )
        await self._uow.commit()
        logger.info(
            "retention.set entity=%s id=%s until=%s actor=%s",
            entity_type,
            entity_id,
            retention_until,
            actor_id,
        )
        return event_id


class ApplyRedaction:
    """Mark an entity as redacted and log the action.

    Writes ``redacted_at``/``redacted_by``/``redaction_reason`` on the
    target row and a ``REDACTION_APPLIED`` row to ``compliance_events``.
    The columns are status flags only — actual content blanking is the
    responsibility of downstream consumers (projections, exports) which
    must treat ``redacted_at IS NOT NULL`` as "do not surface".

    Idempotent re-application:
        Re-applying preserves the original ``redacted_at`` (so the
        canonical "when did the row first get redacted" timestamp is
        not overwritten) but refreshes ``redacted_by``/``redaction_reason``
        with the latest actor + reason, and appends a fresh ledger row.
        The ledger therefore retains every assertion while the row
        timestamp answers "since when".
    """

    def __init__(self, uow: UnitOfWork):
        self._uow = uow

    async def execute(
        self,
        *,
        entity_type: str,
        entity_id: uuid.UUID,
        reason: str,
        actor_id: uuid.UUID,
    ) -> uuid.UUID:
        if not reason.strip():
            raise ValueError("reason must not be empty")
        table = _table_for(entity_type)
        session = _session_of(self._uow)
        if not await _row_exists(session, table, entity_id):
            raise NotFoundError(f"{entity_type} {entity_id} not found")
        now = datetime.now(UTC)
        await session.execute(
            text(
                f"""
                UPDATE {table}
                   SET redacted_at = COALESCE(redacted_at, :now),
                       redacted_by = :actor,
                       redaction_reason = :reason
                 WHERE id = :id
                """
            ),
            {"now": now, "actor": actor_id, "reason": reason, "id": entity_id},
        )
        event_id = await _record_compliance_event(
            session,
            entity_type=entity_type,
            entity_id=entity_id,
            action="REDACTION_APPLIED",
            reason=reason,
            actor_id=actor_id,
            actor_type="USER",
        )
        await self._uow.commit()
        logger.info(
            "redaction.applied entity=%s id=%s actor=%s",
            entity_type,
            entity_id,
            actor_id,
        )
        return event_id


__all__ = [
    "ENTITY_TYPE_TO_TABLE",
    "ApplyLegalHold",
    "ApplyRedaction",
    "InvalidEntityTypeError",
    "ReleaseLegalHold",
    "SetRetention",
]
