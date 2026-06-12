"""Tests for ``GET /api/v1/conflicts/{conflict_id}/candidates``.

This endpoint exists so the resolve UI can submit a stable ``winning_claim_id``
instead of indexing into a different array (the public evidence endpoint
deliberately hides claim ids).  The behaviour these tests pin down is the
exact contract the frontend resolve form relies on:

* The response carries one entry per claim_id on the conflict, with the
  claim's stable id, value, and the source's name + reliability tier.
* ``is_winning`` is True iff the candidate's ``claim_id`` matches
  ``conflict.winning_claim_id``.
* ``is_superseded`` is True iff the claim's own ``claim_type`` is SUPERSEDED.
* Claims whose source row is missing are silently omitted (defensive).
* The endpoint is gated by the same role tuple as the rest of /conflicts.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from atlas.domain.entities import Claim, ClaimConflict, Source
from atlas.domain.enums import ClaimType, ConflictStatus, SourceKind


def _seed_conflict(uow, conflict: ClaimConflict) -> None:
    """Seed a conflict + its claim_ids in the in-memory store.

    The fake conflict repository deliberately reloads ``claim_ids`` from a
    separate ``conflict_claim_links`` map on every ``.get()`` (mirroring how
    the real SQL repo rebuilds them from a join), so a test that only writes
    to ``store.conflicts`` will get an empty ``claim_ids`` back.  This helper
    writes both halves so the repository round-trip works.
    """
    uow.store.conflicts[conflict.id] = conflict
    uow.store.conflict_claim_links[conflict.id] = list(conflict.claim_ids)


@pytest.mark.asyncio
async def test_candidates_requires_auth(client):
    """No api key -> 401/403, never reaching the handler."""
    resp = await client.get(f"/api/v1/conflicts/{uuid4()}/candidates")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_candidates_404_when_conflict_missing(async_client_analyst, in_memory_uow):
    resp = await async_client_analyst.get(f"/api/v1/conflicts/{uuid4()}/candidates")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_candidates_returns_claim_ids_and_source_metadata(
    async_client_analyst, in_memory_uow
):
    """Happy path: each candidate carries the stable claim_id plus source info.

    This is the exact contract the resolve form needs: a list it can render
    with stable identifiers, so the form's submit can send the actual
    ``winning_claim_id`` instead of an index.
    """
    event_id = uuid4()

    ntsb = Source(name="NTSB", kind=SourceKind.EXTERNAL, reliability_tier=1)
    faa = Source(name="FAA", kind=SourceKind.EXTERNAL, reliability_tier=2)
    in_memory_uow.store.sources[ntsb.id] = ntsb
    in_memory_uow.store.sources[faa.id] = faa

    claim_ntsb = Claim(
        event_id=event_id,
        source_id=ntsb.id,
        field_name="aircraft_type",
        field_value="Boeing 737-800",
        claim_type=ClaimType.RAW,
    )
    claim_faa = Claim(
        event_id=event_id,
        source_id=faa.id,
        field_name="aircraft_type",
        field_value="Boeing 737-700",
        claim_type=ClaimType.RAW,
    )
    in_memory_uow.store.claims[claim_ntsb.id] = claim_ntsb
    in_memory_uow.store.claims[claim_faa.id] = claim_faa

    conflict = ClaimConflict(
        event_id=event_id,
        field_name="aircraft_type",
        status=ConflictStatus.OPEN,
        claim_ids=[claim_faa.id, claim_ntsb.id],  # intentionally reversed
    )
    _seed_conflict(in_memory_uow, conflict)

    resp = await async_client_analyst.get(
        f"/api/v1/conflicts/{conflict.id}/candidates"
    )
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["conflict_id"] == str(conflict.id)
    assert body["field_name"] == "aircraft_type"
    assert len(body["candidates"]) == 2

    # Tier 1 (NTSB) sorts before tier 2 (FAA): "lower = more trusted".
    # This proves the response order is NOT just conflict.claim_ids order,
    # which is the whole reason candidates exists.
    first, second = body["candidates"]
    assert first["source_name"] == "NTSB"
    assert first["claim_id"] == str(claim_ntsb.id)
    assert first["source_reliability_tier"] == 1
    assert first["field_value"] == "Boeing 737-800"
    assert first["is_winning"] is False
    assert first["is_superseded"] is False
    assert first["claim_type"] == "RAW"

    assert second["source_name"] == "FAA"
    assert second["claim_id"] == str(claim_faa.id)
    assert second["source_reliability_tier"] == 2


@pytest.mark.asyncio
async def test_candidates_marks_winning_on_resolved_conflict(
    async_client_analyst, in_memory_uow
):
    """is_winning must mirror ClaimConflict.winning_claim_id exactly."""
    event_id = uuid4()
    source = Source(name="NTSB", kind=SourceKind.EXTERNAL, reliability_tier=1)
    in_memory_uow.store.sources[source.id] = source

    winner = Claim(
        event_id=event_id,
        source_id=source.id,
        field_name="location",
        field_value="EHAM",
        claim_type=ClaimType.CONFIRMED,
    )
    loser = Claim(
        event_id=event_id,
        source_id=source.id,
        field_name="location",
        field_value="EDDF",
        claim_type=ClaimType.RAW,
    )
    in_memory_uow.store.claims[winner.id] = winner
    in_memory_uow.store.claims[loser.id] = loser

    conflict = ClaimConflict(
        event_id=event_id,
        field_name="location",
        status=ConflictStatus.RESOLVED,
        winning_claim_id=winner.id,
        claim_ids=[winner.id, loser.id],
    )
    _seed_conflict(in_memory_uow, conflict)

    resp = await async_client_analyst.get(
        f"/api/v1/conflicts/{conflict.id}/candidates"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    by_id = {c["claim_id"]: c for c in body["candidates"]}
    assert by_id[str(winner.id)]["is_winning"] is True
    assert by_id[str(loser.id)]["is_winning"] is False


@pytest.mark.asyncio
async def test_candidates_marks_superseded_and_sorts_them_last(
    async_client_analyst, in_memory_uow
):
    """Superseded claims appear in the response but sort after active ones."""
    event_id = uuid4()
    source = Source(name="NTSB", kind=SourceKind.EXTERNAL, reliability_tier=1)
    in_memory_uow.store.sources[source.id] = source

    replacement = Claim(
        event_id=event_id,
        source_id=source.id,
        field_name="operator",
        field_value="Colgan Air",
        claim_type=ClaimType.RAW,
    )
    old = Claim(
        event_id=event_id,
        source_id=source.id,
        field_name="operator",
        field_value="Continental Connection",
        claim_type=ClaimType.SUPERSEDED,
        superseded_by_claim_id=replacement.id,
    )
    in_memory_uow.store.claims[replacement.id] = replacement
    in_memory_uow.store.claims[old.id] = old

    conflict = ClaimConflict(
        event_id=event_id,
        field_name="operator",
        claim_ids=[old.id, replacement.id],
    )
    _seed_conflict(in_memory_uow, conflict)

    resp = await async_client_analyst.get(
        f"/api/v1/conflicts/{conflict.id}/candidates"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["candidates"]) == 2

    # Active first, superseded last regardless of conflict.claim_ids order.
    first, second = body["candidates"]
    assert first["claim_id"] == str(replacement.id)
    assert first["is_superseded"] is False
    assert second["claim_id"] == str(old.id)
    assert second["is_superseded"] is True
    assert second["superseded_by_claim_id"] == str(replacement.id)


@pytest.mark.asyncio
async def test_candidates_skips_claims_with_missing_source(
    async_client_analyst, in_memory_uow
):
    """A candidate with no resolvable source is unreviewable; omit it rather
    than emit a placeholder the UI could mistake for a real source."""
    event_id = uuid4()
    real = Source(name="NTSB", kind=SourceKind.EXTERNAL, reliability_tier=1)
    in_memory_uow.store.sources[real.id] = real

    good = Claim(
        event_id=event_id,
        source_id=real.id,
        field_name="registration",
        field_value="N200WQ",
        claim_type=ClaimType.RAW,
    )
    orphan = Claim(
        event_id=event_id,
        source_id=uuid4(),  # source never added to the store
        field_name="registration",
        field_value="N200WQ-typo",
        claim_type=ClaimType.RAW,
    )
    in_memory_uow.store.claims[good.id] = good
    in_memory_uow.store.claims[orphan.id] = orphan

    conflict = ClaimConflict(
        event_id=event_id,
        field_name="registration",
        claim_ids=[good.id, orphan.id],
    )
    _seed_conflict(in_memory_uow, conflict)

    resp = await async_client_analyst.get(
        f"/api/v1/conflicts/{conflict.id}/candidates"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["candidates"]) == 1
    assert body["candidates"][0]["claim_id"] == str(good.id)


@pytest.mark.asyncio
async def test_candidates_empty_when_no_claim_rows_exist(
    async_client_analyst, in_memory_uow
):
    """A conflict whose ``claim_ids`` all refer to absent claim rows yields
    an empty candidates list, NOT a 500.  This can happen transiently during
    archive flows; the UI should render an empty state."""
    event_id = uuid4()
    conflict = ClaimConflict(
        event_id=event_id,
        field_name="weather",
        claim_ids=[uuid4(), uuid4()],
    )
    _seed_conflict(in_memory_uow, conflict)

    resp = await async_client_analyst.get(
        f"/api/v1/conflicts/{conflict.id}/candidates"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["candidates"] == []
    assert body["field_name"] == "weather"


@pytest.mark.asyncio
async def test_candidates_readable_by_analyst_reviewer_admin(
    async_client_analyst, async_client_reviewer, async_client_admin, in_memory_uow
):
    """All three authenticated roles can read candidates (read-only endpoint)."""
    event_id = uuid4()
    source = Source(name="NTSB", kind=SourceKind.EXTERNAL, reliability_tier=1)
    in_memory_uow.store.sources[source.id] = source
    claim = Claim(
        event_id=event_id,
        source_id=source.id,
        field_name="x",
        field_value="y",
        claim_type=ClaimType.RAW,
    )
    in_memory_uow.store.claims[claim.id] = claim
    conflict = ClaimConflict(
        event_id=event_id, field_name="x", claim_ids=[claim.id]
    )
    _seed_conflict(in_memory_uow, conflict)

    for c in (async_client_analyst, async_client_reviewer, async_client_admin):
        resp = await c.get(f"/api/v1/conflicts/{conflict.id}/candidates")
        assert resp.status_code == 200, (c, resp.text)
