"""Audit-chain verification (TICKET-004).

Calls the ``audit_chain_verify(text)`` SQL function created in migration
053 and surfaces the results as plain Python.  The trigger and verifier
share canonicalisation logic in PL/pgSQL on purpose: re-implementing the
hash in Python would invite drift the next time a column is added to a
protected table.  Here we just consume what Postgres reports.

The verifier returns a per-row record so a caller (CLI, scheduled job,
admin endpoint) can show *which* row in *which* table is the first to
break.  Aggregating to a single boolean would throw away the
information operators need to investigate.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from atlas.application.unit_of_work import UnitOfWork

#: Tables protected by the chain (mirrors ``_CHAINED_TABLES`` in migration 053).
#: Kept in code as well so callers can iterate without a DB round-trip.
CHAINED_TABLES: tuple[str, ...] = (
    "claim_history",
    "conflict_activity_log",
    "accident_projection_history",
    "public_event_page_revisions",
    "usage_events",
    "report_exports",
)


@dataclass(frozen=True)
class ChainRowResult:
    """One row's verification outcome as returned by ``audit_chain_verify``."""

    row_id: uuid.UUID
    row_position: int
    ok: bool
    expected_hash: bytes
    stored_hash: bytes


@dataclass(frozen=True)
class ChainTableResult:
    """Aggregate verification result for one protected table."""

    table_name: str
    row_count: int
    first_bad_row: ChainRowResult | None

    @property
    def ok(self) -> bool:
        return self.first_bad_row is None


def _session_of(uow: UnitOfWork) -> AsyncSession:
    """Reach the SQLAlchemy session without leaking it into the abstract UoW.

    The verifier runs raw SQL (``audit_chain_verify`` is a set-returning
    function); a repository wrapper would add ceremony for a single call.
    """
    return cast(AsyncSession, uow.session)  # type: ignore[attr-defined]


async def verify_table(uow: UnitOfWork, table_name: str) -> ChainTableResult:
    """Verify the chain for a single table.

    Streams the verifier results and stops materialising rows once the
    first failure is captured.  Postgres still has to walk the whole
    table — the chain is intrinsically sequential — but the Python side
    does not have to hold every row in memory.

    Raises a ``ValueError`` if ``table_name`` is not registered with the
    chain; we surface that here rather than letting Postgres raise so
    the caller gets a typed error.
    """
    if table_name not in CHAINED_TABLES:
        raise ValueError(
            f"table {table_name!r} is not chain-protected; valid tables are {CHAINED_TABLES!r}"
        )
    session = _session_of(uow)
    result = await session.execute(
        text(
            "SELECT row_id, row_position, ok, expected_hash, stored_hash "
            "FROM audit_chain_verify(:t)"
        ),
        {"t": table_name},
    )
    row_count = 0
    first_bad: ChainRowResult | None = None
    for row in result:
        row_count += 1
        if not row.ok and first_bad is None:
            first_bad = ChainRowResult(
                row_id=row.row_id,
                row_position=int(row.row_position),
                ok=False,
                expected_hash=bytes(row.expected_hash),
                stored_hash=bytes(row.stored_hash) if row.stored_hash is not None else b"",
            )
    return ChainTableResult(
        table_name=table_name,
        row_count=row_count,
        first_bad_row=first_bad,
    )


async def verify_all(uow: UnitOfWork) -> list[ChainTableResult]:
    """Verify every protected table.  Order matches :data:`CHAINED_TABLES`."""
    return [await verify_table(uow, name) for name in CHAINED_TABLES]


__all__ = [
    "CHAINED_TABLES",
    "ChainRowResult",
    "ChainTableResult",
    "verify_all",
    "verify_table",
]
