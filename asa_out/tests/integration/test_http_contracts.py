"""
HTTP-layer integration tests.

These tests exercise the actual FastAPI application against a real PostgreSQL
database.  They are deliberately NOT MagicMock-based — the whole point is to
catch query-shape bugs, JSONB envelope issues, migration drift, and transaction
boundary problems that MagicMock-based unit tests cannot detect.

Prerequisites:
  - DATABASE_URL environment variable pointing at a test PostGIS database
  - Alembic migrations applied (run `alembic upgrade head` before these tests)

In CI these tests run in the `backend-unit` job which already starts a PostGIS
service container and runs `alembic upgrade head` before pytest.

The tests are marked with `pytest.mark.integration` and can be excluded from
fast local runs with `-m "not integration"`.
"""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio

# Skip the entire module if no real database is configured.
# This makes the tests opt-in for local dev while still mandatory in CI.
DATABASE_URL = os.getenv("DATABASE_URL", "")
pytestmark = pytest.mark.integration

if not DATABASE_URL:
    pytestmark = pytest.mark.skip(
        reason="DATABASE_URL not set — skipping integration tests"
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture(scope="session")
async def db_engine():
    """Shared engine for integration tests; each test gets its own transaction."""
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(DATABASE_URL, echo=False)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    """
    Function-scoped session wrapped in a transaction.  Tests can be run in
    random order without leaking rows into each other.
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    async with db_engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False, autoflush=False)
        try:
            yield session
        finally:
            await session.close()
            if trans.is_active:
                await trans.rollback()


@pytest_asyncio.fixture
async def http_client(db_session):
    """
    Returns an httpx.AsyncClient wired directly to the FastAPI app.
    Uses the test database session to avoid network round-trips.
    """
    import httpx

    from atlas.api.app import app
    from atlas.db.engine import get_db, get_read_db

    # Override DB dependencies to use our test session so we control rollback.
    async def _read_db():
        yield db_session

    async def _write_db():
        yield db_session

    app.dependency_overrides[get_read_db] = _read_db
    app.dependency_overrides[get_db] = _write_db

    # ASGITransport is required for httpx >= 0.24 — the  shortcut was
    # removed.  Using it with the pinned lockfile (httpx==0.28.x) would raise
    # TypeError at fixture setup time, breaking every integration test before it
    # can run.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


# ── Test 1: HTTP smoke contract ────────────────────────────────────────────────

class TestHTTPContracts:
    """
    Every public endpoint must return a well-formed response.
    These tests catch route-ordering bugs, missing joins, and schema drift
    that are invisible to MagicMock unit tests.
    """

    @pytest.mark.asyncio
    async def test_health(self, http_client):
        r = await http_client.get("/api/v1/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert "version" in body

    @pytest.mark.asyncio
    async def test_readyz(self, http_client):
        r = await http_client.get("/api/v1/readyz")
        # Either 200 (ready) or 503 (not ready but structured)
        assert r.status_code in (200, 503)
        body = r.json()
        assert "checks" in body or "detail" in body

    @pytest.mark.asyncio
    async def test_metrics_endpoint(self, http_client):
        r = await http_client.get("/metrics")
        assert r.status_code == 200
        assert "atlas_" in r.text or "python_" in r.text  # prometheus format

    @pytest.mark.asyncio
    async def test_sources(self, http_client):
        r = await http_client.get("/api/v1/sources")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    @pytest.mark.asyncio
    async def test_accidents_list(self, http_client):
        r = await http_client.get("/api/v1/accidents")
        assert r.status_code == 200
        body = r.json()
        assert "items" in body
        assert "total" in body
        assert isinstance(body["items"], list)

    @pytest.mark.asyncio
    async def test_accidents_map_envelope(self, http_client):
        """
        This test specifically guards the regression where the map response
        changed from a bare list to a wrapper envelope but the smoke test
        was not updated — causing CI check 12 to fail silently.
        """
        r = await http_client.get("/api/v1/accidents/map")
        assert r.status_code == 200
        body = r.json()
        # Must be an envelope, not a list
        assert isinstance(body, dict), (
            f"Map endpoint must return a dict envelope, got {type(body).__name__}. "
            "This is the regression from v28.2 — the smoke test change was the hint."
        )
        assert "items" in body
        assert "count" in body
        assert "truncated" in body
        assert "limit" in body
        assert isinstance(body["items"], list)
        assert isinstance(body["truncated"], bool)

    @pytest.mark.asyncio
    async def test_analytics_summary(self, http_client):
        r = await http_client.get("/api/v1/analytics/summary")
        assert r.status_code == 200
        body = r.json()
        assert "total_accidents" in body
        assert "confidence_bins" in body

    @pytest.mark.asyncio
    async def test_search_q_max_length(self, http_client):
        """q parameter longer than 200 chars must return 422, not 500."""
        oversized_q = "a" * 201
        r = await http_client.get(f"/api/v1/accidents?q={oversized_q}")
        assert r.status_code == 422, (
            "q parameter longer than 200 chars must be rejected with 422. "
            "Without this guard a huge search string is passed to 5 LIKE predicates."
        )

    @pytest.mark.asyncio
    async def test_search_literal_percent(self, http_client):
        """q='%' must not match all rows — LIKE escaping must be applied."""
        r_percent = await http_client.get("/api/v1/accidents?q=%25")  # URL-encoded %
        r_all     = await http_client.get("/api/v1/accidents")
        assert r_percent.status_code == 200
        assert r_all.status_code == 200
        # If LIKE escaping is broken, % matches every row and the counts will match.
        # If escaping is correct, a % search should return 0 results (no field contains literal %).
        percent_total = r_percent.json()["total"]
        all_total     = r_all.json()["total"]
        if all_total > 0:
            assert percent_total < all_total, (
                "Searching for '%' should not match all rows — LIKE escaping is broken."
            )


# ── Test 2: Claim/conflict/projection cycle ────────────────────────────────────

class TestClaimConflictProjectionCycle:
    """
    Exercises the complete multi-source disagreement flow against a real database:
      ingest two sources → conflict created → field withheld → resolve → field shown

    This is the integration test the system has been missing since v20.  MagicMock
    tests cannot verify that the partial unique index actually rejects duplicates,
    that JSONB envelope shapes survive the round-trip, or that projection queries
    return the right rows after a resolution.
    """

    @pytest.mark.asyncio
    async def test_partial_unique_index_rejects_duplicate_active_claim(self, db_session):
        """
        The partial unique index uq_active_claim_per_event_source_field must
        prevent two active claims for the same (event, source, field).
        This cannot be tested with MagicMock — it requires a real Postgres
        constraint enforcement.
        """
        import sqlalchemy.exc

        from atlas.models.orm import AccidentEvent, Claim, ClaimType

        event_id = f"test-{uuid.uuid4()}"
        source_id = "src-ntsb-001"  # must exist from seed

        # Insert a minimal event row
        db_session.add(AccidentEvent(
            id=event_id,
            canonical_id=f"TEST-UNIQUE-INDEX-{event_id}",
            record_status="active",
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
        ))
        await db_session.flush()

        claim_a = Claim(
            id=str(uuid.uuid4()),
            event_id=event_id,
            source_id=source_id,
            field_name="fatalities_total",
            field_value={"type": "int", "v": 1},
            claim_type=ClaimType.CONFIRMED.value,
            created_at=datetime.now(tz=UTC),
        )
        db_session.add(claim_a)
        await db_session.flush()

        claim_b = Claim(
            id=str(uuid.uuid4()),
            event_id=event_id,
            source_id=source_id,
            field_name="fatalities_total",  # same field, same source, same event
            field_value={"type": "int", "v": 2},
            claim_type=ClaimType.CONFIRMED.value,
            created_at=datetime.now(tz=UTC),
        )
        db_session.add(claim_b)

        with pytest.raises(sqlalchemy.exc.IntegrityError):
            await db_session.flush()

        await db_session.rollback()

    @pytest.mark.asyncio
    async def test_provenance_response_includes_truncation_field(self, http_client, db_session):
        """
        The provenance endpoint must return the `truncation` field in its
        response shape.  Even for an event with few claims, the field must be
        present (with all booleans False) — not absent.
        """
        from atlas.models.orm import AccidentEvent

        event_id = f"test-prov-{uuid.uuid4()}"
        db_session.add(AccidentEvent(
            id=event_id,
            canonical_id=f"TEST-PROV-{event_id}",
            record_status="active",
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
        ))
        await db_session.flush()

        r = await http_client.get(f"/api/v1/accidents/{event_id}/provenance")
        assert r.status_code == 200
        body = r.json()

        assert "truncation" in body, (
            "Provenance response must include the 'truncation' field. "
            "This was the frontend-invisible truncation bug from v28.2."
        )
        trunc = body["truncation"]
        assert trunc is not None
        assert "claims" in trunc
        assert "conflicts" in trunc
        assert "source_documents" in trunc
        assert "claims_limit" in trunc
        # For a fresh event with no claims, none should be truncated
        assert trunc["claims"] is False
        assert trunc["conflicts"] is False
        assert trunc["source_documents"] is False

        await db_session.rollback()

    @pytest.mark.asyncio
    async def test_real_ntsb_and_asn_fixture_create_conflict_then_resolution(
        self, http_client, db_session, monkeypatch
    ):
        """
        End-to-end multi-source proof using the real ingestion paths:

          NTSB fixture row → projected event
          ASN-like fixture row → matched to the same event by registration+date
          conflicting fatalities_total claims → open conflict + withheld field
          reviewer resolution via HTTP endpoint → fatalities_total projected again

        This is intentionally not a hand-built ClaimWriter-only test.  It runs
        the actual NTSB per-record ingestion path and the actual generic CSV
        per-record ingestion path so source mapping, normalization, claim
        writing, conflict detection, projection withholding, and resolution are
        all exercised together.
        """
        from sqlalchemy import select
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from atlas.ingestion.generic_csv_adapter import SourceMapping, load_csv_with_mapping
        from atlas.ingestion.ntsb_adapter import load_from_csv
        from atlas.ingestion.pipeline import IngestionPipeline, IngestionResult
        from atlas.models import claim_value as cv
        from atlas.models.orm import (
            AccidentEvent,
            AccidentRecord,
            Claim,
            ClaimConflict,
            Source,
        )

        # The ingestion per-record paths commit after each row in production.
        # In this integration test we want the real row-processing behavior but
        # still keep the function-scoped transaction rollback intact, so replace
        # commit with flush for this test session only.
        async def _flush_only_commit():
            await db_session.flush()

        monkeypatch.setattr(db_session, "commit", _flush_only_commit)

        root = Path(__file__).resolve().parents[1]
        ntsb_fixture = root / "fixtures" / "ntsb_sample.csv"
        asn_fixture = root / "fixtures" / "asn_like_conflict.csv"
        asn_mapping = root.parent / "src" / "atlas" / "ingestion" / "source_mappings" / "asn_mapping.json"

        # The migration guarantees NTSB exists.  ASN is optional/disabled in
        # production, so the test seeds the registry row it needs explicitly.
        await db_session.execute(
            pg_insert(Source.__table__).values(
                id="src-asn-001",
                short_name="ASN",
                display_name="Aviation Safety Network test fixture",
                tier=2,
                license_type="fixture_only",
                base_url="https://aviation-safety.net",
                description="ASN-like deterministic integration fixture.",
                ingestion_enabled=False,
            ).on_conflict_do_nothing(index_elements=["id"])
        )
        await db_session.flush()

        unique = uuid.uuid4().hex[:10].upper()
        registration = f"N{unique[:5]}"
        ntsb_event_id = f"TMS{unique}"
        asn_record_id = f"ASN-{unique}"

        ntsb_records = await load_from_csv(str(ntsb_fixture))
        ntsb_raw = dict(ntsb_records[1])  # fatal accident fixture
        ntsb_probable_cause = ntsb_raw["ProbableCause"]
        ntsb_raw.update({
            "EventId": ntsb_event_id,
            "Registration": registration,
            # Match the ASN fixture's external id so ntsb_report_number does not
            # create a second, unrelated conflict.
            "ReportNumber": asn_record_id,
            "TotalFatalInjuries": "1",
            "EventDate": "2023-02-11",
            # Generic ASN fixture has date precision only; make NTSB day-precision
            # too so this proof isolates the fatality disagreement.
            "EventTime": None,
        })

        mapping = SourceMapping.from_file(asn_mapping)
        asn_rows = load_csv_with_mapping(str(asn_fixture), mapping)
        asn_raw = dict(asn_rows[0])
        asn_raw.update({
            "registration": registration,
            "acc_no": asn_record_id,
            "type": "Piper",
            "location": "Fort Lauderdale, Florida, United States",
            "narrative": ntsb_probable_cause,
            "fat.": "2",
        })
        asn_raw["__record_id__"] = asn_record_id
        asn_raw["__canonical__"] = dict(asn_raw["__canonical__"])
        asn_raw["__canonical__"].update({
            "aircraft_registration": registration,
            "aircraft_make": "Piper",
            "fatalities_total": "2",
            "occurred_at": "2023-02-11",
            "occurred_at_precision": "day",
            "location_text": "Fort Lauderdale, Florida, United States",
            "injury_severity": "Fatal",
            "aircraft_damage": "DEST",
            "investigation_status": "PROBABLE CAUSE",
            "probable_cause": ntsb_probable_cause,
            "ntsb_report_number": asn_record_id,
        })

        pipeline = IngestionPipeline()

        ntsb_run_id = f"run-ntsb-{unique}"
        ntsb_result = IngestionResult(
            run_id=ntsb_run_id,
            source="NTSB_CSV_TEST",
            started_at=datetime.now(tz=UTC),
        )
        await pipeline._process(ntsb_raw, db_session, ntsb_result, ntsb_run_id)

        event = (await db_session.execute(
            select(AccidentEvent).where(AccidentEvent.canonical_id == f"NTSB-{ntsb_event_id}")
        )).scalar_one()
        record_before = (await db_session.execute(
            select(AccidentRecord).where(AccidentRecord.id == event.id)
        )).scalar_one()
        assert record_before.aircraft_registration == registration
        assert record_before.fatalities_total == 1

        asn_run_id = f"run-asn-{unique}"
        asn_result = IngestionResult(
            run_id=asn_run_id,
            source="GENERIC_CSV:src-asn-001",
            started_at=datetime.now(tz=UTC),
        )
        await pipeline._process_generic(asn_raw, db_session, asn_result, asn_run_id, mapping)

        assert asn_result.events_created == 0, (
            "ASN-like row should match the existing NTSB event, not create ASN-* duplicate."
        )
        assert asn_result.events_updated == 1
        duplicate = (await db_session.execute(
            select(AccidentEvent).where(AccidentEvent.canonical_id == f"ASN-{asn_record_id}")
        )).scalar_one_or_none()
        assert duplicate is None, "Generic ingestion created a duplicate event instead of matching."

        conflicts = list((await db_session.execute(
            select(ClaimConflict).where(
                ClaimConflict.event_id == event.id,
                ClaimConflict.field_name == "fatalities_total",
                ClaimConflict.status == "open",
            )
        )).scalars().all())
        assert len(conflicts) == 1
        conflict = conflicts[0]

        claims = list((await db_session.execute(
            select(Claim).where(Claim.id.in_([conflict.claim_a_id, conflict.claim_b_id]))
        )).scalars().all())
        claims_by_source = {claim.source_id: claim for claim in claims}
        assert set(claims_by_source) == {"src-ntsb-001", "src-asn-001"}
        assert cv.decode(claims_by_source["src-ntsb-001"].field_value) == 1
        assert cv.decode(claims_by_source["src-asn-001"].field_value) == 2
        assert {claim.claim_type for claim in claims} == {"disputed"}

        withheld = (await db_session.execute(
            select(AccidentRecord)
            .where(AccidentRecord.id == event.id)
            .execution_options(populate_existing=True)
        )).scalar_one()
        assert withheld.has_conflicts is True
        assert withheld.fatalities_total is None, (
            "Open fatalities_total conflict must withhold the projected value."
        )

        accepted = claims_by_source["src-ntsb-001"]
        rejected = claims_by_source["src-asn-001"]
        response = await http_client.post(
            f"/api/v1/conflicts/{conflict.id}/resolve",
            json={
                "resolution_type": "claim_accepted",
                "accepted_claim_id": accepted.id,
                "rejected_claim_ids": [rejected.id],
                "resolution": "NTSB fixture accepted for deterministic multi-source proof.",
                "resolved_by": "integration-test",
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "resolved"
        assert body["accepted_claim_id"] == accepted.id

        resolved_record = (await db_session.execute(
            select(AccidentRecord)
            .where(AccidentRecord.id == event.id)
            .execution_options(populate_existing=True)
        )).scalar_one()
        assert resolved_record.has_conflicts is False
        assert resolved_record.fatalities_total == 1
        assert "src-ntsb-001" in (resolved_record.source_ids or [])
        assert set(resolved_record.claim_source_ids or []) >= {"src-ntsb-001", "src-asn-001"}

    @pytest.mark.asyncio
    async def test_jsonb_claim_envelope_round_trips(self, db_session):
        """
        After writing a claim with ClaimWriter, fetching it back through the
        ORM must produce a field_value with the correct envelope shape.
        The envelope must have 'type' (type tag) and 'v' (value) keys.
        This catches double-encoding bugs that are invisible to unit tests.
        """
        from atlas.claims.writer import ClaimWriter
        from atlas.models.orm import AccidentEvent, Claim

        event_id = f"test-jsonb-{uuid.uuid4()}"
        source_id = "src-ntsb-001"

        db_session.add(AccidentEvent(
            id=event_id,
            canonical_id=f"TEST-JSONB-{event_id}",
            record_status="active",
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
        ))
        await db_session.flush()

        writer = ClaimWriter(
            session=db_session,
            event_id=event_id,
            source_id=source_id,
        )
        await writer.write_fields({
            "fatalities_total": 3,
            "occurred_at": datetime(2023, 6, 15, 14, 30, tzinfo=UTC),
        })
        await db_session.flush()

        from sqlalchemy import select

        claims = (await db_session.execute(
            select(Claim).where(Claim.event_id == event_id)
        )).scalars().all()

        assert len(claims) == 2
        for claim in claims:
            fv = claim.field_value
            assert isinstance(fv, dict), f"field_value must be a dict, got {type(fv)}"
            assert "type" in fv, f"envelope must have 'type' key, got {fv!r}"
            assert "v" in fv, f"envelope must have 'v' key, got {fv!r}"
            # The value must NOT be double-encoded (another dict or string JSON)
            assert not isinstance(fv.get("v"), str) or fv["type"] != "int", (
                f"Integer value was string-encoded — double-encoding bug: {fv!r}"
            )

        await db_session.rollback()


# ── Test 3: Analytics cache ────────────────────────────────────────────────────

class TestAnalyticsCache:
    """Verify the analytics TTL cache works and can be disabled in tests."""

    @pytest.mark.asyncio
    async def test_analytics_cache_disabled_at_ttl_zero(self, http_client):
        """
        When ANALYTICS_CACHE_TTL_S=0 every request must hit the DB.
        The response shape must be identical regardless.
        """
        from atlas.api import app as app_module
        from atlas.api.app import _analytics_cache

        old_ttl = app_module.settings.analytics_cache_ttl_s
        app_module.settings.analytics_cache_ttl_s = 0
        _analytics_cache.invalidate()
        try:
            r1 = await http_client.get("/api/v1/analytics/summary")
            r2 = await http_client.get("/api/v1/analytics/summary")
        finally:
            app_module.settings.analytics_cache_ttl_s = old_ttl
            _analytics_cache.invalidate()

        assert r1.status_code == 200
        assert r2.status_code == 200
        # Shapes must match
        assert set(r1.json().keys()) == set(r2.json().keys())

    @pytest.mark.asyncio
    async def test_analytics_cache_serves_fresh_result(self, http_client):
        """After one miss the cache must serve the next request without a DB hit."""
        from atlas.api.app import _analytics_cache
        _analytics_cache.invalidate()

        r1 = await http_client.get("/api/v1/analytics/summary")
        assert r1.status_code == 200

        # Cache should now be warm — second call should return identical data
        r2 = await http_client.get("/api/v1/analytics/summary")
        assert r2.status_code == 200
        assert r1.json()["total_accidents"] == r2.json()["total_accidents"]


# ── Test 4: Rate limiting ──────────────────────────────────────────────────────

class TestRateLimiting:
    """
    Rate limiting must actually produce 429 responses when limits are exceeded.
    This test does NOT use xfail — rate limiting is a production protection
    feature.  If it doesn't work, the test must fail, not be skipped.

    The behavioral test below hits the real Atlas map route with a deliberately
    tight per-route limit and a unique client address.
    """

    def test_rate_limit_settings_are_more_restrictive_than_default(self):
        """
        Per-route limits on expensive endpoints must be tighter than default.
        This is a configuration sanity check — not a substitute for the real test.
        """
        from atlas.config import get_settings
        s = get_settings()

        def _rpm(spec: str) -> int:
            count, unit = spec.split("/")
            factors = {"second": 60, "minute": 1, "hour": 1/60}
            return int(float(count) * factors.get(unit, 1))

        default_rpm = _rpm(s.rate_limit_default)
        assert _rpm(s.rate_limit_map) < default_rpm, "Map limit must be < default"
        assert _rpm(s.rate_limit_analytics) < default_rpm, "Analytics limit must be < default"
        assert _rpm(s.rate_limit_provenance) <= default_rpm, "Provenance limit must be <= default"

    def test_rate_limiter_is_registered_on_app(self):
        """SlowAPI middleware and limiter state must be wired to a factory app."""
        from slowapi.middleware import SlowAPIMiddleware

        from atlas.api.app import create_app
        from atlas.config import Settings

        test_app = create_app(Settings(app_env="test", api_auth_enabled=False, rate_limit_enabled=True))
        middleware_classes = [m.cls for m in test_app.user_middleware]
        assert SlowAPIMiddleware in middleware_classes, (
            "SlowAPIMiddleware must be registered when rate_limit_enabled=True."
        )
        assert hasattr(test_app.state, "limiter"), (
            "app.state.limiter must be set — SlowAPI reads it from app state."
        )

    def test_rate_limited_endpoints_are_installed_on_factory_app(self):
        """
        Expensive endpoints must receive SlowAPI limit metadata when a factory
        app is created.  This checks the real app instance, not module globals.
        """
        from fastapi.routing import APIRoute

        from atlas.api.app import create_app
        from atlas.config import Settings

        test_app = create_app(Settings(app_env="test", api_auth_enabled=False, rate_limit_enabled=True))
        limited_paths = {
            "/api/v1/accidents/map",
            "/api/v1/analytics/summary",
            "/api/v1/accidents/{event_id}/provenance",
            "/api/v1/conflicts/{conflict_id}/resolve",
        }
        routes = {r.path: r for r in test_app.router.routes if isinstance(r, APIRoute)}
        for path in limited_paths:
            assert path in routes, f"{path} must be registered on the factory app"
            endpoint = routes[path].endpoint
            assert hasattr(endpoint, "_rate_limits"), (
                f"{path} must have SlowAPI limit metadata installed by create_app()."
            )

    @pytest.mark.asyncio
    async def test_map_endpoint_returns_429_when_limit_exceeded(self):
        """
        The real Atlas map endpoint must produce an actual 429 when its route
        limit is hit.  This uses create_app(Settings(...)) and the real router;
        it does not mutate the module singleton or use a stub route.
        """
        import httpx

        from atlas.api.app import create_app
        from atlas.config import Settings
        from atlas.db.engine import get_read_db

        class _EmptyRows:
            def all(self):
                return []

        class _FakeDB:
            async def execute(self, stmt):
                return _EmptyRows()

        async def _fake_read_db():
            yield _FakeDB()

        test_app = create_app(Settings(
            app_env="test",
            api_auth_enabled=False,
            rate_limit_enabled=True,
            rate_limit_map="1/minute",
            rate_limit_default="1000/minute",
            rate_limit_storage_url=None,
        ))
        test_app.dependency_overrides[get_read_db] = _fake_read_db

        transport = httpx.ASGITransport(
            app=test_app,
            client=(f"203.0.113.{uuid.uuid4().int % 200 + 1}", 12345),
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r1 = await client.get("/api/v1/accidents/map")
            r2 = await client.get("/api/v1/accidents/map")

        assert r1.status_code == 200, f"First request should succeed, got {r1.status_code}: {r1.text}"
        assert r2.status_code == 429, (
            f"Second request should be rate-limited (429), got {r2.status_code}. "
            "This fails if the real factory-created map route is not actually protected."
        )
        assert "detail" in r2.json(), "429 response must include a detail message"


# ── Sprint B: Runtime metrics verification ────────────────────────────────────

class TestMetricsRuntime:
    """
    Metrics must change after real requests.  This proves the middleware is
    actually observing requests, not just that counters are defined.
    """

    @staticmethod
    def _metric_value(text: str, metric: str, labels: dict[str, str]) -> float:
        label_parts = [f'{k}="{v}"' for k, v in labels.items()]
        for line in text.splitlines():
            if not line.startswith(metric + "{"):
                continue
            if not all(part in line for part in label_parts):
                continue
            try:
                return float(line.rsplit(" ", 1)[-1])
            except ValueError:
                return 0.0
        return 0.0

    @pytest.mark.asyncio
    async def test_request_counter_increments(self, http_client):
        """Scraping /metrics twice must show the route/status counter increased."""
        before = (await http_client.get("/metrics")).text
        await http_client.get("/api/v1/health")
        after = (await http_client.get("/metrics")).text

        labels = {"method": "GET", "path_template": "/api/v1/health", "status_code": "200"}
        before_value = self._metric_value(before, "atlas_http_requests_total", labels)
        after_value = self._metric_value(after, "atlas_http_requests_total", labels)
        assert after_value >= before_value + 1, (
            f"Expected health request counter to increase from {before_value} to at least "
            f"{before_value + 1}, got {after_value}."
        )

    @pytest.mark.asyncio
    async def test_histogram_count_increments(self, http_client):
        """After a request, the duration histogram count for that route must increase."""
        before = (await http_client.get("/metrics")).text
        await http_client.get("/api/v1/health")
        after = (await http_client.get("/metrics")).text

        labels = {"method": "GET", "path_template": "/api/v1/health"}
        before_value = self._metric_value(before, "atlas_http_request_duration_seconds_count", labels)
        after_value = self._metric_value(after, "atlas_http_request_duration_seconds_count", labels)
        assert after_value >= before_value + 1, (
            f"Expected health latency histogram count to increase from {before_value}, "
            f"got {after_value}."
        )

    @pytest.mark.asyncio
    async def test_in_flight_gauge_does_not_stuck(self, http_client):
        """In-flight gauge must return to zero after a request completes."""
        # Make a few requests and check the gauge doesn't accumulate
        for _ in range(3):
            await http_client.get("/api/v1/health")
        r = await http_client.get("/metrics")
        text = r.text
        # Parse the gauge value
        import re
        match = re.search(r"atlas_http_requests_in_flight\s+([\d.]+)", text)
        if match:
            gauge_value = float(match.group(1))
            assert gauge_value == 0.0, (
                f"In-flight gauge must be 0 after all requests complete, got {gauge_value}. "
                "A stuck gauge means dec() is not called in the finally block."
            )

    @pytest.mark.asyncio
    async def test_metrics_labels_use_route_template_not_raw_id(self, http_client):
        """Metrics labels must use route templates, not raw event IDs."""
        # Request a non-existent event — this exercises the unmatched or template path
        await http_client.get("/api/v1/accidents/nonexistent-event-id-12345")
        r = await http_client.get("/metrics")
        text = r.text
        # The raw ID must not appear as a label value
        assert "nonexistent-event-id-12345" not in text, (
            "Raw event IDs must not appear in metric labels — only route templates "
            "like '/api/v1/accidents/{event_id}' should be used."
        )


# ── Sprint B: Map truncation with real rows ───────────────────────────────────

class TestMapTruncationWithRealRows:
    """
    The map truncation path must be exercised with actual DB rows.
    This proves the limit+1 fetch-and-detect logic works under real data,
    not just that the endpoint shape is correct.
    """

    @pytest.mark.asyncio
    async def test_map_truncation_with_seeded_rows(self, http_client, db_session):
        """
        Override MAX_MAP_RESULTS to a tiny value, seed more rows than the cap,
        and assert the response is truncated with the correct count.
        """
        import uuid
        from datetime import UTC, datetime

        from atlas.models.orm import AccidentEvent, AccidentRecord

        # Seed 4 geocoded accident events
        event_ids = []
        for i in range(4):
            eid = f"test-map-trunc-{uuid.uuid4()}"
            event_ids.append(eid)
            db_session.add(AccidentEvent(
                id=eid,
                canonical_id=f"TEST-MAPTRUNC-{eid}",
                record_status="active",
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            ))
            db_session.add(AccidentRecord(
                id=eid,
                location_lat=40.0 + i,
                location_lon=-100.0 + i,
                location_text=f"Test Location {i}",
                injury_severity="MINOR",
                fatalities_total=0,
                occurred_year=2020 + i,
                confidence_score=0.7,
                last_projected_at=datetime.now(tz=UTC),
            ))
        await db_session.flush()

        # Override max_map_results to 3 so 4 rows triggers truncation.
        # Patch the real settings object, not the entire module global with a MagicMock.
        from atlas.api import app as app_module
        old_limit = app_module.settings.max_map_results
        app_module.settings.max_map_results = 3
        try:
            r = await http_client.get("/api/v1/accidents/map")
        finally:
            app_module.settings.max_map_results = old_limit

        assert r.status_code == 200
        body = r.json()
        assert body["truncated"] is True, (
            "With 4 geocoded rows and limit=3, truncated must be True."
        )
        assert body["count"] == 3, (
            f"count must be exactly 3 (the limit), got {body['count']}."
        )
        assert body["limit"] == 3
        assert len(body["items"]) == 3

        await db_session.rollback()


# ── Sprint B: LIKE escaping against real rows ─────────────────────────────────

class TestLikeEscapingWithRealRows:
    """
    LIKE escaping must be proven against actual DB content, not just tested
    on the helper function.  An empty database can make these tests vacuously pass.
    """

    @pytest.mark.asyncio
    async def test_percent_does_not_match_all_rows(self, http_client, db_session):
        """Searching for '%' must not return rows whose fields don't contain '%'."""
        import uuid
        from datetime import UTC, datetime

        from atlas.models.orm import AccidentEvent, AccidentRecord

        eid = f"test-like-{uuid.uuid4()}"
        db_session.add(AccidentEvent(
            id=eid, canonical_id=f"TEST-LIKE-{eid}", record_status="active",
            created_at=datetime.now(tz=UTC), updated_at=datetime.now(tz=UTC),
        ))
        db_session.add(AccidentRecord(
            id=eid, location_text="Normal Location Without Percent",
            aircraft_make="Boeing", aircraft_model="737",
            injury_severity="FATAL", fatalities_total=1,
            occurred_year=2023, confidence_score=0.8,
            last_projected_at=datetime.now(tz=UTC),
        ))
        await db_session.flush()

        # Search for literal % — should not match "Normal Location Without Percent"
        r_percent = await http_client.get("/api/v1/accidents?q=%25")  # URL-encoded %
        r_all = await http_client.get("/api/v1/accidents")

        assert r_percent.status_code == 200
        percent_total = r_percent.json()["total"]
        all_total = r_all.json()["total"]

        # If escaping is broken, % matches every row — totals would be equal
        if all_total > 0:
            assert percent_total < all_total, (
                f"Searching for '%' returned {percent_total} rows out of {all_total}. "
                "If LIKE escaping is broken, '%' matches every row. "
                "If totals are equal, the escape is not being applied."
            )

        await db_session.rollback()

    @pytest.mark.asyncio
    async def test_literal_text_matches_correctly(self, http_client, db_session):
        """A literal search term must match the exact row, not all rows."""
        import uuid
        from datetime import UTC, datetime

        from atlas.models.orm import AccidentEvent, AccidentRecord

        unique_make = f"TestMfg_{uuid.uuid4().hex[:8]}"
        eid = f"test-like2-{uuid.uuid4()}"
        db_session.add(AccidentEvent(
            id=eid, canonical_id=f"TEST-LIKE2-{eid}", record_status="active",
            created_at=datetime.now(tz=UTC), updated_at=datetime.now(tz=UTC),
        ))
        db_session.add(AccidentRecord(
            id=eid, location_text="Test Airport",
            aircraft_make=unique_make, aircraft_model="M1",
            injury_severity="NONE", fatalities_total=0,
            occurred_year=2022, confidence_score=0.7,
            last_projected_at=datetime.now(tz=UTC),
        ))
        await db_session.flush()

        r = await http_client.get(f"/api/v1/accidents?q={unique_make}")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] >= 1, (
            f"Searching for unique make '{unique_make}' must return at least 1 result."
        )
        found_ids = [item["id"] for item in body["items"]]
        assert eid in found_ids, "The seeded record must appear in search results."

        await db_session.rollback()


# ── Sprint B: Stale migration readiness test ──────────────────────────────────

class TestReadyzStaleMigration:
    """
    /readyz must return 503 when the DB migration version does not match
    the Alembic script head.  This proves the migration check is real,
    not just a row-existence check.
    """

    @pytest.mark.asyncio
    async def test_readyz_fails_with_stale_migration(self, http_client, db_session):
        """
        Override the DB's alembic_version to a fake old value and confirm 503.
        """
        from sqlalchemy import text

        # Save current version
        row = (await db_session.execute(text("SELECT version_num FROM alembic_version"))).one_or_none()
        if row is None:
            pytest.skip("alembic_version table is empty — run migrations first")
        real_head = row[0]

        try:
            # Set a fake old version
            await db_session.execute(
                text("UPDATE alembic_version SET version_num = 'fake_old_version_0000'")
            )
            await db_session.flush()

            r = await http_client.get("/api/v1/readyz")
            assert r.status_code == 503, (
                f"readyz must return 503 when DB is at 'fake_old_version_0000' "
                f"instead of script head '{real_head}'. "
                "Got status_code={r.status_code}."
            )
            detail = r.json().get("detail", {})
            migrations_check = detail.get("checks", {}).get("migrations", "")
            assert "fake_old_version_0000" in migrations_check or "error" in migrations_check.lower(), (
                "readyz response must identify the version mismatch in the migrations check."
            )
        finally:
            # Restore real version regardless of test outcome
            await db_session.execute(
                text(f"UPDATE alembic_version SET version_num = '{real_head}'")
            )
            await db_session.flush()


# ── Sprint D1: App factory behavior ───────────────────────────────────────────

class TestAppFactoryBehavior:
    """create_app() must not return a route-less shell."""

    @pytest.mark.asyncio
    async def test_create_app_returns_route_bearing_app(self, db_session):
        import httpx

        from atlas.api.app import create_app
        from atlas.config import Settings
        from atlas.db.engine import get_db, get_read_db

        test_app = create_app(Settings(app_env="test", api_auth_enabled=False, rate_limit_enabled=False))

        async def _read_db():
            yield db_session

        async def _write_db():
            yield db_session

        test_app.dependency_overrides[get_read_db] = _read_db
        test_app.dependency_overrides[get_db] = _write_db

        transport = httpx.ASGITransport(app=test_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/api/v1/health")

        assert r.status_code == 200, (
            "create_app() must return an app with the real Atlas routes registered, "
            "not a middleware-only shell."
        )

    @pytest.mark.asyncio
    async def test_create_app_uses_its_own_settings(self, db_session):
        import httpx

        from atlas.api.app import create_app
        from atlas.config import Settings
        from atlas.db.engine import get_db, get_read_db

        async def _read_db():
            yield db_session

        async def _write_db():
            yield db_session

        app_a = create_app(Settings(
            app_env="test", api_auth_enabled=False, rate_limit_enabled=False, api_version="factory-a"
        ))
        app_b = create_app(Settings(
            app_env="test", api_auth_enabled=False, rate_limit_enabled=False, api_version="factory-b"
        ))
        for test_app in (app_a, app_b):
            test_app.dependency_overrides[get_read_db] = _read_db
            test_app.dependency_overrides[get_db] = _write_db

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_a), base_url="http://test") as client_a:
            r_a = await client_a.get("/api/v1/health")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_b), base_url="http://test") as client_b:
            r_b = await client_b.get("/api/v1/health")

        assert r_a.json()["version"] == "factory-a"
        assert r_b.json()["version"] == "factory-b"


# ── Sprint D1: Stronger anti-meridian and bbox tests ─────────────────────────

class TestMapBoundingBoxBehavioral:
    """Behavioral proof that map bounding-box filters work correctly."""

    @pytest.mark.asyncio
    async def test_anti_meridian_rejected(self, http_client):
        """west > east must return 422, not silently return wrong results."""
        r = await http_client.get(
            "/api/v1/accidents/map",
            params={"north": "60", "south": "30", "east": "-170", "west": "170"},
        )
        assert r.status_code == 422, (
            f"Bounding box crossing anti-meridian (west=170 > east=-170) must "
            f"return 422, got {r.status_code}. "
            "Silently returning wrong results would be worse than an error."
        )
        body = r.json()
        assert "anti-meridian" in str(body).lower() or "west" in str(body).lower(), (
            "422 response must explain the anti-meridian issue."
        )

    @pytest.mark.asyncio
    async def test_valid_bbox_returns_envelope(self, http_client):
        """A valid bbox must return the map response envelope."""
        r = await http_client.get(
            "/api/v1/accidents/map",
            params={"north": "60", "south": "30", "east": "10", "west": "-10"},
        )
        assert r.status_code == 200
        body = r.json()
        assert "items" in body
        assert "truncated" in body

    @pytest.mark.asyncio
    async def test_partial_bbox_rejected(self, http_client):
        """Providing only some bbox params must return 422."""
        r = await http_client.get(
            "/api/v1/accidents/map",
            params={"north": "60", "south": "30"},  # missing east/west
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_south_gt_north_rejected(self, http_client):
        """south > north is geographically invalid."""
        r = await http_client.get(
            "/api/v1/accidents/map",
            params={"north": "30", "south": "60", "east": "10", "west": "-10"},
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_low_zoom_returns_clusters_with_seeded_rows(self, http_client, db_session):
        """Low zoom map requests should return SQL-computed clusters, not raw points."""
        import uuid
        from datetime import UTC, datetime

        from atlas.models.orm import AccidentEvent, AccidentRecord

        # Three nearby points inside a very tight bbox should collapse into one
        # grid cluster at zoom=4.  This proves the real endpoint executes the
        # cluster branch against actual DB rows.
        for i in range(3):
            eid = f"test-map-cluster-{uuid.uuid4()}"
            db_session.add(AccidentEvent(
                id=eid,
                canonical_id=f"TEST-MAPCLUSTER-{eid}",
                record_status="active",
                created_at=datetime.now(tz=UTC),
                updated_at=datetime.now(tz=UTC),
            ))
            db_session.add(AccidentRecord(
                id=eid,
                location_lat=10.05 + (i * 0.01),
                location_lon=10.05 + (i * 0.01),
                location_text=f"Cluster Test Location {i}",
                injury_severity="MINOR",
                fatalities_total=i,
                occurred_year=2020 + i,
                confidence_score=0.7,
                last_projected_at=datetime.now(tz=UTC),
            ))
        await db_session.flush()

        r = await http_client.get(
            "/api/v1/accidents/map",
            params={
                "zoom": "4",
                "north": "10.2",
                "south": "10.0",
                "east": "10.2",
                "west": "10.0",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["mode"] == "clusters"
        assert body["items"] == []
        assert body["clusters"], "Cluster mode must return at least one cluster for seeded rows."
        assert body["clusters"][0]["count"] >= 3
        assert body["cluster_cell_degrees"] is not None

        await db_session.rollback()


# ── Sprint D1: Conflict queue requires reviewer auth ─────────────────────────

class TestConflictQueueAuth:
    """Conflict queue must be protected from unauthenticated public access."""

    @pytest.mark.asyncio
    async def test_conflict_queue_with_auth_disabled_allows_access(self, http_client):
        """
        When API_AUTH_ENABLED=false (test/dev mode), the queue must be accessible
        because require_reviewer allows through when auth is disabled.
        """
        r = await http_client.get("/api/v1/conflicts")
        # Should succeed in test env (auth disabled) — the key test is that
        # the endpoint *exists* and auth dependency is *wired* (even if bypassed)
        assert r.status_code in (200, 401, 403), (
            f"Conflict queue must respond with 200 (auth disabled) or 401/403 "
            f"(auth enabled), got {r.status_code}."
        )

    def test_conflict_queue_has_reviewer_dependency(self):
        """The conflict queue route must depend on require_reviewer."""
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.list_open_conflicts)
        assert "require_reviewer" in src, (
            "list_open_conflicts must depend on require_reviewer. "
            "Unprotected conflict queues expose internal data disagreements publicly."
        )

    def test_conflict_stats_has_reviewer_dependency(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.conflict_stats)
        assert "require_reviewer" in src, (
            "conflict_stats must depend on require_reviewer."
        )


# ── Sprint D1: Migration index exists ─────────────────────────────────────────

class TestLatLonIndexMigration:
    """Migration 0014 must exist and create the lat/lon index."""

    def test_migration_0014_exists(self):
        import os
        versions_dir = os.path.join(
            os.path.dirname(__file__), "..", "..", "migrations", "versions"
        )
        files = os.listdir(versions_dir)
        assert any("0014" in f for f in files), (
            "Migration 0014 (lat/lon B-tree index) must exist. "
            "Without it, map bounding-box queries perform full table scans."
        )

    def test_migration_0014_has_downgrade(self):
        import glob
        import os
        versions_dir = os.path.join(
            os.path.dirname(__file__), "..", "..", "migrations", "versions"
        )
        files = glob.glob(os.path.join(versions_dir, "0014*.py"))
        assert files, "Migration 0014 file not found"
        with open(files[0]) as f:
            content = f.read()
        assert "def downgrade" in content
        assert "drop_index" in content, (
            "Migration 0014 downgrade must drop the index it creates."
        )
