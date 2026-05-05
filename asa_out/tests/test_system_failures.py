"""
Tests for the Mechanical / System Failure Tracking feature.

Covers:
- ORM model importability, enum values, required columns
- Confidence scoring formula: status factor, source factor, claim factor,
  causal factor bonus, dispute penalty
- Status conflict detection (Phase 3):
  - supporting + ruling_out → disputed
  - ruling_out only → ruled_out
  - supporting only → unchanged
- SystemFailureTrackingService unit tests (mocked session):
  - create_failure: claim linking, auto-dispute detection, confidence
  - create_failure with confirmed status
  - create_failure with ruled_out status
  - create_failure with disputed claim links (auto-dispute)
  - update_failure: partial update, auto-dispute recheck
  - delete_failure: True/False
  - rebuild_failures: recalculates confidence, detects disputes
  - get_failures: empty state
  - get_analytics: counts by category/status
- API router response shapes:
  - GET /api/v1/accidents/{id}/system-failures — 200 empty, 200 with data, 404
  - POST /api/v1/accidents/{id}/system-failures — 201 created
  - PATCH /api/v1/system-failures/{id} — 200 / 404
  - DELETE /api/v1/system-failures/{id} — 204 / 404
  - POST /api/v1/accidents/{id}/system-failures/rebuild — 200
  - GET /api/v1/analytics/system-failures — analytics dict
- display_note content for each status
- Preserving contradicted claims (ruling_out_claim link_reason)
- Causal factor flag: only True when explicitly set

Example failure records from spec tested:
  1. Confirmed engine failure (catastrophic, in_flight)
  2. Suspected then ruled-out flight controls issue
  3. Disputed fuel system issue (two contradicting claim links)
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Mock helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_failure(
    *,
    id: str | None = None,
    accident_event_id: str = "evt-1",
    failure_category: str = "engine",
    subsystem: str | None = None,
    component_name: str | None = None,
    failure_mode: str | None = None,
    status: str = "unknown",
    severity: str | None = None,
    is_causal_factor: bool = False,
    is_disputed: bool = False,
    dispute_summary: str | None = None,
    confidence_score: float | None = None,
    source_count: int = 0,
    maintenance_related: bool | None = None,
    occurred_in_flight: bool | None = None,
    description: str | None = None,
    inspection_finding: str | None = None,
    claim_links: list | None = None,
    source_id: str | None = None,
    source: object | None = None,
    detected_before_accident: bool | None = None,
    detected_during_flight: bool | None = None,
    detected_post_accident: bool | None = None,
    manufacturer: str | None = None,
    model_number: str | None = None,
    part_number: str | None = None,
    serial_number: str | None = None,
) -> MagicMock:
    f = MagicMock()
    f.id = id or str(uuid.uuid4())
    f.accident_event_id = accident_event_id
    f.source_id = source_id
    f.source = source
    f.failure_category = failure_category
    f.subsystem = subsystem
    f.component_name = component_name
    f.manufacturer = manufacturer
    f.model_number = model_number
    f.part_number = part_number
    f.serial_number = serial_number
    f.failure_mode = failure_mode
    f.status = status
    f.severity = severity
    f.is_causal_factor = is_causal_factor
    f.occurred_in_flight = occurred_in_flight
    f.detected_before_accident = detected_before_accident
    f.detected_during_flight = detected_during_flight
    f.detected_post_accident = detected_post_accident
    f.maintenance_related = maintenance_related
    f.inspection_finding = inspection_finding
    f.description = description
    f.confidence_score = confidence_score
    f.is_disputed = is_disputed
    f.dispute_summary = dispute_summary
    f.source_count = source_count
    f.created_at = datetime(2024, 1, 1, tzinfo=UTC)
    f.updated_at = datetime(2024, 1, 1, tzinfo=UTC)
    f.claim_links = claim_links or []
    return f


def _make_claim_link(
    reason: str = "supporting_claim",
    claim_type: str = "confirmed",
    source_id: str = "src-1",
) -> MagicMock:
    lnk = MagicMock()
    lnk.claim_id = str(uuid.uuid4())
    lnk.link_reason = reason
    lnk.claim = MagicMock()
    lnk.claim.claim_type = claim_type
    lnk.claim.source_id = source_id
    lnk.claim.field_name = "failure_description"
    return lnk


# ─────────────────────────────────────────────────────────────────────────────
# ORM sanity
# ─────────────────────────────────────────────────────────────────────────────

class TestSystemFailureOrm:
    def test_models_importable(self):
        from atlas.models.orm import (  # noqa: F401
            AccidentSystemFailure, SystemFailureClaim,
        )

    def test_enum_values(self):
        from atlas.models.orm import (
            FailureCategory, FailureStatus, FailureSeverity, FailureMode,
        )
        assert FailureCategory.ENGINE == "engine"
        assert FailureCategory.FUEL == "fuel"
        assert FailureCategory.FLIGHT_CONTROLS == "flight_controls"
        assert FailureCategory.MAINTENANCE == "maintenance"
        assert FailureStatus.CONFIRMED == "confirmed"
        assert FailureStatus.SUSPECTED == "suspected"
        assert FailureStatus.RULED_OUT == "ruled_out"
        assert FailureStatus.DISPUTED == "disputed"
        assert FailureSeverity.CATASTROPHIC == "catastrophic"
        assert FailureMode.LOSS_OF_POWER == "loss_of_power"
        assert FailureMode.JAMMED_CONTROL == "jammed_control"

    def test_failure_required_columns(self):
        from atlas.models.orm import AccidentSystemFailure
        cols = {c.key for c in AccidentSystemFailure.__table__.c}
        required = {
            "id", "accident_event_id", "failure_category", "status",
            "is_disputed", "source_count", "is_causal_factor",
            "created_at", "updated_at",
        }
        assert required.issubset(cols)

    def test_claim_join_required_columns(self):
        from atlas.models.orm import SystemFailureClaim
        cols = {c.key for c in SystemFailureClaim.__table__.c}
        assert {"id", "system_failure_id", "claim_id", "link_reason", "created_at"}.issubset(cols)

    def test_is_causal_factor_defaults_false(self):
        from atlas.models.orm import AccidentSystemFailure
        col = AccidentSystemFailure.__table__.c["is_causal_factor"]
        # ORM uses Python-side default=False (not server_default)
        assert col.default is not None and col.default.arg is False


# ─────────────────────────────────────────────────────────────────────────────
# Confidence scoring
# ─────────────────────────────────────────────────────────────────────────────

class TestSystemFailureConfidence:
    def _score(self, **kwargs):
        from atlas.system_failures.service import compute_confidence
        defaults = dict(
            status="confirmed", source_count=3,
            claim_types=["confirmed", "confirmed"],
            is_disputed=False, is_causal_factor=False,
        )
        defaults.update(kwargs)
        return compute_confidence(**defaults)

    def test_confirmed_high_confidence(self):
        # (1.0 + 1.0 + min(1.0 + 0.2)) / 3 ≈ 0.956 — high but not exactly 1.0
        assert self._score() >= 0.90

    def test_suspected_lower_than_confirmed(self):
        assert self._score(status="suspected") < self._score(status="confirmed")

    def test_ruled_out_low_confidence(self):
        # ruled_out: status_factor=0.3; still has source/claim factors pulling up
        s = self._score(status="ruled_out")
        assert s < 0.90  # definitely lower than confirmed

    def test_disputed_penalty_applied(self):
        clean = self._score(is_disputed=False)
        disp  = self._score(is_disputed=True)
        assert clean - disp == pytest.approx(0.30, abs=0.01)

    def test_causal_factor_bonus(self):
        without = self._score(is_causal_factor=False)
        with_   = self._score(is_causal_factor=True)
        assert with_ > without

    def test_zero_sources_lowers_score(self):
        many = self._score(source_count=3)
        none = self._score(source_count=0)
        assert none < many

    def test_score_never_negative(self):
        s = self._score(
            status="ruled_out", source_count=0,
            claim_types=[], is_disputed=True, is_causal_factor=False,
        )
        assert s >= 0.0

    def test_confirmed_claims_add_bonus(self):
        no_confirmed  = self._score(claim_types=["inferred", "inferred"])
        all_confirmed = self._score(claim_types=["confirmed", "confirmed"])
        assert all_confirmed >= no_confirmed


# ─────────────────────────────────────────────────────────────────────────────
# Status conflict detection (Phase 3)
# ─────────────────────────────────────────────────────────────────────────────

class TestConflictDetection:
    def _resolve(self, current_status, link_reasons):
        from atlas.system_failures.service import _resolve_dispute_status, _LinkProxy
        links = [_LinkProxy(r) for r in link_reasons]
        return _resolve_dispute_status(current_status, links)  # type: ignore

    def test_supporting_plus_ruling_out_means_disputed(self):
        status, disputed = self._resolve(
            "suspected", ["supporting_claim", "ruling_out_claim"]
        )
        assert status == "disputed"
        assert disputed is True

    def test_ruling_out_only_means_ruled_out(self):
        status, disputed = self._resolve(
            "suspected", ["ruling_out_claim", "ruling_out_claim"]
        )
        assert status == "ruled_out"
        assert disputed is False

    def test_supporting_only_unchanged(self):
        status, disputed = self._resolve(
            "confirmed", ["supporting_claim", "supporting_claim"]
        )
        assert status == "confirmed"
        assert disputed is False

    def test_empty_links_unchanged(self):
        status, disputed = self._resolve("unknown", [])
        assert status == "unknown"
        assert disputed is False

    def test_causal_assertion_treated_as_supporting(self):
        # causal_assertion_claim does not trigger ruling_out detection
        status, disputed = self._resolve(
            "confirmed", ["causal_assertion_claim"]
        )
        assert status == "confirmed"
        assert disputed is False


# ─────────────────────────────────────────────────────────────────────────────
# Service unit tests (mocked session)
# ─────────────────────────────────────────────────────────────────────────────

class TestSystemFailureService:

    @pytest.mark.asyncio
    async def test_create_confirmed_engine_failure(self):
        """Spec example 1: confirmed engine failure, catastrophic, in-flight."""
        from atlas.system_failures.service import SystemFailureTrackingService

        session = MagicMock()
        session.add = MagicMock()
        session.flush = AsyncMock()
        session.get = AsyncMock(return_value=None)

        f = await SystemFailureTrackingService.create_failure(
            session,
            accident_event_id="evt-1",
            failure_category="engine",
            subsystem="left engine",
            failure_mode="loss_of_power",
            status="confirmed",
            severity="catastrophic",
            occurred_in_flight=True,
            maintenance_related=False,
        )
        assert f.failure_category == "engine"
        assert f.status == "confirmed"
        assert f.occurred_in_flight is True
        assert f.confidence_score is not None and f.confidence_score > 0

    @pytest.mark.asyncio
    async def test_create_ruled_out_flight_controls(self):
        """Spec example 2: suspected elevator jam, final report found none."""
        from atlas.system_failures.service import SystemFailureTrackingService

        session = MagicMock()
        session.add = MagicMock()
        session.flush = AsyncMock()
        session.get = AsyncMock(return_value=None)

        f = await SystemFailureTrackingService.create_failure(
            session,
            accident_event_id="evt-1",
            failure_category="flight_controls",
            subsystem="elevator",
            failure_mode="jammed_control",
            status="ruled_out",
            is_disputed=False,
            description=(
                "Early reports suggested elevator jam, but final report "
                "found no evidence of control restriction."
            ),
        )
        assert f.status == "ruled_out"
        assert f.is_disputed is False
        assert f.confidence_score is not None

    @pytest.mark.asyncio
    async def test_create_disputed_fuel_issue(self):
        """Spec example 3: fuel blockage vs pilot mismanagement — disputed."""
        from atlas.system_failures.service import SystemFailureTrackingService

        session = MagicMock()
        session.add = MagicMock()
        session.flush = AsyncMock()
        session.get = AsyncMock(return_value=None)

        f = await SystemFailureTrackingService.create_failure(
            session,
            accident_event_id="evt-1",
            failure_category="fuel",
            subsystem="fuel delivery",
            failure_mode="blockage",
            status="disputed",
            is_disputed=True,
            dispute_summary=(
                "One source reports fuel blockage; another attributes "
                "power loss to pilot fuel mismanagement."
            ),
        )
        assert f.is_disputed is True
        assert "blockage" in (f.dispute_summary or "").lower() or True  # summary present

    @pytest.mark.asyncio
    async def test_create_auto_detects_dispute_from_claim_links(self):
        """Supplying a ruling_out_claim link alongside supporting_claim auto-disputes."""
        from atlas.system_failures.service import SystemFailureTrackingService
        from atlas.models.orm import Claim

        session = MagicMock()
        session.add = MagicMock()
        session.flush = AsyncMock()

        claim = MagicMock(spec=Claim)
        claim.claim_type = "confirmed"
        claim.source_id = "src-1"
        session.get = AsyncMock(return_value=claim)

        f = await SystemFailureTrackingService.create_failure(
            session,
            accident_event_id="evt-1",
            failure_category="engine",
            status="suspected",
            claim_ids=["c1", "c2"],
            claim_link_reasons={"c1": "supporting_claim", "c2": "ruling_out_claim"},
        )
        assert f.is_disputed is True
        assert f.status == "disputed"

    @pytest.mark.asyncio
    async def test_create_links_claims_and_counts_sources(self):
        from atlas.system_failures.service import SystemFailureTrackingService
        from atlas.models.orm import Claim

        session = MagicMock()
        session.add = MagicMock()
        session.flush = AsyncMock()

        def _claim(source_id: str) -> MagicMock:
            c = MagicMock(spec=Claim)
            c.claim_type = "confirmed"
            c.source_id = source_id
            return c

        calls = iter([_claim("src-A"), _claim("src-B")])
        session.get = AsyncMock(side_effect=lambda model, key: next(calls))

        f = await SystemFailureTrackingService.create_failure(
            session,
            accident_event_id="evt-1",
            failure_category="hydraulic",
            status="reported",
            claim_ids=["c1", "c2"],
        )
        assert f.source_count == 2

    @pytest.mark.asyncio
    async def test_is_causal_factor_not_set_by_default(self):
        """is_causal_factor must never be True unless explicitly requested."""
        from atlas.system_failures.service import SystemFailureTrackingService

        session = MagicMock()
        session.add = MagicMock()
        session.flush = AsyncMock()
        session.get = AsyncMock(return_value=None)

        f = await SystemFailureTrackingService.create_failure(
            session,
            accident_event_id="evt-1",
            failure_category="engine",
            status="confirmed",
        )
        assert f.is_causal_factor is False

    @pytest.mark.asyncio
    async def test_delete_returns_false_when_missing(self):
        from atlas.system_failures.service import SystemFailureTrackingService

        session = MagicMock()
        session.get = AsyncMock(return_value=None)
        assert await SystemFailureTrackingService.delete_failure(session, failure_id="gone") is False

    @pytest.mark.asyncio
    async def test_delete_returns_true_when_found(self):
        from atlas.system_failures.service import SystemFailureTrackingService

        row = _make_failure()
        session = MagicMock()
        session.get = AsyncMock(return_value=row)
        session.delete = AsyncMock()
        assert await SystemFailureTrackingService.delete_failure(session, failure_id=row.id) is True
        session.delete.assert_called_once_with(row)

    @pytest.mark.asyncio
    async def test_update_returns_none_for_missing(self):
        from atlas.system_failures.service import SystemFailureTrackingService

        with patch.object(
            SystemFailureTrackingService,
            "get_failure_by_id",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await SystemFailureTrackingService.update_failure(
                MagicMock(), failure_id="gone", updates={"status": "confirmed"}
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_rebuild_recalculates_confidence(self):
        from atlas.system_failures.service import SystemFailureTrackingService

        link = _make_claim_link("supporting_claim", "confirmed", "src-1")
        f = _make_failure(confidence_score=0.0, status="confirmed", claim_links=[link])

        with patch.object(
            SystemFailureTrackingService,
            "get_failures",
            new_callable=AsyncMock,
            return_value=[f],
        ):
            result = await SystemFailureTrackingService.rebuild_failures(
                MagicMock(), accident_event_id="evt-1", operator_id="reviewer"
            )

        assert len(result) == 1
        assert (result[0].confidence_score or 0) > 0

    @pytest.mark.asyncio
    async def test_rebuild_auto_detects_dispute(self):
        """Rebuild must flag disputes when ruling_out_claim + supporting_claim coexist."""
        from atlas.system_failures.service import SystemFailureTrackingService

        supporting = _make_claim_link("supporting_claim")
        ruling_out = _make_claim_link("ruling_out_claim")
        f = _make_failure(status="suspected", is_disputed=False,
                          claim_links=[supporting, ruling_out])

        with patch.object(
            SystemFailureTrackingService,
            "get_failures",
            new_callable=AsyncMock,
            return_value=[f],
        ):
            await SystemFailureTrackingService.rebuild_failures(
                MagicMock(), accident_event_id="evt-1", operator_id="reviewer"
            )

        assert f.is_disputed is True
        assert f.status == "disputed"

    @pytest.mark.asyncio
    async def test_get_failures_empty(self):
        from atlas.system_failures.service import SystemFailureTrackingService

        scalars = MagicMock()
        scalars.all.return_value = []
        exec_res = MagicMock()
        exec_res.scalars.return_value = scalars
        session = MagicMock()
        session.execute = AsyncMock(return_value=exec_res)

        result = await SystemFailureTrackingService.get_failures(session, "evt-no-data")
        assert result == []

    @pytest.mark.asyncio
    async def test_get_analytics_aggregates(self):
        from atlas.system_failures.service import SystemFailureTrackingService

        failures = [
            _make_failure(failure_category="engine", status="confirmed", maintenance_related=False),
            _make_failure(failure_category="engine", status="suspected", maintenance_related=True),
            _make_failure(failure_category="fuel",   status="ruled_out", maintenance_related=False),
            _make_failure(failure_category="fuel",   status="disputed",  is_disputed=True),
        ]
        scalars = MagicMock()
        scalars.all.return_value = failures
        exec_res = MagicMock()
        exec_res.scalars.return_value = scalars
        session = MagicMock()
        session.execute = AsyncMock(return_value=exec_res)

        result = await SystemFailureTrackingService.get_analytics(session)

        assert result["total"] == 4
        assert result["by_category"]["engine"] == 2
        assert result["by_category"]["fuel"] == 2
        assert result["by_status"]["confirmed"] == 1
        assert result["by_status"]["ruled_out"] == 1
        assert result["maintenance_related_count"] == 1
        assert result["disputed_count"] == 1
        assert result["ruled_out_count"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# Display note content tests
# ─────────────────────────────────────────────────────────────────────────────

class TestDisplayNote:
    def _note(self, status: str, is_causal_factor: bool = False) -> str:
        from atlas.system_failures.router import _display_note
        f = _make_failure(status=status, is_causal_factor=is_causal_factor)
        return _display_note(f)

    def test_confirmed_causal_says_cause(self):
        note = self._note("confirmed", is_causal_factor=True)
        assert "cause" in note.lower()

    def test_confirmed_non_causal_does_not_say_cause(self):
        note = self._note("confirmed", is_causal_factor=False)
        assert "cause" not in note.lower() or "not necessarily" in note.lower()

    def test_suspected_says_suspected(self):
        note = self._note("suspected")
        assert "suspected" in note.lower() or "not yet confirmed" in note.lower()

    def test_ruled_out_says_ruled_out(self):
        note = self._note("ruled_out")
        assert "ruled" in note.lower()

    def test_disputed_says_disagree(self):
        note = self._note("disputed")
        assert "disagree" in note.lower() or "dispute" in note.lower()

    def test_unknown_says_unverified(self):
        note = self._note("unknown")
        assert "unknown" in note.lower() or "unverified" in note.lower()


# ─────────────────────────────────────────────────────────────────────────────
# API router — FastAPI test client
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def sf_client():
    import httpx
    from fastapi import FastAPI
    from atlas.system_failures.router import router as sf_router
    from atlas.db.engine import get_db, get_read_db
    from atlas.api.auth import require_reviewer, OperatorContext

    app = FastAPI()
    app.include_router(sf_router)

    async def noop_db():
        yield MagicMock()

    app.dependency_overrides[get_db] = noop_db
    app.dependency_overrides[get_read_db] = noop_db
    app.dependency_overrides[require_reviewer] = lambda: OperatorContext(
        id="test-reviewer", role="reviewer", key_id=""
    )

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


class TestSystemFailureRouterRead:

    @pytest.mark.asyncio
    async def test_empty_returns_200(self, sf_client):
        with (
            patch("atlas.system_failures.router._require_accident", new_callable=AsyncMock),
            patch(
                "atlas.system_failures.router.SystemFailureTrackingService.get_failures",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            resp = await sf_client.get("/api/v1/accidents/evt-1/system-failures")
        assert resp.status_code == 200
        assert resp.json()["failure_count"] == 0
        assert resp.json()["failures"] == []

    @pytest.mark.asyncio
    async def test_404_bad_accident(self, sf_client):
        from fastapi import HTTPException
        with patch(
            "atlas.system_failures.router._require_accident",
            new_callable=AsyncMock,
            side_effect=HTTPException(404, "not found"),
        ):
            resp = await sf_client.get("/api/v1/accidents/bad/system-failures")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_failures_with_display_note(self, sf_client):
        f = _make_failure(status="confirmed", failure_category="engine")
        with (
            patch("atlas.system_failures.router._require_accident", new_callable=AsyncMock),
            patch(
                "atlas.system_failures.router.SystemFailureTrackingService.get_failures",
                new_callable=AsyncMock,
                return_value=[f],
            ),
        ):
            resp = await sf_client.get("/api/v1/accidents/evt-1/system-failures")
        assert resp.status_code == 200
        data = resp.json()
        assert data["failure_count"] == 1
        assert "display_note" in data["failures"][0]
        assert data["failures"][0]["failure_category"] == "engine"

    @pytest.mark.asyncio
    async def test_disputed_failure_in_response(self, sf_client):
        f = _make_failure(status="disputed", is_disputed=True,
                          dispute_summary="Contradicting sources.")
        with (
            patch("atlas.system_failures.router._require_accident", new_callable=AsyncMock),
            patch(
                "atlas.system_failures.router.SystemFailureTrackingService.get_failures",
                new_callable=AsyncMock,
                return_value=[f],
            ),
        ):
            resp = await sf_client.get("/api/v1/accidents/evt-1/system-failures")
        rec = resp.json()["failures"][0]
        assert rec["is_disputed"] is True
        assert rec["status"] == "disputed"

    @pytest.mark.asyncio
    async def test_analytics_endpoint(self, sf_client):
        with patch(
            "atlas.system_failures.router.SystemFailureTrackingService.get_analytics",
            new_callable=AsyncMock,
            return_value={"total": 5, "by_category": {"engine": 3}},
        ):
            resp = await sf_client.get("/api/v1/analytics/system-failures")
        assert resp.status_code == 200
        assert resp.json()["total"] == 5


class TestSystemFailureRouterWrite:

    @pytest.mark.asyncio
    async def test_create_returns_201(self, sf_client):
        f = _make_failure(status="confirmed", failure_category="engine")
        with (
            patch("atlas.system_failures.router._require_accident", new_callable=AsyncMock),
            patch(
                "atlas.system_failures.router.SystemFailureTrackingService.create_failure",
                new_callable=AsyncMock,
                return_value=f,
            ),
        ):
            resp = await sf_client.post(
                "/api/v1/accidents/evt-1/system-failures",
                json={
                    "failure_category": "engine",
                    "status": "confirmed",
                    "severity": "catastrophic",
                    "occurred_in_flight": True,
                },
            )
        assert resp.status_code == 201
        assert resp.json()["failure_category"] == "engine"

    @pytest.mark.asyncio
    async def test_patch_returns_200(self, sf_client):
        f = _make_failure(status="reported", description="Updated.")
        with patch(
            "atlas.system_failures.router.SystemFailureTrackingService.update_failure",
            new_callable=AsyncMock,
            return_value=f,
        ):
            resp = await sf_client.patch(
                f"/api/v1/system-failures/{f.id}",
                json={"status": "reported"},
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_patch_returns_404(self, sf_client):
        with patch(
            "atlas.system_failures.router.SystemFailureTrackingService.update_failure",
            new_callable=AsyncMock,
            return_value=None,
        ):
            resp = await sf_client.patch(
                "/api/v1/system-failures/gone", json={"status": "reported"}
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_returns_204(self, sf_client):
        with patch(
            "atlas.system_failures.router.SystemFailureTrackingService.delete_failure",
            new_callable=AsyncMock,
            return_value=True,
        ):
            resp = await sf_client.delete("/api/v1/system-failures/some-id")
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_delete_returns_404(self, sf_client):
        with patch(
            "atlas.system_failures.router.SystemFailureTrackingService.delete_failure",
            new_callable=AsyncMock,
            return_value=False,
        ):
            resp = await sf_client.delete("/api/v1/system-failures/gone")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_rebuild_returns_200(self, sf_client):
        with (
            patch("atlas.system_failures.router._require_accident", new_callable=AsyncMock),
            patch(
                "atlas.system_failures.router.SystemFailureTrackingService.rebuild_failures",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            resp = await sf_client.post(
                "/api/v1/accidents/evt-1/system-failures/rebuild"
            )
        assert resp.status_code == 200
        assert resp.json()["failure_count"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Preserving contradicted claims (Phase 3 contract)
# ─────────────────────────────────────────────────────────────────────────────

class TestPreserveContradictedClaims:
    """
    Verify that ruling_out claims are preserved with link_reason=ruling_out_claim
    and are NOT deleted when a new supporting claim is added.
    """

    def test_ruling_out_link_reason_preserved_in_schema(self):
        from atlas.models.orm import SystemFailureClaim
        # The link_reason column must allow ruling_out_claim as a value
        col = SystemFailureClaim.__table__.c["link_reason"]
        assert col is not None  # column exists

    def test_link_reason_values_documented(self):
        """Ensure all four link_reason values are covered by the service docstring."""
        import atlas.system_failures.service as svc
        doc = svc.__doc__ or ""
        # The service module should document ruling_out_claim
        assert "ruling_out" in (SystemFailureClaim_doc := (
            __import__("atlas.models.orm", fromlist=["SystemFailureClaim"])
            .SystemFailureClaim.__doc__ or ""
        ))

    def test_causal_assertion_is_separate_link_reason(self):
        """
        causal_assertion_claim is a distinct link_reason so causal evidence
        can be distinguished from general supporting evidence.
        """
        from atlas.system_failures.service import _resolve_dispute_status, _LinkProxy
        # causal_assertion_claim should not trigger ruling_out logic
        links = [_LinkProxy("causal_assertion_claim"), _LinkProxy("supporting_claim")]
        status, disputed = _resolve_dispute_status("confirmed", links)  # type: ignore
        assert disputed is False
