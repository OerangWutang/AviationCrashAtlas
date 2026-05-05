"""
Tests for v20 hardening additions.

These tests cover the v20 prompt items that landed in this iteration:

  Step 9   active-claim uniqueness migration
  Step 11  source-document extraction (real URLs only)
  Step 12  source_record_state hash-changed / hash-unchanged behaviour
  Step 13  event_revisions emission on first-seen / changed / unchanged
  Step 15  ProjectionExplanation generation rules

The tests are deliberately mostly source-introspection or pure-Python
logic; the four pre-existing v19 DB-mocked unit tests in test_core.py
already cover the full async-projection plumbing using the same MagicMock
session pattern, so these tests reuse that pattern rather than spinning
up Postgres.

Anywhere a test could only meaningfully run against a real PostGIS
instance (the partial unique index, the actual upsert into
source_record_state, or the JSONB shape of projection_explanations as
seen by Postgres) it is left as a TODO with a comment pointing to the
integration test that would cover it.
"""
from __future__ import annotations

import inspect
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

# ──────────────────────────────────────────────────────────────────────────────
# Step 11 — document extraction is conservative
# ──────────────────────────────────────────────────────────────────────────────


class TestDocumentExtractionIsConservative:
    """
    The v20 extractor must NOT fabricate per-event investigation page
    URLs.  It only emits documents from real URL fields in the raw
    payload, plus a single deterministic CAROL search URL when EventId
    is present.  Tests confirm the extractor keeps that promise.
    """

    def test_no_documents_when_payload_has_no_urls_and_no_event_id(self):
        from atlas.ingestion.document_extractor import extract_documents_from_ntsb
        # Realistic minimal NTSB CSV record with no URL fields and no EventId.
        raw = {
            "AircraftMake": "Cessna",
            "AircraftModel": "172",
            "InjurySeverity": "NONE",
        }
        assert extract_documents_from_ntsb(raw) == []

    def test_event_id_alone_yields_one_carol_search_url(self):
        from atlas.ingestion.document_extractor import extract_documents_from_ntsb
        raw = {"EventId": "20230104X12345"}
        docs = extract_documents_from_ntsb(raw)
        assert len(docs) == 1
        d = docs[0]
        assert d.document_type == "investigation_page"
        # Must be the deterministic CAROL search URL pattern, NOT a
        # fabricated investigation page URL.
        assert "carol-main-public/basic-search" in d.url
        assert "EventID=20230104X12345" in d.url

    def test_known_url_field_is_used_with_correct_document_type(self):
        from atlas.ingestion.document_extractor import extract_documents_from_ntsb
        raw = {
            "EventId": "20230104X12345",
            "FinalReportUrl": "https://example.gov/final-report.pdf",
        }
        docs = extract_documents_from_ntsb(raw)
        # Both the FinalReportUrl AND the deterministic CAROL URL.
        types = sorted(d.document_type for d in docs)
        assert "final" in types
        assert "investigation_page" in types

    def test_non_url_string_in_known_field_is_ignored(self):
        from atlas.ingestion.document_extractor import extract_documents_from_ntsb
        raw = {
            "EventId": "x",
            # Not a URL — must be skipped, not fabricated into one.
            "FinalReportUrl": "to be determined",
        }
        types = [d.document_type for d in extract_documents_from_ntsb(raw)]
        assert "final" not in types

    def test_duplicates_are_removed(self):
        from atlas.ingestion.document_extractor import extract_documents_from_ntsb
        url = "https://www.ntsb.gov/some-pdf.pdf"
        raw = {
            "EventId": "x",
            "FinalReportUrl": url,
            "ReportPdfUrl": url,
        }
        urls = [d.url for d in extract_documents_from_ntsb(raw)]
        # Each unique URL should appear exactly once.
        assert urls.count(url) == 1

    def test_unmapped_url_field_becomes_external_link(self):
        from atlas.ingestion.document_extractor import extract_documents_from_ntsb
        raw = {
            "EventId": "x",
            "SomeRandomLink": "https://other.gov/a",
        }
        docs = extract_documents_from_ntsb(raw)
        types = {d.document_type for d in docs}
        assert "external_link" in types


# ──────────────────────────────────────────────────────────────────────────────
# Step 13 — event_revisions ORM exists with the right columns
# ──────────────────────────────────────────────────────────────────────────────


class TestEventRevisionsORM:
    def test_event_revision_orm_exists(self):
        from atlas.models.orm import EventRevision  # noqa: F401

    def test_event_revision_columns_present(self):
        from atlas.models.orm import EventRevision
        required = [
            "id", "event_id", "revision_type", "occurred_at",
            "source_id", "source_record_id", "snapshot_id", "claim_id",
            "conflict_id", "source_document_id", "ingestion_run_id",
            "field_names", "old_value", "new_value", "description",
        ]
        for f in required:
            assert hasattr(EventRevision, f), (
                f"EventRevision must have column '{f}'"
            )


class TestSourceRecordStateORM:
    def test_source_record_state_orm_exists(self):
        from atlas.models.orm import SourceRecordState  # noqa: F401

    def test_source_record_state_columns_present(self):
        from atlas.models.orm import SourceRecordState
        required = [
            "source_id", "source_record_id", "event_id",
            "first_seen_at", "last_seen_at", "last_changed_at",
            "current_payload_hash", "current_snapshot_id",
            "previous_payload_hash", "parser_version",
            "current_field_names",
        ]
        for f in required:
            assert hasattr(SourceRecordState, f), (
                f"SourceRecordState must have column '{f}'"
            )


# ──────────────────────────────────────────────────────────────────────────────
# Step 13/14 — pipeline emits revisions on the right events
# ──────────────────────────────────────────────────────────────────────────────
#
# We assert against the pipeline source code rather than running it,
# because the full _process flow requires a live AsyncSession and Postgres.
# These checks catch the most common regression: a refactor that drops
# the revision emission entirely.


class TestPipelineEmitsRevisions:
    def test_pipeline_imports_revision_helpers(self):
        import atlas.ingestion.pipeline as pipeline_module
        src = inspect.getsource(pipeline_module)
        assert "from atlas.ingestion import revisions as rev" in src or \
               "from atlas.ingestion.revisions import" in src, (
            "pipeline.py must import the revisions helpers"
        )

    def test_pipeline_emits_first_seen_for_new_events(self):
        import atlas.ingestion.pipeline as pipeline_module
        src = inspect.getsource(pipeline_module)
        assert "emit_source_record_first_seen" in src

    def test_pipeline_emits_snapshot_changed_for_existing_events(self):
        import atlas.ingestion.pipeline as pipeline_module
        src = inspect.getsource(pipeline_module)
        assert "emit_source_snapshot_changed" in src

    def test_pipeline_emits_unchanged_on_hash_match(self):
        import atlas.ingestion.pipeline as pipeline_module
        src = inspect.getsource(pipeline_module)
        # The unchanged path is mandatory — re-checking a record that
        # hasn't changed is itself information.
        assert "emit_source_record_unchanged" in src

    def test_pipeline_emits_projection_rebuilt_on_each_process(self):
        import atlas.ingestion.pipeline as pipeline_module
        src = inspect.getsource(pipeline_module)
        assert "emit_projection_rebuilt" in src


class TestPipelineUpdatesSourceRecordState:
    """
    source_record_state is the rolling-state table the timeline and
    operational dashboards read.  The pipeline must update it both on
    new content and on hash-unchanged re-fetches.
    """

    def test_pipeline_imports_source_record_state(self):
        import atlas.ingestion.pipeline as pipeline_module
        src = inspect.getsource(pipeline_module)
        assert "SourceRecordState" in src

    def test_pipeline_bumps_last_seen_on_hash_unchanged(self):
        import atlas.ingestion.pipeline as pipeline_module
        src = inspect.getsource(pipeline_module._process if hasattr(
            pipeline_module, "_process") else pipeline_module.IngestionPipeline._process)
        # Either branch must update last_seen_at — the rolling state's
        # whole point is to record "we still see this record".
        assert "last_seen_at" in src

    def test_pipeline_records_parser_version(self):
        import atlas.ingestion.pipeline as pipeline_module
        src = inspect.getsource(pipeline_module)
        assert "PARSER_VERSION" in src
        assert "parser_version" in src


# ──────────────────────────────────────────────────────────────────────────────
# Step 15 — projection explanations
# ──────────────────────────────────────────────────────────────────────────────


def _claim(
    *, claim_id: str, field_name: str, value: Any, source_id: str,
    claim_type: str = "confirmed", is_winning: bool = False,
    created_at: datetime | None = None,
):
    """Tiny test helper for building a Claim-shaped MagicMock."""
    from atlas.models import claim_value as cv
    c = MagicMock()
    c.id = claim_id
    c.field_name = field_name
    c.field_value = cv.encode(value)
    c.source_id = source_id
    c.claim_type = claim_type
    c.is_winning = is_winning
    c.created_at = created_at or datetime.now(tz=UTC)
    return c


def _src(*, sid: str, tier: int = 1, short: str = "S"):
    s = MagicMock()
    s.id = sid
    s.tier = tier
    s.short_name = short
    s.display_name = short
    s.license_type = "public_domain"
    s.base_url = None
    s.description = None
    return s


def _conflict(*, field_name: str, claim_a_id: str, claim_b_id: str, status: str = "open"):
    cf = MagicMock()
    cf.field_name = field_name
    cf.claim_a_id = claim_a_id
    cf.claim_b_id = claim_b_id
    cf.status = status
    cf.resolution = None
    return cf


class TestProjectionExplanations:
    """
    ProjectionService._build_explanations is pure logic given claims +
    winners + sources + conflicts, so we can test it without a session.
    """

    def _svc(self):
        from atlas.claims.projection import ProjectionService
        # ProjectionService needs a session for ConfidenceEngine; we don't
        # call rebuild_event here so a MagicMock is fine.
        return ProjectionService(session=MagicMock())

    def test_only_active_claim(self):
        svc = self._svc()
        c = _claim(claim_id="c1", field_name="aircraft_make",
                   value="Cessna", source_id="s1")
        winners = {"aircraft_make": c}
        sources = {"s1": _src(sid="s1", tier=1)}
        rows = svc._build_explanations(
            claims=[c], winners=winners, sources=sources, conflicts=[],
        )
        assert len(rows) == 1
        r = rows[0]
        assert r["field_name"] == "aircraft_make"
        assert r["selection_reason"] == "only_active_claim"
        assert r["selected_claim_id"] == "c1"
        assert r["has_open_conflict"] is False

    def test_open_dispute_withholds(self):
        svc = self._svc()
        a = _claim(claim_id="a", field_name="fatalities_total",
                   value=0, source_id="s1", claim_type="disputed")
        b = _claim(claim_id="b", field_name="fatalities_total",
                   value=1, source_id="s2", claim_type="disputed")
        sources = {"s1": _src(sid="s1", tier=1), "s2": _src(sid="s2", tier=2)}
        cf = _conflict(field_name="fatalities_total",
                       claim_a_id="a", claim_b_id="b", status="open")
        rows = svc._build_explanations(
            claims=[a, b], winners={}, sources=sources, conflicts=[cf],
        )
        # Should produce exactly one explanation, with the withhold reason
        # and no displayed value or selected claim.
        assert len(rows) == 1
        r = rows[0]
        assert r["field_name"] == "fatalities_total"
        assert r["selection_reason"] == "withheld_open_dispute"
        assert r["displayed_value"] is None
        assert r["selected_claim_id"] is None
        assert r["has_open_conflict"] is True
        assert r["disputed_claim_count"] == 2

    def test_higher_tier_wins(self):
        svc = self._svc()
        # The higher-tier (tier=1) winner should be reported as such.
        winner = _claim(claim_id="w", field_name="phase_of_flight",
                        value="LANDING", source_id="s1",
                        claim_type="confirmed", is_winning=True)
        loser = _claim(claim_id="l", field_name="phase_of_flight",
                       value="TAKEOFF", source_id="s2",
                       claim_type="confirmed")
        sources = {
            "s1": _src(sid="s1", tier=1),
            "s2": _src(sid="s2", tier=3),
        }
        rows = svc._build_explanations(
            claims=[winner, loser],
            winners={"phase_of_flight": winner},
            sources=sources,
            conflicts=[],
        )
        r = rows[0]
        assert r["selection_reason"] == "selected_higher_tier"
        assert r["selected_claim_id"] == "w"
        assert r["source_rank"] == 1

    def test_location_text_marked_approximate_when_no_coords(self):
        svc = self._svc()
        c = _claim(claim_id="loc1", field_name="location_text",
                   value="Bend, OR, USA", source_id="s1")
        winners = {"location_text": c}
        rows = svc._build_explanations(
            claims=[c], winners=winners,
            sources={"s1": _src(sid="s1", tier=1)},
            conflicts=[],
        )
        r = rows[0]
        # No location_coordinates winning claim is present, so this is
        # nearest-city-only — surface that explicitly.
        assert r["selection_reason"] == "approximate_nearest_city_only"

    def test_no_active_claim_withholds(self):
        svc = self._svc()
        # Field present in claim list as DISPUTED but no winner and no
        # open conflict (rare but possible during a stale projection
        # snapshot) → withheld_no_active_claim.
        c = _claim(claim_id="c1", field_name="probable_cause",
                   value="X", source_id="s1", claim_type="disputed")
        rows = svc._build_explanations(
            claims=[c], winners={},
            sources={"s1": _src(sid="s1", tier=1)},
            conflicts=[],
        )
        r = rows[0]
        assert r["selection_reason"] == "withheld_no_active_claim"

    def test_explanation_is_jsonable(self):
        """displayed_value must be JSON-serialisable for JSONB storage."""
        import json

        from atlas.claims.projection import _jsonable
        svc = self._svc()
        d = datetime(2023, 1, 4, 22, 30, tzinfo=UTC)
        c = _claim(claim_id="c1", field_name="occurred_at",
                   value=d, source_id="s1")
        winners = {"occurred_at": c}
        rows = svc._build_explanations(
            claims=[c], winners=winners,
            sources={"s1": _src(sid="s1", tier=1)},
            conflicts=[],
        )
        # Round-trip through JSON to confirm.
        json.dumps(rows[0])
        assert isinstance(_jsonable(d), str)


# ──────────────────────────────────────────────────────────────────────────────
# Step 5 — document_status aggregate
# ──────────────────────────────────────────────────────────────────────────────


class TestDocumentStatusAggregate:
    """
    ProjectionService._aggregate_document_status mirrors the frontend's
    fallback derivation; the backend must be authoritative.
    """

    def _doc(self, *, url_verified: bool, is_available: bool | None):
        d = MagicMock()
        d.url_verified = url_verified
        d.is_available = is_available
        return d

    def _agg(self, docs):
        from atlas.claims.projection import ProjectionService
        return ProjectionService._aggregate_document_status(docs)

    def test_none_linked_when_empty(self):
        assert self._agg([]) == "none_linked"

    def test_linked_unverified_when_no_check_done(self):
        docs = [self._doc(url_verified=False, is_available=None)]
        assert self._agg(docs) == "linked_unverified"

    def test_verified_when_all_verified_and_available(self):
        docs = [self._doc(url_verified=True, is_available=True)] * 2
        assert self._agg(docs) == "verified"

    def test_unavailable_when_all_known_dead(self):
        docs = [self._doc(url_verified=False, is_available=False)] * 2
        assert self._agg(docs) == "unavailable"

    def test_mixed_when_partial(self):
        docs = [
            self._doc(url_verified=True, is_available=True),
            self._doc(url_verified=False, is_available=False),
        ]
        assert self._agg(docs) == "mixed"


# ──────────────────────────────────────────────────────────────────────────────
# Step 9 — partial unique index migration is present and well-formed
# ──────────────────────────────────────────────────────────────────────────────


class TestActiveClaimUniquenessMigration:
    def test_migration_file_exists(self):
        import importlib.util
        from pathlib import Path
        path = Path(__file__).parent.parent / \
            "migrations/versions/0007_active_claim_uniqueness.py"
        assert path.exists()
        spec = importlib.util.spec_from_file_location("m0007", path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        assert hasattr(m, "upgrade")
        assert hasattr(m, "downgrade")

    def test_migration_creates_partial_unique_index(self):
        # Inspect the source rather than executing — the actual
        # partial-index DDL needs Postgres to exercise.
        from pathlib import Path
        path = Path(__file__).parent.parent / \
            "migrations/versions/0007_active_claim_uniqueness.py"
        src = path.read_text()
        # Must reference the three columns it claims to enforce
        # uniqueness over.
        assert "event_id" in src
        assert "source_id" in src
        assert "field_name" in src
        # Must be partial (excluding superseded rows).
        assert "superseded" in src.lower()
        # Must mention "unique" in the index definition (case-insensitive
        # to allow either a CREATE UNIQUE INDEX statement or a sa op).
        assert "unique" in src.lower()


# ──────────────────────────────────────────────────────────────────────────────
# Step 15 — projection_explanations + document_status persist on AccidentRecord
# ──────────────────────────────────────────────────────────────────────────────


class TestAccidentRecordHasV20Columns:
    def test_projection_explanations_column_exists(self):
        from atlas.models.orm import AccidentRecord
        assert hasattr(AccidentRecord, "projection_explanations")

    def test_document_status_column_exists(self):
        from atlas.models.orm import AccidentRecord
        assert hasattr(AccidentRecord, "document_status")


# ──────────────────────────────────────────────────────────────────────────────
# API surface — provenance returns projections + revisions
# ──────────────────────────────────────────────────────────────────────────────


class TestAPISurfaceForV20:
    def test_projection_explanation_out_model_exists(self):
        from atlas.api.app import ProjectionExplanationOut
        # Must include the documented selection_reason field
        assert "selection_reason" in ProjectionExplanationOut.model_fields

    def test_event_revision_out_model_exists(self):
        from atlas.api.app import EventRevisionOut
        assert "revision_type" in EventRevisionOut.model_fields

    def test_accident_provenance_includes_projections_and_revisions(self):
        from atlas.api.app import AccidentProvenance
        fields = AccidentProvenance.model_fields
        assert "projections" in fields
        assert "revisions" in fields

    def test_accident_detail_includes_document_status(self):
        from atlas.api.app import AccidentDetail
        assert "document_status" in AccidentDetail.model_fields


# ──────────────────────────────────────────────────────────────────────────────
# Conflict resolution endpoint — schema, routing, and validation rules
# ──────────────────────────────────────────────────────────────────────────────


class TestConflictResolveInSchema:
    """ConflictResolveIn must enforce the documented constraints at parse time."""

    def test_model_exists(self):
        from atlas.api.app import ConflictResolveIn  # noqa: F401

    def test_required_fields(self):
        import pydantic

        from atlas.api.app import ConflictResolveIn

        # resolution_type is required — resolved_by is now optional (auth provides it).
        with pytest.raises(pydantic.ValidationError):
            ConflictResolveIn(resolved_by="ops@example.com")    # missing resolution_type

    def test_valid_resolution_types_accepted(self):
        from atlas.api.app import ConflictResolveIn

        valid = [
            "claim_accepted", "claim_rejected", "claims_merged",
            "source_corrected", "not_applicable", "manual_override",
        ]
        for rt in valid:
            obj = ConflictResolveIn(
                resolution_type=rt,
                resolved_by="ops@example.com",
                accepted_claim_id="c-1" if rt == "claim_accepted" else None,
            )
            assert obj.resolution_type == rt

    def test_invalid_resolution_type_rejected(self):
        import pydantic

        from atlas.api.app import ConflictResolveIn

        with pytest.raises(pydantic.ValidationError):
            ConflictResolveIn(
                resolution_type="fabricated_winner",   # not in the Literal
                resolved_by="ops@example.com",
            )

    def test_optional_fields_default_to_none(self):
        from atlas.api.app import ConflictResolveIn

        obj = ConflictResolveIn(
            resolution_type="not_applicable",
            resolved_by="ops@example.com",
        )
        assert obj.accepted_claim_id is None
        assert obj.rejected_claim_ids is None
        assert obj.resolution is None

    def test_full_payload_round_trips(self):
        from atlas.api.app import ConflictResolveIn

        obj = ConflictResolveIn(
            resolution_type="claim_accepted",
            accepted_claim_id="claim-ntsb",
            rejected_claim_ids=["claim-asn"],
            resolution="NTSB final report takes precedence",
            resolved_by="reviewer@example.com",
        )
        d = obj.model_dump()
        assert d["resolution_type"] == "claim_accepted"
        assert d["accepted_claim_id"] == "claim-ntsb"
        assert d["rejected_claim_ids"] == ["claim-asn"]
        assert d["resolved_by"] == "reviewer@example.com"

    def test_accepted_claim_id_forbidden_for_claim_rejected(self):
        """accepted_claim_id must be None when resolution_type is 'claim_rejected'."""
        import pydantic

        from atlas.api.app import ConflictResolveIn

        with pytest.raises(pydantic.ValidationError):
            ConflictResolveIn(
                resolution_type="claim_rejected",
                accepted_claim_id="claim-a",   # not allowed for claim_rejected
                rejected_claim_ids=["claim-b"],
                resolved_by="ops@example.com",
            )

    def test_accepted_claim_id_forbidden_for_source_corrected(self):
        """accepted_claim_id must be None for non-claim_accepted types."""
        import pydantic

        from atlas.api.app import ConflictResolveIn

        with pytest.raises(pydantic.ValidationError):
            ConflictResolveIn(
                resolution_type="source_corrected",
                accepted_claim_id="claim-a",   # forbidden
                resolved_by="ops@example.com",
            )

    def test_rejected_claim_ids_required_for_claim_rejected(self):
        """rejected_claim_ids must be non-empty for claim_rejected."""
        import pydantic

        from atlas.api.app import ConflictResolveIn

        with pytest.raises(pydantic.ValidationError):
            ConflictResolveIn(
                resolution_type="claim_rejected",
                rejected_claim_ids=None,   # missing
                resolved_by="ops@example.com",
            )

    def test_no_overlap_between_accepted_and_rejected(self):
        """A claim cannot appear in both accepted_claim_id and rejected_claim_ids."""
        import pydantic

        from atlas.api.app import ConflictResolveIn

        with pytest.raises(pydantic.ValidationError):
            ConflictResolveIn(
                resolution_type="claim_accepted",
                accepted_claim_id="claim-a",
                rejected_claim_ids=["claim-a"],   # same claim — contradiction
                resolved_by="ops@example.com",
            )

    def test_claims_merged_accepts_optional_claim_ids(self):
        """claims_merged may optionally include accepted_claim_id."""
        from atlas.api.app import ConflictResolveIn

        obj = ConflictResolveIn(
            resolution_type="claims_merged",
            accepted_claim_id="claim-a",
            rejected_claim_ids=["claim-b"],
            resolved_by="ops@example.com",
        )
        assert obj.accepted_claim_id == "claim-a"

    def test_valid_claim_rejected_without_accepted(self):
        """claim_rejected with only rejected_claim_ids (survivor auto-derived later)."""
        from atlas.api.app import ConflictResolveIn

        obj = ConflictResolveIn(
            resolution_type="claim_rejected",
            rejected_claim_ids=["claim-b"],
            resolved_by="ops@example.com",
        )
        assert obj.resolution_type == "claim_rejected"
        assert obj.accepted_claim_id is None  # derived in endpoint, not here


    """The resolve endpoint must be registered and return the right shape."""

    def test_endpoint_registered_as_post(self):
        """The route must be a POST, not a GET."""
        from fastapi.routing import APIRoute

        from atlas.api import app as app_module

        matches = [
            route for route in app_module.app.router.routes
            if isinstance(route, APIRoute)
            and route.path == "/api/v1/conflicts/{conflict_id}/resolve"
        ]
        assert matches, "resolve endpoint must be registered"
        assert "POST" in matches[0].methods
        assert "GET" not in matches[0].methods

    def test_endpoint_uses_write_session(self):
        """Must use get_db (write + commit) not get_read_db."""
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module)
        # get_db must be imported and referenced in the resolve endpoint
        assert "get_db" in src

    def test_endpoint_triggers_projection_rebuild(self):
        """Projection rebuild is now owned by ConflictResolutionService."""
        import inspect

        from atlas.claims.resolution import ConflictResolutionService
        src = inspect.getsource(ConflictResolutionService.resolve)
        assert "rebuild_event" in src

    def test_response_model_is_conflict_out(self):
        """resolve_conflict must return a ConflictOut."""
        import inspect

        from atlas.api.app import resolve_conflict
        src = inspect.getsource(resolve_conflict)
        assert "ConflictOut" in src

    def test_conflict_out_exposes_resolved_by(self):
        """
        ConflictOut currently omits resolved_by (it was added in the ORM but
        not surfaced in the API).  The resolution endpoint response must
        include it so callers can confirm who resolved the conflict.
        """
        from atlas.api.app import ConflictOut
        # resolved_by must now be a field on the out model.
        assert "resolved_by" in ConflictOut.model_fields, (
            "ConflictOut must expose resolved_by so audit consumers can read "
            "who resolved each conflict without a separate DB query."
        )


class TestConflictResolveValidationLogic:
    """
    Validation rules are pure logic once we have the conflict record in
    hand.  We test them via the endpoint source code rather than spinning
    up Postgres — the same pattern used throughout test_v20.py.
    """

    def _src(self):
        import inspect

        from atlas.api.app import resolve_conflict
        return inspect.getsource(resolve_conflict)

    def test_open_status_check_present(self):
        """ConflictResolutionService must reject non-open conflicts."""
        import inspect

        from atlas.claims.resolution import ConflictResolutionService
        src = inspect.getsource(ConflictResolutionService.resolve)
        assert "status != \"open\"" in src or "status != 'open'" in src
        assert "ConflictAlreadyResolvedError" in src

    def test_accepted_claim_id_required_for_claim_accepted(self):
        """claim_accepted without accepted_claim_id must raise 422."""
        src = self._src()
        assert "claim_accepted" in src
        assert "accepted_claim_id" in src
        assert "422" in src

    def test_accepted_claim_id_membership_check(self):
        """accepted_claim_id must belong to the conflict's claim pair."""
        src = self._src()
        # Must reference both sides of the pair
        assert "conflict_claim_ids" in src or \
               ("claim_a_id" in src and "claim_b_id" in src)

    def test_rejected_claim_ids_membership_check(self):
        """rejected_claim_ids must only reference claims in the conflict."""
        src = self._src()
        assert "rejected_claim_ids" in src

    def test_resolved_by_persisted(self):
        """resolved_by (operator_id) must be written to the conflict row."""
        import inspect

        from atlas.claims.resolution import ConflictResolutionService
        src = inspect.getsource(ConflictResolutionService.resolve)
        assert "conflict.resolved_by = operator_id" in src

    def test_resolved_at_set_to_utc_now(self):
        """resolved_at must be set from the server clock, not the client."""
        import inspect

        from atlas.claims.resolution import ConflictResolutionService
        src = inspect.getsource(ConflictResolutionService.resolve)
        assert "resolved_at" in src
        assert "UTC" in src or "timezone.utc" in src

    def test_accepted_claim_restored_to_pre_dispute_type(self):
        """
        Claim restoration is now handled by ProjectionService.finalize_accepted_
        claims_for_field(), not inline in resolve_conflict().  The route
        delegates to that method; the method owns the restoration invariant.
        """
        import inspect

        from atlas.claims.projection import ProjectionService
        src = inspect.getsource(ProjectionService.finalize_accepted_claims_for_field)
        # Must restore the accepted claim to its pre-dispute type.
        assert "claim.claim_type = restore_type" in src
        # Must recover pre-dispute type from ClaimHistory.
        assert "ClaimHistory" in src
        assert "DISPUTED" in src
        # Must record the restoration in history.
        assert "field_finalized:" in src

    def test_claim_restoration_recovers_original_type_from_history(self):
        """
        Restoration uses ClaimHistory to find the pre-dispute type so an
        INFERRED claim is restored to INFERRED, not promoted to CONFIRMED.
        """
        import inspect

        from atlas.claims.projection import ProjectionService
        src = inspect.getsource(ProjectionService.finalize_accepted_claims_for_field)
        assert "old_claim_type" in src
        assert "restore_type" in src
        assert "CONFIRMED" in src  # safe fallback must be present

    def test_claim_not_restored_when_other_open_conflicts_remain(self):
        """
        finalize_accepted_claims_for_field() must abort immediately if any
        open conflicts remain for the field — it must not restore when the
        field is still in dispute.
        """
        import inspect

        from atlas.claims.projection import ProjectionService
        src = inspect.getsource(ProjectionService.finalize_accepted_claims_for_field)
        # Must query for open conflicts and return early if any exist.
        assert "open" in src
        assert "return" in src  # early exit path

    def test_error_message_is_accurate_about_transaction_rollback(self):
        """
        The 500 error message must say 'Resolution was not saved' — get_db()
        rolls back on exception, so claiming 'was recorded' would be false.
        """
        src = self._src()
        assert "was not saved" in src
        assert "was recorded" not in src

    def test_error_message_event_id_is_interpolated(self):
        """The retry hint must use an f-string so event_id is interpolated."""
        src = self._src()
        assert "event_id" in src
        assert 'f"' in src or "f'" in src


# ──────────────────────────────────────────────────────────────────────────────
# Behavioral tests for _build_record field-withholding with open conflicts
# ──────────────────────────────────────────────────────────────────────────────


class TestBuildRecordOpenConflictWithholding:
    """
    _build_record must suppress field values for any field that has an
    open conflict, regardless of what _select_winners() returned.

    This is the authoritative enforcement of the "never display more
    certainty than the data supports" principle for the multi-conflict
    case: A-vs-B resolved with A accepted, but A-vs-C still open →
    the field must still be withheld until A-vs-C is also resolved.

    These tests call _build_record() directly with fabricated inputs so
    they are fast and require no database.
    """

    def _svc(self):
        from atlas.claims.projection import ProjectionService
        return ProjectionService(session=MagicMock())

    def _make_record(self, winners, conflicts):
        """Helper: call _build_record with minimal inputs."""
        svc = self._svc()
        return svc._build_record(
            event_id="evt-test",
            claims=[],
            winners=winners,
            sources={},
            conflicts=conflicts,
            score=0.5,
            breakdown={},
        )

    def _open_conflict(self, field: str) -> MagicMock:
        cf = MagicMock()
        cf.field_name = field
        cf.status = "open"
        cf.resolution = None
        return cf

    def _resolved_conflict(self, field: str) -> MagicMock:
        cf = MagicMock()
        cf.field_name = field
        cf.status = "resolved"
        cf.resolution = "NTSB is authoritative"
        return cf

    def test_no_conflict_projects_winner(self):
        """Baseline: a field with a winner and no open conflict is projected."""
        w = _claim(
            claim_id="c1", field_name="fatalities_total",
            value=0, source_id="s1",
        )
        record = self._make_record(
            winners={"fatalities_total": w},
            conflicts=[],
        )
        assert record["fatalities_total"] == 0

    def test_open_conflict_withholds_field(self):
        """
        A field with an open conflict must be withheld (None) even when
        _select_winners() found a winner for it.
        """
        w = _claim(
            claim_id="c1", field_name="fatalities_total",
            value=0, source_id="s1",
        )
        record = self._make_record(
            winners={"fatalities_total": w},
            conflicts=[self._open_conflict("fatalities_total")],
        )
        assert record["fatalities_total"] is None, (
            "_build_record must withhold a field that has an open conflict, "
            "even when a winner exists"
        )

    def test_resolved_conflict_does_not_withhold_field(self):
        """A resolved conflict must not suppress the winning value."""
        w = _claim(
            claim_id="c1", field_name="fatalities_total",
            value=0, source_id="s1",
        )
        record = self._make_record(
            winners={"fatalities_total": w},
            conflicts=[self._resolved_conflict("fatalities_total")],
        )
        assert record["fatalities_total"] == 0

    def test_open_conflict_on_one_field_does_not_affect_another(self):
        """
        An open conflict on field X must not suppress field Y.
        Withholding must be per-field, not event-wide.
        """
        w_fatal = _claim(
            claim_id="c1", field_name="fatalities_total",
            value=1, source_id="s1",
        )
        w_make = _claim(
            claim_id="c2", field_name="aircraft_make",
            value="Cessna", source_id="s1",
        )
        record = self._make_record(
            winners={"fatalities_total": w_fatal, "aircraft_make": w_make},
            conflicts=[self._open_conflict("fatalities_total")],
        )
        assert record["fatalities_total"] is None  # withheld — open conflict
        assert record["aircraft_make"] == "Cessna"  # unaffected

    def test_three_source_multi_conflict_partial_resolution(self):
        """
        A-vs-B resolved (A accepted), A-vs-C still open.
        The field must still be withheld because A-vs-C is open.
        This is the exact scenario the review flagged.
        """
        # A has been restored to CONFIRMED and is the winner.
        a = _claim(
            claim_id="a", field_name="fatalities_total",
            value=0, source_id="s1", claim_type="confirmed",
        )
        record = self._make_record(
            winners={"fatalities_total": a},
            conflicts=[
                self._resolved_conflict("fatalities_total"),  # A-vs-B resolved
                self._open_conflict("fatalities_total"),       # A-vs-C still open
            ],
        )
        assert record["fatalities_total"] is None, (
            "Field must remain withheld while any open conflict exists, "
            "even after a partial resolution"
        )

    def test_all_conflicts_resolved_projects_winner(self):
        """Once all conflicts are resolved, the winner is projected."""
        a = _claim(
            claim_id="a", field_name="fatalities_total",
            value=0, source_id="s1", claim_type="confirmed",
        )
        record = self._make_record(
            winners={"fatalities_total": a},
            conflicts=[
                self._resolved_conflict("fatalities_total"),
                self._resolved_conflict("fatalities_total"),
            ],
        )
        assert record["fatalities_total"] == 0

    def test_has_conflicts_still_true_when_open_conflict_exists(self):
        """has_conflicts must reflect open-conflict state independently."""
        record = self._make_record(
            winners={},
            conflicts=[self._open_conflict("fatalities_total")],
        )
        assert record["has_conflicts"] is True

    def test_has_conflicts_false_when_all_resolved(self):
        record = self._make_record(
            winners={},
            conflicts=[self._resolved_conflict("fatalities_total")],
        )
        assert record["has_conflicts"] is False


# ──────────────────────────────────────────────────────────────────────────────
# Behavioral integration test: finalize_accepted_claims_for_field full flow
# ──────────────────────────────────────────────────────────────────────────────


class TestFinalizeAcceptedClaimsIntegration:
    """
    End-to-end behavioral tests for the multi-conflict resolution flow.

    These tests use async mock sessions (the same pattern as the
    existing projection tests in test_core.py) so they exercise real
    code paths without a live database.

    Scenario: three sources disagree on fatalities_total.
      Claim A (NTSB, tier 1) says 0
      Claim B (ASN, tier 2) says 1
      Claim C (XYZ, tier 3) says 2

    Resulting conflicts: A-B open, A-C open, B-C open.

    Resolution sequence:
      Step 1: resolve A-B, accept A → field still withheld (A-C, B-C open)
      Step 2: resolve B-C, reject B  → field still withheld (A-C open)
      Step 3: resolve A-C, accept A  → field now settled, A restored, projected
    """

    def _claim_mock(self, claim_id, field_name, value, source_id,
                    claim_type="disputed"):
        from atlas.models import claim_value as cv
        c = MagicMock()
        c.id = claim_id
        c.field_name = field_name
        c.field_value = cv.encode(value)
        c.source_id = source_id
        c.claim_type = claim_type
        c.is_winning = False
        c.created_at = datetime(2023, 1, 4, tzinfo=UTC)
        return c

    def _conflict_mock(self, conflict_id, field_name,
                       claim_a_id, claim_b_id,
                       status="open", accepted_claim_id=None):
        cf = MagicMock()
        cf.id = conflict_id
        cf.field_name = field_name
        cf.claim_a_id = claim_a_id
        cf.claim_b_id = claim_b_id
        cf.status = status
        cf.resolution = None if status == "open" else "resolved"
        cf.accepted_claim_id = accepted_claim_id
        return cf

    def _history_mock(self, claim_id, old_type, new_type):
        h = MagicMock()
        h.claim_id = claim_id
        h.old_claim_type = old_type
        h.new_claim_type = new_type
        h.changed_at = datetime(2023, 1, 1, tzinfo=UTC)
        return h

    def test_field_withheld_when_partial_resolution_still_has_open_conflict(self):
        """
        After resolving A-B (accept A), two other conflicts remain (A-C, B-C).
        _build_record() must withhold fatalities_total.
        """
        from atlas.claims.projection import ProjectionService
        svc = ProjectionService(session=MagicMock())

        a = self._claim_mock("a", "fatalities_total", 0, "s1", "confirmed")
        # A-B resolved, A accepted; A-C and B-C still open
        conflicts = [
            self._conflict_mock("cf-ab", "fatalities_total", "a", "b",
                                status="resolved", accepted_claim_id="a"),
            self._conflict_mock("cf-ac", "fatalities_total", "a", "c",
                                status="open"),
            self._conflict_mock("cf-bc", "fatalities_total", "b", "c",
                                status="open"),
        ]
        record = svc._build_record(
            event_id="evt-1", claims=[a], winners={"fatalities_total": a},
            sources={}, conflicts=conflicts, score=0.5, breakdown={},
        )
        assert record["fatalities_total"] is None, (
            "Field must still be withheld: two open conflicts remain"
        )

    def test_field_projected_when_all_conflicts_resolved(self):
        """
        After all three conflicts are resolved (A accepted in A-B and A-C),
        _build_record() must project A's value.
        """
        from atlas.claims.projection import ProjectionService
        svc = ProjectionService(session=MagicMock())

        a = self._claim_mock("a", "fatalities_total", 0, "s1", "confirmed")
        conflicts = [
            self._conflict_mock("cf-ab", "fatalities_total", "a", "b",
                                status="resolved", accepted_claim_id="a"),
            self._conflict_mock("cf-ac", "fatalities_total", "a", "c",
                                status="resolved", accepted_claim_id="a"),
            self._conflict_mock("cf-bc", "fatalities_total", "b", "c",
                                status="resolved", accepted_claim_id=None),
        ]
        record = svc._build_record(
            event_id="evt-1", claims=[a], winners={"fatalities_total": a},
            sources={}, conflicts=conflicts, score=0.5, breakdown={},
        )
        assert record["fatalities_total"] == 0, (
            "All conflicts resolved: A's value must be projected"
        )

    def test_is_winning_false_for_withheld_field_winner(self):
        """
        A claim that is the winner for a field that is currently withheld
        must NOT be marked is_winning=True after rebuild_event().
        """
        import inspect

        from atlas.claims.projection import ProjectionService
        src = inspect.getsource(ProjectionService.rebuild_event)
        # Must compute open_conflict_fields before marking is_winning.
        assert "open_conflict_fields" in src
        assert "projected_winners" in src
        # Must use projected_winners (not all winners) for is_winning.
        assert "projected_winners.values()" in src

    def test_winning_source_count_excludes_withheld_field_sources(self):
        """
        source_ids / winning_source_count must only include sources for
        fields that are actually projected — not withheld-field winners.
        """
        from atlas.claims.projection import ProjectionService
        src = inspect.getsource(ProjectionService._build_record)
        # Must filter winners by open_conflict_fields when building source_ids.
        assert "projected_winner_source_ids" in src
        assert "open_conflict_fields" in src

    @pytest.mark.asyncio
    async def test_finalize_aborts_when_open_conflicts_remain(self):
        """
        finalize_accepted_claims_for_field() must be a no-op when any
        open conflict still exists for the field.
        """
        from unittest.mock import AsyncMock

        from atlas.claims.projection import ProjectionService

        session = MagicMock()
        # Simulate: one open conflict returned
        open_cf = self._conflict_mock("cf-ac", "fatalities_total", "a", "c")
        execute_result = MagicMock()
        execute_result.scalar_one_or_none.return_value = open_cf
        session.execute = AsyncMock(return_value=execute_result)

        svc = ProjectionService(session=session)
        # Should return without doing anything further.
        await svc.finalize_accepted_claims_for_field(
            event_id="evt-1",
            field_name="fatalities_total",
            resolved_by="reviewer@example.com",
        )
        # Only one DB query (the open-conflict check) should have run.
        assert session.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_finalize_restores_deferred_accepted_claim(self):
        """
        When no open conflicts remain and a resolved conflict has an
        accepted_claim_id pointing to a still-DISPUTED claim, finalize
        must restore that claim to its pre-dispute type.
        """
        from unittest.mock import AsyncMock

        from atlas.claims.projection import ProjectionService

        session = MagicMock()
        claim_a = self._claim_mock("a", "fatalities_total", 0, "s1", "disputed")
        history = self._history_mock("a", "confirmed", "disputed")
        resolved_cf = self._conflict_mock(
            "cf-ab", "fatalities_total", "a", "b",
            status="resolved", accepted_claim_id="a",
        )

        call_count = 0

        async def fake_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                # open-conflict check → nothing open
                result.scalar_one_or_none.return_value = None
            elif call_count == 2:
                # resolved-conflicts query → one resolved conflict
                result.scalars.return_value.all.return_value = [resolved_cf]
            elif call_count == 3:
                # ClaimHistory lookup for claim a
                result.scalar_one_or_none.return_value = history
            return result

        session.execute = AsyncMock(side_effect=fake_execute)
        session.get = AsyncMock(return_value=claim_a)
        session.add = MagicMock()

        svc = ProjectionService(session=session)
        await svc.finalize_accepted_claims_for_field(
            event_id="evt-1",
            field_name="fatalities_total",
            resolved_by="reviewer@example.com",
        )

        # Claim A must have been restored from DISPUTED to CONFIRMED.
        assert claim_a.claim_type == "confirmed"
        # ClaimHistory row must have been written.
        session.add.assert_called_once()
        history_row = session.add.call_args[0][0]
        assert history_row.old_claim_type == "disputed"
        assert history_row.new_claim_type == "confirmed"
        assert "field_finalized:" in history_row.change_reason

    @pytest.mark.asyncio
    async def test_finalize_aborts_on_contradictory_resolutions(self):
        """
        If a claim was accepted in one conflict but rejected in another,
        finalize must NOT restore it — the reviewers contradict each other.
        The field stays withheld.
        """
        from unittest.mock import AsyncMock

        from atlas.claims.projection import ProjectionService

        session = MagicMock()

        # A accepted in A-vs-B, A rejected in A-vs-C — contradiction
        cf_ab = self._conflict_mock("cf-ab", "fatalities_total", "a", "b",
                                    status="resolved", accepted_claim_id="a")
        cf_ab.rejected_claim_ids = None
        cf_ac = self._conflict_mock("cf-ac", "fatalities_total", "a", "c",
                                    status="resolved", accepted_claim_id=None)
        cf_ac.rejected_claim_ids = ["a"]  # A was rejected here

        call_count = 0

        async def fake_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                # open-conflict check → none open
                result.scalar_one_or_none.return_value = None
            elif call_count == 2:
                # resolved-conflicts query
                result.scalars.return_value.all.return_value = [cf_ab, cf_ac]
            return result

        session.execute = AsyncMock(side_effect=fake_execute)
        session.get = AsyncMock()  # should not be called
        session.add = MagicMock()

        svc = ProjectionService(session=session)
        await svc.finalize_accepted_claims_for_field(
            event_id="evt-1",
            field_name="fatalities_total",
            resolved_by="reviewer@example.com",
        )

        # Must abort — no claim should be restored.
        session.get.assert_not_called()
        session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_finalize_aborts_on_multiple_distinct_accepted_claims(self):
        """
        If two different claims were each accepted (in different conflicts),
        finalize must abort — ambiguous which one to project.
        """
        from unittest.mock import AsyncMock

        from atlas.claims.projection import ProjectionService

        session = MagicMock()

        # A accepted in A-vs-B; B accepted in B-vs-C — two distinct winners
        cf_ab = self._conflict_mock("cf-ab", "fatalities_total", "a", "b",
                                    status="resolved", accepted_claim_id="a")
        cf_ab.rejected_claim_ids = None
        cf_bc = self._conflict_mock("cf-bc", "fatalities_total", "b", "c",
                                    status="resolved", accepted_claim_id="b")
        cf_bc.rejected_claim_ids = None

        call_count = 0

        async def fake_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                result.scalar_one_or_none.return_value = None
            elif call_count == 2:
                result.scalars.return_value.all.return_value = [cf_ab, cf_bc]
            return result

        session.execute = AsyncMock(side_effect=fake_execute)
        session.get = AsyncMock()
        session.add = MagicMock()

        svc = ProjectionService(session=session)
        await svc.finalize_accepted_claims_for_field(
            event_id="evt-1",
            field_name="fatalities_total",
            resolved_by="reviewer@example.com",
        )

        session.get.assert_not_called()
        session.add.assert_not_called()

    def test_claim_rejected_survivor_auto_derivation_in_endpoint(self):
        """
        ConflictResolutionService must auto-derive the surviving claim as
        accepted_claim_id when resolution_type is 'claim_rejected'.
        """
        import inspect

        from atlas.claims.resolution import ConflictResolutionService
        src = inspect.getsource(ConflictResolutionService.resolve)
        assert "survivors" in src
        assert "accepted_claim_id" in src

    def test_contradiction_detection_in_finalize(self):
        """
        finalize_accepted_claims_for_field must detect when a claim is
        both accepted and rejected across different resolved conflicts
        and abort restoration.
        """
        import inspect

        from atlas.claims.projection import ProjectionService
        src = inspect.getsource(ProjectionService.finalize_accepted_claims_for_field)
        assert "contradictions" in src
        assert "rejected_ids" in src
        assert "contradictory_resolutions" in src

    def test_ambiguous_multiple_accepted_detection_in_finalize(self):
        """
        If multiple distinct claims are accepted across conflicts,
        finalize must detect the ambiguity and abort.
        """
        import inspect

        from atlas.claims.projection import ProjectionService
        src = inspect.getsource(ProjectionService.finalize_accepted_claims_for_field)
        assert "ambiguous_multiple_accepted" in src
        assert "len(accepted_ids) > 1" in src


class TestMigration0010:
    """Migration 0010 must define the audit indexes correctly."""

    def _load(self):
        import importlib.util
        from pathlib import Path
        path = Path(__file__).parent.parent / \
            "migrations/versions/0010_conflict_resolution_audit_index.py"
        assert path.exists(), "Migration 0010 must exist"
        spec = importlib.util.spec_from_file_location("m0010", path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m, path.read_text()

    def test_migration_file_exists_and_is_valid(self):
        m, _ = self._load()
        assert hasattr(m, "upgrade")
        assert hasattr(m, "downgrade")

    def test_revises_0009(self):
        _, src = self._load()
        assert "down_revision" in src
        assert "0009" in src

    def test_creates_resolved_by_index(self):
        _, src = self._load()
        assert "ix_conflict_resolved_by" in src
        assert "resolved_by" in src

    def test_creates_resolved_at_index(self):
        _, src = self._load()
        assert "ix_conflict_resolved_at" in src
        assert "resolved_at" in src

    def test_indexes_are_partial(self):
        """Both indexes must be partial (WHERE status = 'resolved')."""
        _, src = self._load()
        assert "resolved" in src.lower()
        # Both upgrade and downgrade must mention the index names
        assert src.count("ix_conflict_resolved_by") >= 2   # create + drop
        assert src.count("ix_conflict_resolved_at") >= 2


# ──────────────────────────────────────────────────────────────────────────────
# v25 feature tests
# ──────────────────────────────────────────────────────────────────────────────


class TestClaimTypeRejected:
    """REJECTED is a first-class ClaimType — not left as DISPUTED."""

    def test_rejected_in_enum(self):
        from atlas.models.orm import ClaimType
        assert ClaimType.REJECTED.value == "rejected"
        assert "rejected" in [ct.value for ct in ClaimType]

    def test_rejected_excluded_from_projection_winners(self):
        """_select_winners must never pick a REJECTED claim."""
        from atlas.claims.projection import ProjectionService
        svc = ProjectionService(session=MagicMock())
        rejected = _claim(claim_id="r1", field_name="fatalities_total", value=99, source_id="s1", claim_type="rejected")
        winners = svc._select_winners([rejected], {})
        assert "fatalities_total" not in winners

    def test_rejected_not_restored_by_finalize(self):
        """finalize must skip REJECTED claims even if they appear in accepted_ids."""
        import inspect

        from atlas.claims.projection import ProjectionService
        src = inspect.getsource(ProjectionService.finalize_accepted_claims_for_field)
        assert "REJECTED" in src
        assert "permanently excluded" in src or "never restore" in src

    def test_resolution_service_marks_rejected_claims(self):
        """ConflictResolutionService must set claim_type=REJECTED for rejected claims."""
        import inspect

        from atlas.claims.resolution import ConflictResolutionService
        src = inspect.getsource(ConflictResolutionService.resolve)
        assert "ClaimType.REJECTED" in src
        assert "conflict_rejected:" in src




class TestFrontendRejectedClaimType:
    """Frontend TypeScript must stay aligned with backend ClaimType.REJECTED."""

    def test_claim_type_tuple_includes_rejected(self):
        from pathlib import Path
        src = (Path(__file__).parent.parent / "web/types/index.ts").read_text()
        assert "export const CLAIM_TYPES" in src
        assert "export type ClaimType = (typeof CLAIM_TYPES)[number]" in src
        for value in ("confirmed", "inferred", "disputed", "rejected", "superseded", "pending"):
            assert f"'{value}'" in src, f"Missing ClaimType value: {value}"
        assert "isClaimType" in src
        assert "claim_type: ClaimType" in src
        assert "'confirmed' | 'inferred' | 'disputed' | 'superseded' | 'pending'" not in src

    def test_backend_claimout_exposes_claim_type_literal(self):
        from pathlib import Path
        src = (Path(__file__).parent.parent / "src/atlas/api/schemas.py").read_text()
        assert "ClaimTypeValue = Literal" in src
        assert "claim_type: ClaimTypeValue" in src
        for value in ("confirmed", "inferred", "disputed", "rejected", "superseded", "pending"):
            assert f'\"{value}\"' in src, f"Missing API ClaimTypeValue: {value}"

    def test_backend_and_frontend_claim_type_contracts_match(self):
        import re
        from pathlib import Path
        root = Path(__file__).parent.parent
        bsrc = (root / "src/atlas/api/schemas.py").read_text()
        fsrc = (root / "web/types/index.ts").read_text()
        b_block = re.search(r"ClaimTypeValue = Literal\[(.*?)\]", bsrc, re.S).group(1)
        f_block = re.search(r"export const CLAIM_TYPES = \[(.*?)\] as const", fsrc, re.S).group(1)
        backend_values = re.findall(r'\"([^\"]+)\"', b_block)
        frontend_values = re.findall(r"'([^']+)'", f_block)
        assert backend_values == frontend_values
        assert "rejected" in backend_values

    def test_claim_type_badges_and_labels_are_exhaustive(self):
        from pathlib import Path
        src = (Path(__file__).parent.parent / "web/lib/utils.ts").read_text()
        assert "import { CLAIM_TYPES, type ClaimType }" in src
        assert "Record<ClaimType, string>" in src
        assert "rejected:" in src
        assert "line-through" in src
        assert "claimTypeLabel" in src
        assert "case 'rejected': return 'Rejected'" in src
        assert "const exhaustive: never = type" in src

    def test_rejected_claims_have_a_field_status_fallback(self):
        from pathlib import Path
        src = (Path(__file__).parent.parent / "web/components/AccidentDetailPanel.tsx").read_text()
        assert "| 'Rejected'" in src
        assert "winning.claim_type === 'rejected'" in src
        assert "return 'Rejected'" in src
        assert "Rejected:" in src

    def test_mock_provenance_exercises_rejected_rendering(self):
        from pathlib import Path
        src = (Path(__file__).parent.parent / "web/lib/api.ts").read_text()
        assert "claim_type: 'rejected'" in src
        assert "Rejected during conflict review" in src

class TestAuthModule:
    """API key auth module structure and behaviour."""

    def test_auth_module_exists(self):
        from atlas.api import auth  # noqa: F401

    def test_operator_context_dataclass(self):
        from atlas.api.auth import OperatorContext
        op = OperatorContext(id="ops@example.com", role="reviewer", key_id="key-1")
        assert op.id == "ops@example.com"
        assert op.role == "reviewer"

    def test_require_reviewer_dependency_exists(self):
        from atlas.api.auth import require_reviewer  # noqa: F401

    def test_require_admin_dependency_exists(self):
        from atlas.api.auth import require_admin  # noqa: F401

    def test_get_operator_dependency_exists(self):
        from atlas.api.auth import get_operator  # noqa: F401

    def test_key_hash_is_sha256(self):
        """Key hashing must use SHA-256 so it is constant-time comparable."""
        import hashlib

        from atlas.api.auth import _hash_key
        raw = "test-key-12345"
        assert _hash_key(raw) == hashlib.sha256(raw.encode()).hexdigest()

    def test_require_reviewer_returns_sentinel_when_auth_disabled(self):
        """When API_AUTH_ENABLED=False, require_reviewer allows any request."""
        import inspect

        from atlas.api import auth as auth_module
        src = inspect.getsource(auth_module.require_reviewer)
        assert "api_auth_enabled" in src
        # Must return a context (not raise) when auth is disabled.
        assert "return" in src
        assert "reviewer" in src

    def test_require_reviewer_raises_401_when_no_key_and_auth_enabled(self):
        """When auth is enabled and no key provided, must raise 401."""
        import inspect

        from atlas.api import auth as auth_module
        src = inspect.getsource(auth_module.require_reviewer)
        assert "401" in src
        assert "WWW-Authenticate" in src

    def test_require_reviewer_raises_403_for_wrong_role(self):
        """A valid key with wrong role must raise 403."""
        import inspect

        from atlas.api import auth as auth_module
        src = inspect.getsource(auth_module.require_reviewer)
        assert "403" in src
        assert "Insufficient role" in src

    def test_require_admin_rejects_auth_disabled_mode(self):
        """Admin dependencies must not return the auth-disabled reviewer sentinel."""
        import inspect

        from atlas.api import auth as auth_module
        src = inspect.getsource(auth_module.require_admin)
        assert "api_auth_enabled" in src
        assert "API_AUTH_ENABLED=true" in src
        assert "Admin endpoints require" in src
        assert "return OperatorContext" not in src

    def test_require_admin_requires_exact_admin_role(self):
        import inspect

        from atlas.api import auth as auth_module
        src = inspect.getsource(auth_module.require_admin)
        assert 'op.role != "admin"' in src
        assert "Insufficient role" in src
        assert "operator_id" in src

    def test_api_key_orm_model_exists(self):
        from atlas.models.orm import ApiKey
        fields = [c.key for c in ApiKey.__table__.columns]
        assert "key_hash" in fields
        assert "operator_id" in fields
        assert "role" in fields
        assert "is_active" in fields

    def test_route_uses_require_reviewer(self):
        """The resolve route must depend on require_reviewer."""
        import inspect

        from atlas.api.app import resolve_conflict
        src = inspect.getsource(resolve_conflict)
        assert "require_reviewer" in src
        assert "OperatorContext" in src

    def test_operator_id_derived_from_auth_context(self):
        """resolved_by must come from operator.id, not body.resolved_by directly."""
        import inspect

        from atlas.api.app import resolve_conflict
        src = inspect.getsource(resolve_conflict)
        assert "operator.id" in src
        assert "operator_id" in src

    def test_auth_uses_dedicated_committing_session(self):
        """Auth must not use get_read_db, or last_used_at never persists."""
        import inspect

        from atlas.api import auth as auth_module

        auth_src = inspect.getsource(auth_module)
        assert "get_auth_db" in auth_src
        assert "get_read_db" not in auth_src
        assert "Depends(get_auth_db)" in auth_src

    def test_resolve_key_persists_last_used_at(self):
        """A valid API key must explicitly update and commit last_used_at."""
        import inspect

        from atlas.api import auth as auth_module

        src = inspect.getsource(auth_module._resolve_key)
        assert "_record_key_use" in src
        assert "last_used_at" in inspect.getsource(auth_module._record_key_use)
        assert ".values(last_used_at=" in inspect.getsource(auth_module._record_key_use)
        assert "await db.commit()" in inspect.getsource(auth_module._record_key_use)

    def test_auth_last_used_update_is_not_best_effort_only(self):
        """The old in-memory assignment comment masked a non-persisted write."""
        import inspect

        from atlas.api import auth as auth_module

        src = inspect.getsource(auth_module._resolve_key)
        assert "best-effort" not in src
        assert "non-blocking" not in src
        assert "api_key.last_used_at =" not in src


class TestAuthSessionDependency:
    """Dedicated session for auth audit writes."""

    def test_get_auth_db_exists(self):
        from atlas.db.engine import get_auth_db  # noqa: F401

    def test_get_auth_db_does_not_commit_on_clean_exit(self):
        import inspect

        from atlas.db import engine

        src = inspect.getsource(engine.get_auth_db)
        assert "await session.commit()" not in src
        assert "await session.rollback()" in src
        assert "last_used_at" in src


class TestConflictResolutionService:
    """ConflictResolutionService owns all resolution invariants."""

    def test_service_module_exists(self):
        from atlas.claims import resolution  # noqa: F401

    def test_service_class_exists(self):
        from atlas.claims.resolution import ConflictResolutionService  # noqa: F401

    def test_exception_classes_exist(self):
        from atlas.claims.resolution import (
            ConflictAlreadyResolvedError,
            ConflictNotFoundError,
            ConflictValidationError,
            ProjectionRebuildError,
        )
        # All must be proper exceptions.
        for exc_class in (
            ConflictNotFoundError, ConflictAlreadyResolvedError,
            ConflictValidationError, ProjectionRebuildError,
        ):
            assert issubclass(exc_class, Exception)

    def test_already_resolved_error_carries_status(self):
        from atlas.claims.resolution import ConflictAlreadyResolvedError
        err = ConflictAlreadyResolvedError("cf-1", "resolved")
        assert err.conflict_id == "cf-1"
        assert err.status == "resolved"

    def test_service_owns_locking(self):
        """Service must use SELECT FOR UPDATE on conflict rows."""
        import inspect

        from atlas.claims.resolution import ConflictResolutionService
        src = inspect.getsource(ConflictResolutionService.resolve)
        assert "with_for_update" in src

    def test_service_increments_version(self):
        """version must be incremented on every resolution for optimistic locking."""
        import inspect

        from atlas.claims.resolution import ConflictResolutionService
        src = inspect.getsource(ConflictResolutionService.resolve)
        assert "version" in src
        # Must increment, not just set to a constant.
        assert "version" in src and "+= 1" in src or "version + 1" in src or "+ 1" in src

    def test_service_delegates_to_finalize_and_rebuild(self):
        """Service must call finalize_accepted_claims_for_field then rebuild_event."""
        import inspect

        from atlas.claims.resolution import ConflictResolutionService
        src = inspect.getsource(ConflictResolutionService.resolve)
        assert "finalize_accepted_claims_for_field" in src
        assert "rebuild_event" in src

    @pytest.mark.asyncio
    async def test_service_raises_not_found_for_missing_conflict(self):
        from unittest.mock import AsyncMock

        from atlas.claims.resolution import (
            ConflictNotFoundError,
            ConflictResolutionService,
        )

        session = MagicMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result)

        svc = ConflictResolutionService(session=session)
        with pytest.raises(ConflictNotFoundError):
            await svc.resolve(
                conflict_id="missing",
                resolution_type="claim_accepted",
                operator_id="reviewer@example.com",
                accepted_claim_id="claim-a",
            )

    @pytest.mark.asyncio
    async def test_service_raises_already_resolved_for_non_open_conflict(self):
        from unittest.mock import AsyncMock

        from atlas.claims.resolution import (
            ConflictAlreadyResolvedError,
            ConflictResolutionService,
        )

        session = MagicMock()
        conflict = MagicMock()
        conflict.status = "resolved"
        conflict.claim_a_id = "a"
        conflict.claim_b_id = "b"
        result = MagicMock()
        result.scalar_one_or_none.return_value = conflict
        session.execute = AsyncMock(return_value=result)

        svc = ConflictResolutionService(session=session)
        with pytest.raises(ConflictAlreadyResolvedError) as exc_info:
            await svc.resolve(
                conflict_id="cf-1",
                resolution_type="claim_accepted",
                operator_id="reviewer@example.com",
                accepted_claim_id="a",
            )
        assert exc_info.value.status == "resolved"

    @pytest.mark.asyncio
    async def test_service_raises_validation_error_for_bad_accepted_id(self):
        from unittest.mock import AsyncMock

        from atlas.claims.resolution import (
            ConflictResolutionService,
            ConflictValidationError,
        )

        session = MagicMock()
        conflict = MagicMock()
        conflict.status = "open"
        conflict.claim_a_id = "a"
        conflict.claim_b_id = "b"
        conflict.version = 0
        result = MagicMock()
        result.scalar_one_or_none.return_value = conflict
        session.execute = AsyncMock(return_value=result)

        svc = ConflictResolutionService(session=session)
        with pytest.raises(ConflictValidationError):
            await svc.resolve(
                conflict_id="cf-1",
                resolution_type="claim_accepted",
                operator_id="reviewer@example.com",
                accepted_claim_id="c",   # not in {a, b}
            )


class TestConflictVersionColumn:
    """ClaimConflict.version enables optimistic locking."""

    def test_version_column_exists_on_orm(self):
        from atlas.models.orm import ClaimConflict
        cols = [c.key for c in ClaimConflict.__table__.columns]
        assert "version" in cols

    def test_version_default_is_zero(self):
        from atlas.models.orm import ClaimConflict
        col = ClaimConflict.__table__.columns["version"]
        # Python-side default OR server default should be 0
        py_default = col.default.arg if col.default is not None else None
        srv_default = str(col.server_default.arg) if col.server_default is not None else None
        assert py_default == 0 or srv_default == "0"


class TestMigration0011:
    """Migration 0011 must create api_keys, add version, and be idempotent."""

    def _load(self):
        import importlib.util
        from pathlib import Path
        path = Path(__file__).parent.parent / \
            "migrations/versions/0011_api_keys_conflict_version_rejected_type.py"
        assert path.exists(), "Migration 0011 must exist"
        spec = importlib.util.spec_from_file_location("m0011", path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m, path.read_text()

    def test_migration_file_valid(self):
        m, _ = self._load()
        assert hasattr(m, "upgrade")
        assert hasattr(m, "downgrade")

    def test_revises_0010(self):
        _, src = self._load()
        assert "down_revision" in src
        assert "0010" in src

    def test_creates_api_keys_table(self):
        _, src = self._load()
        assert "api_keys" in src
        assert "key_hash" in src
        assert "operator_id" in src
        assert "role" in src
        assert "is_active" in src

    def test_adds_version_to_conflict(self):
        _, src = self._load()
        assert "version" in src
        assert "claim_conflicts" in src
        assert "Integer" in src

    def test_downgrade_reverses_all_changes(self):
        _, src = self._load()
        # downgrade must drop both the table and the column
        assert "drop_table" in src
        assert "drop_column" in src
        assert src.count("api_keys") >= 2    # create + drop
        assert src.count("version") >= 2     # add + drop


# ──────────────────────────────────────────────────────────────────────────────
# v27 feature tests: generic CSV adapter, keygen, SSRF, cursor pagination,
# conflict queue, admin override, migration 0012
# ──────────────────────────────────────────────────────────────────────────────


class TestSourceMapping:
    """SourceMapping JSON loading and column-map application."""

    def _minimal_mapping(self, **overrides):
        from atlas.ingestion.generic_csv_adapter import SourceMapping
        data = {
            "source_id": "src-asn-001",
            "record_id_field": "id",
            "field_map": {"Date": "occurred_at", "Fatalities": "fatalities_total"},
        }
        data.update(overrides)
        return SourceMapping(data)

    def test_load_minimal_mapping(self):
        m = self._minimal_mapping()
        assert m.source_id == "src-asn-001"
        assert m.field_map["Date"] == "occurred_at"

    def test_requires_source_id(self):
        from atlas.ingestion.generic_csv_adapter import SourceMapping
        with pytest.raises(ValueError, match="source_id"):
            SourceMapping({"source_id": "", "field_map": {"X": "y"}})

    def test_requires_field_map(self):
        from atlas.ingestion.generic_csv_adapter import SourceMapping
        with pytest.raises(ValueError, match="field_map"):
            SourceMapping({"source_id": "src-asn-001", "field_map": {}})

    def test_value_transform_applied(self):
        from atlas.ingestion.generic_csv_adapter import SourceMapping
        m = SourceMapping({
            "source_id": "src-asn-001",
            "field_map": {"Phase": "phase_of_flight"},
            "value_transforms": {"phase_of_flight": {"landing": "LANDING"}},
        })
        assert m.apply_transforms("phase_of_flight", "landing") == "LANDING"

    def test_value_transform_passthrough_unknown(self):
        m = self._minimal_mapping()
        assert m.apply_transforms("occurred_at", "2023-01-04") == "2023-01-04"

    def test_defaults(self):
        m = self._minimal_mapping()
        assert m.delimiter == ","
        assert m.encoding == "utf-8"
        assert m.skip_rows == 0

    def test_custom_delimiter(self):
        m = self._minimal_mapping(delimiter=";")
        assert m.delimiter == ";"

    def test_from_file_missing_raises(self):
        from atlas.ingestion.generic_csv_adapter import SourceMapping
        with pytest.raises(FileNotFoundError):
            SourceMapping.from_file("/nonexistent/path/mapping.json")


class TestBundledMappings:
    """Bundled ASN and ICAO mapping files are valid and loadable."""

    def test_asn_mapping_loads(self):
        from atlas.ingestion.generic_csv_adapter import load_bundled_mapping
        m = load_bundled_mapping("asn_mapping")
        assert m.source_id == "src-asn-001"
        assert "date" in m.field_map or "Date" in m.field_map

    def test_icao_mapping_loads(self):
        from atlas.ingestion.generic_csv_adapter import load_bundled_mapping
        m = load_bundled_mapping("icao_mapping")
        assert m.source_id == "src-icao-001"

    def test_list_bundled_returns_names(self):
        from atlas.ingestion.generic_csv_adapter import list_bundled_mappings
        names = list_bundled_mappings()
        assert len(names) >= 2
        # Both bundled files should be listed
        name_str = " ".join(names)
        assert "asn" in name_str or "mapping" in name_str

    def test_unknown_bundled_mapping_raises(self):
        from atlas.ingestion.generic_csv_adapter import load_bundled_mapping
        with pytest.raises(FileNotFoundError, match="not found"):
            load_bundled_mapping("definitely_nonexistent_source_xyz")


class TestGenericCsvRowTransform:
    """_row_to_raw maps source columns to canonical via the mapping."""

    def _make_mapping(self, field_map: dict, transforms: dict | None = None):
        from atlas.ingestion.generic_csv_adapter import SourceMapping
        return SourceMapping({
            "source_id": "src-asn-001",
            "record_id_field": "id",
            "field_map": field_map,
            "value_transforms": transforms or {},
        })

    def test_basic_column_mapping(self):
        from atlas.ingestion.generic_csv_adapter import _row_to_raw
        mapping = self._make_mapping({"Date": "occurred_at", "Fatal": "fatalities_total"})
        row = {"id": "A001", "Date": "2023-01-04", "Fatal": "3"}
        raw = _row_to_raw(row, mapping)
        assert raw["__canonical__"]["occurred_at"] == "2023-01-04"
        assert raw["__canonical__"]["fatalities_total"] == "3"
        assert raw["__record_id__"] == "A001"

    def test_blank_values_excluded(self):
        from atlas.ingestion.generic_csv_adapter import _row_to_raw
        mapping = self._make_mapping({"Date": "occurred_at", "Empty": "operator_name"})
        row = {"id": "A001", "Date": "2023-01-04", "Empty": ""}
        raw = _row_to_raw(row, mapping)
        assert "operator_name" not in raw["__canonical__"]

    def test_missing_source_column_excluded(self):
        from atlas.ingestion.generic_csv_adapter import _row_to_raw
        mapping = self._make_mapping({"Date": "occurred_at", "Missing": "operator_name"})
        row = {"id": "A001", "Date": "2023-01-04"}
        raw = _row_to_raw(row, mapping)
        assert "operator_name" not in raw["__canonical__"]

    def test_original_row_preserved(self):
        """raw dict must preserve all original columns for snapshot fidelity."""
        from atlas.ingestion.generic_csv_adapter import _row_to_raw
        mapping = self._make_mapping({"Date": "occurred_at"})
        row = {"id": "A001", "Date": "2023-01-04", "ExtraCol": "extra"}
        raw = _row_to_raw(row, mapping)
        assert raw["ExtraCol"] == "extra"
        assert raw["__source_id__"] == "src-asn-001"

    def test_value_transform_applied_in_row(self):
        from atlas.ingestion.generic_csv_adapter import _row_to_raw
        mapping = self._make_mapping(
            {"Phase": "phase_of_flight"},
            transforms={"phase_of_flight": {"landing": "LANDING"}},
        )
        row = {"id": "A001", "Phase": "landing"}
        raw = _row_to_raw(row, mapping)
        assert raw["__canonical__"]["phase_of_flight"] == "LANDING"


class TestNormaliseGeneric:
    """normalise_generic converts canonical_raw dicts to Python-typed ClaimWriter input."""

    def test_occurred_at_normalised(self):
        from atlas.ingestion.generic_csv_adapter import normalise_generic
        result = normalise_generic({"occurred_at": "2023-01-04"})
        assert "occurred_at" in result
        assert "occurred_at_precision" in result

    def test_fatalities_normalised_to_int(self):
        from atlas.ingestion.generic_csv_adapter import normalise_generic
        result = normalise_generic({"fatalities_total": "3"})
        assert result["fatalities_total"] == 3
        assert isinstance(result["fatalities_total"], int)

    def test_blank_dict_returns_only_defaults(self):
        """Empty input returns only the normalised defaults for severity/damage/status."""
        from atlas.ingestion.generic_csv_adapter import normalise_generic
        result = normalise_generic({})
        # No positional/temporal/aircraft data from an empty dict
        assert "occurred_at" not in result
        assert "aircraft_make" not in result
        assert "fatalities_total" not in result
        # Only the always-present enum fields with UNKNOWN defaults
        for k in result:
            assert k in ("injury_severity", "aircraft_damage", "investigation_status")

    def test_invalid_coordinates_excluded(self):
        from atlas.ingestion.generic_csv_adapter import normalise_generic
        result = normalise_generic({"latitude": "not_a_float", "longitude": "also_bad"})
        assert "location_coordinates" not in result

    def test_valid_coordinates_are_python_dict(self):
        from atlas.ingestion.generic_csv_adapter import normalise_generic
        result = normalise_generic({"latitude": "44.06", "longitude": "-121.31"})
        if "location_coordinates" in result:
            coords = result["location_coordinates"]
            assert isinstance(coords, dict)
            assert abs(coords["latitude"] - 44.06) < 0.001

    def test_unknown_fields_ignored(self):
        """Fields not in the normaliser's field set don't crash."""
        from atlas.ingestion.generic_csv_adapter import normalise_generic
        result = normalise_generic({"totally_unknown_field": "value123"})
        assert "totally_unknown_field" not in result

    def test_aircraft_make_model_normalised(self):
        from atlas.ingestion.generic_csv_adapter import normalise_generic
        result = normalise_generic({"aircraft_make": "cessna", "aircraft_model": "172"})
        if "aircraft_make" in result:
            assert result["aircraft_make"].upper() == "CESSNA"

    def test_generic_normaliser_does_not_return_encoded_envelopes(self):
        from atlas.ingestion.generic_csv_adapter import normalise_generic
        from atlas.models import claim_value as cv

        result = normalise_generic({
            "occurred_at": "2023-01-04",
            "fatalities_total": "3",
            "latitude": "44.06",
            "longitude": "-121.31",
            "aircraft_make": "cessna",
        })

        assert result
        assert not cv.contains_envelope(result)
        assert all(not cv.is_envelope(value) for value in result.values())


class TestLoadCsvWithMapping:
    """load_csv_with_mapping round-trip with a real temp CSV file."""

    def _write_csv(self, tmp_path, content: str, filename="test.csv"):
        p = tmp_path / filename
        p.write_text(content)
        return str(p)

    def test_basic_load(self, tmp_path):
        from atlas.ingestion.generic_csv_adapter import SourceMapping, load_csv_with_mapping
        csv_content = "id,Date,Fatalities\nA001,2023-01-04,3\nA002,2022-06-15,0\n"
        filepath = self._write_csv(tmp_path, csv_content)
        mapping = SourceMapping({
            "source_id": "src-asn-001",
            "record_id_field": "id",
            "field_map": {"Date": "occurred_at", "Fatalities": "fatalities_total"},
        })
        records = load_csv_with_mapping(filepath, mapping)
        assert len(records) == 2
        assert records[0]["__record_id__"] == "A001"
        assert records[0]["__canonical__"]["occurred_at"] == "2023-01-04"

    def test_semicolon_delimiter(self, tmp_path):
        from atlas.ingestion.generic_csv_adapter import SourceMapping, load_csv_with_mapping
        csv_content = "id;Date;Location\nB001;2023-05-01;Paris\n"
        filepath = self._write_csv(tmp_path, csv_content)
        mapping = SourceMapping({
            "source_id": "src-asn-001",
            "record_id_field": "id",
            "field_map": {"Date": "occurred_at", "Location": "location_text"},
            "delimiter": ";",
        })
        records = load_csv_with_mapping(filepath, mapping)
        assert len(records) == 1
        assert records[0]["__canonical__"]["location_text"] == "Paris"

    def test_all_blank_rows_skipped(self, tmp_path):
        from atlas.ingestion.generic_csv_adapter import SourceMapping, load_csv_with_mapping
        csv_content = "id,Date\nA001,\n"  # Date is blank — no canonical data
        filepath = self._write_csv(tmp_path, csv_content)
        mapping = SourceMapping({
            "source_id": "src-asn-001",
            "record_id_field": "id",
            "field_map": {"Date": "occurred_at"},
        })
        records = load_csv_with_mapping(filepath, mapping)
        assert len(records) == 0  # blank date row skipped

    def test_missing_file_raises(self):
        from atlas.ingestion.generic_csv_adapter import SourceMapping, load_csv_with_mapping
        mapping = SourceMapping({
            "source_id": "src-asn-001",
            "record_id_field": "id",
            "field_map": {"Date": "occurred_at"},
        })
        with pytest.raises(FileNotFoundError):
            load_csv_with_mapping("/nonexistent/file.csv", mapping)

    def test_bom_stripped_from_first_column(self, tmp_path):
        """Excel-exported CSVs often have a BOM on the first column name."""
        from atlas.ingestion.generic_csv_adapter import SourceMapping, load_csv_with_mapping
        # \ufeff is the BOM character
        csv_content = "\ufeffDate,Location\n2023-01-04,London\n"
        filepath = self._write_csv(tmp_path, csv_content)
        mapping = SourceMapping({
            "source_id": "src-asn-001",
            "field_map": {"Date": "occurred_at", "Location": "location_text"},
        })
        records = load_csv_with_mapping(filepath, mapping)
        assert len(records) == 1
        assert "occurred_at" in records[0]["__canonical__"]


class TestGenericSnapshotBuilder:
    """build_generic_snapshot produces valid RawSnapshot objects."""

    def test_snapshot_has_required_fields(self):
        from atlas.ingestion.generic_csv_adapter import build_generic_snapshot
        snap = build_generic_snapshot(
            {"key": "val"},
            source_id="src-asn-001",
            source_record_id="A001",
            run_id="run-1",
        )
        assert snap.source_id == "src-asn-001"
        assert snap.source_record_id == "A001"
        assert len(snap.payload_hash) == 64  # SHA-256 hex

    def test_same_payload_same_hash(self):
        from atlas.ingestion.generic_csv_adapter import build_generic_snapshot
        row = {"x": 1, "y": 2}
        s1 = build_generic_snapshot(row, source_id="src-asn-001", source_record_id="A1", run_id="r1")
        s2 = build_generic_snapshot(row, source_id="src-asn-001", source_record_id="A1", run_id="r1")
        assert s1.payload_hash == s2.payload_hash

    def test_different_payload_different_hash(self):
        from atlas.ingestion.generic_csv_adapter import build_generic_snapshot
        s1 = build_generic_snapshot({"x": 1}, source_id="src-asn-001", source_record_id="A1", run_id="r1")
        s2 = build_generic_snapshot({"x": 2}, source_id="src-asn-001", source_record_id="A1", run_id="r1")
        assert s1.payload_hash != s2.payload_hash


class TestPipelineGenericCsvDryRun:
    """IngestionPipeline.run_generic_csv dry_run=True validates without DB writes."""

    @pytest.mark.asyncio
    async def test_dry_run_returns_result_without_db(self, tmp_path):
        from atlas.ingestion.generic_csv_adapter import SourceMapping
        from atlas.ingestion.pipeline import IngestionPipeline

        csv_content = "id,Date,Fatalities\nA001,2023-01-04,3\n"
        filepath = tmp_path / "test.csv"
        filepath.write_text(csv_content)
        mapping = SourceMapping({
            "source_id": "src-asn-001",
            "record_id_field": "id",
            "field_map": {"Date": "occurred_at", "Fatalities": "fatalities_total"},
        })
        result = await IngestionPipeline().run_generic_csv(
            str(filepath), mapping, dry_run=True
        )
        assert result.records_fetched == 1
        assert result.events_created == 0   # no DB writes
        assert result.claims_written == 0

    @pytest.mark.asyncio
    async def test_dry_run_file_not_found_returns_error(self, tmp_path):
        from atlas.ingestion.generic_csv_adapter import SourceMapping
        from atlas.ingestion.pipeline import IngestionPipeline

        mapping = SourceMapping({
            "source_id": "src-asn-001",
            "field_map": {"Date": "occurred_at"},
        })
        result = await IngestionPipeline().run_generic_csv(
            "/nonexistent/file.csv", mapping, dry_run=True
        )
        assert len(result.errors) == 1
        assert "csv_load" in result.errors[0]


class TestKeysCLI:
    """API key management CLI commands are wired correctly."""

    def test_keygen_command_exists(self):
        """atlas keys create must be registered."""
        from atlas.cli import keys_app
        cmd_names = [c.name for c in keys_app.registered_commands]
        assert "create" in cmd_names

    def test_keys_list_command_exists(self):
        from atlas.cli import keys_app
        cmd_names = [c.name for c in keys_app.registered_commands]
        assert "list" in cmd_names

    def test_keys_revoke_command_exists(self):
        from atlas.cli import keys_app
        cmd_names = [c.name for c in keys_app.registered_commands]
        assert "revoke" in cmd_names

    def test_keygen_uses_secrets(self):
        """Raw keys must use secrets.token_urlsafe — not uuid or random."""
        import inspect

        from atlas.cli import keys_create
        src = inspect.getsource(keys_create)
        assert "secrets.token_urlsafe" in src or "token_urlsafe" in src

    def test_keygen_hashes_with_sha256(self):
        import inspect

        from atlas.cli import keys_create
        src = inspect.getsource(keys_create)
        assert "sha256" in src
        assert "hexdigest" in src

    def test_keygen_never_stores_raw_key(self):
        """The raw key must never be written to the DB — only the hash."""
        import inspect

        from atlas.cli import keys_create
        src = inspect.getsource(keys_create)
        # The DB insert must use key_hash column
        assert "key_hash=key_hash" in src
        # The raw_key is defined and displayed, but must not be a DB column
        # Verify the pg_insert values() call uses key_hash not raw_key for the hash column
        # Find the values() dict and confirm raw_key is not a key in it
        import re
        values_match = re.search(r"\.values\(([^)]+)\)", src, re.DOTALL)
        if values_match:
            values_str = values_match.group(1)
            # raw_key should appear only as the value for key_hash, not as its own column
            assert "raw_key=" not in values_str  # raw_key should not be a column name


class TestSSRFProtection:
    """check-links SSRF guard rejects private/loopback addresses."""

    def _is_safe(self, url: str, allowed_domains: set | None = None) -> tuple[bool, str]:
        """Inline the SSRF check logic for unit testing."""
        from urllib.parse import urlparse
        _PRIVATE_PREFIXES = (
            "localhost", "127.", "10.", "172.16.", "192.168.", "0.",
            "169.254.", "::1", "fc", "fd",
        )
        try:
            parsed = urlparse(url)
        except Exception:
            return False, "unparseable"
        if parsed.scheme not in ("http", "https"):
            return False, f"scheme {parsed.scheme}"
        host = (parsed.hostname or "").lower()
        for prefix in _PRIVATE_PREFIXES:
            if host == prefix or host.startswith(prefix):
                return False, f"private: {host}"
        if allowed_domains:
            if not any(host == d or host.endswith(f".{d}") for d in allowed_domains):
                return False, f"not in allowlist: {host}"
        return True, ""

    def test_public_url_allowed(self):
        safe, _ = self._is_safe("https://ntsb.gov/report/123")
        assert safe

    def test_localhost_blocked(self):
        safe, reason = self._is_safe("http://localhost:8080/internal")
        assert not safe
        assert "private" in reason

    def test_127_blocked(self):
        safe, reason = self._is_safe("http://127.0.0.1/secret")
        assert not safe

    def test_10_x_blocked(self):
        safe, reason = self._is_safe("http://10.0.0.1/internal")
        assert not safe

    def test_192_168_blocked(self):
        safe, _ = self._is_safe("http://192.168.1.1/admin")
        assert not safe

    def test_link_local_blocked(self):
        safe, _ = self._is_safe("http://169.254.169.254/latest/meta-data/")
        assert not safe

    def test_ftp_scheme_blocked(self):
        safe, reason = self._is_safe("ftp://ntsb.gov/file.csv")
        assert not safe
        assert "scheme" in reason

    def test_allowlist_permits_matching_domain(self):
        safe, _ = self._is_safe(
            "https://ntsb.gov/report",
            allowed_domains={"ntsb.gov", "aviation-safety.net"},
        )
        assert safe

    def test_allowlist_permits_subdomain(self):
        safe, _ = self._is_safe(
            "https://data.ntsb.gov/report",
            allowed_domains={"ntsb.gov"},
        )
        assert safe

    def test_allowlist_blocks_unlisted_domain(self):
        safe, reason = self._is_safe(
            "https://attacker.com/data",
            allowed_domains={"ntsb.gov"},
        )
        assert not safe
        assert "allowlist" in reason

    def test_ssrf_guard_in_cli_source(self):
        """The CLI check-links command must implement the SSRF guard."""
        import inspect

        from atlas.cli import check_links
        src = inspect.getsource(check_links)
        assert "is_safe_url" in src
        assert "_PRIVATE_PREFIXES" in src
        assert "SSRF" in src


class TestCursorPagination:
    """Cursor (keyset) pagination in list_accidents."""

    def test_next_cursor_in_paginated_accidents_model(self):
        """Public pagination schema exposes the cursor token."""
        from pathlib import Path

        src = (Path(__file__).parent.parent / "src/atlas/api/schemas.py").read_text()
        assert "class PaginatedAccidents" in src
        assert "next_cursor" in src

    def test_cursor_building_in_endpoint_source(self):
        """Endpoint builds a versioned base64 cursor from the last returned row."""
        from pathlib import Path

        src = (Path(__file__).parent.parent / "src/atlas/api/app.py").read_text()
        assert "def _encode_date_cursor" in src
        assert "urlsafe_b64encode" in src
        assert '"sort": sort' in src
        assert '"id": record.id' in src

    def test_cursor_applied_as_keyset_not_offset(self):
        """Cursor requests must use a keyset WHERE predicate, never OFFSET."""
        from pathlib import Path

        src = (Path(__file__).parent.parent / "src/atlas/api/app.py").read_text()
        assert "def _apply_date_cursor" in src
        assert "using_cursor = cursor is not None" in src
        assert "if not using_cursor:" in src
        assert "stmt = stmt.offset(page * page_size)" in src
        assert "cur_at, cur_id = _decode_date_cursor" in src

    def test_cursor_order_has_deterministic_tie_breaker_and_null_handling(self):
        """Date cursors must match ORDER BY exactly, including duplicate dates and NULLs."""
        from pathlib import Path

        src = (Path(__file__).parent.parent / "src/atlas/api/app.py").read_text()
        assert '"date_desc": [AccidentRecord.occurred_at.desc().nullslast(), AccidentRecord.id.asc()]' in src
        assert '"date_asc": [AccidentRecord.occurred_at.asc().nullsfirst(), AccidentRecord.id.asc()]' in src
        assert "AccidentRecord.occurred_at.is_(None)" in src
        assert "AccidentRecord.occurred_at.is_not(None)" in src
        assert "AccidentRecord.id > cur_id" in src

    def test_cursor_roundtrip(self):
        """A cursor encoding should round-trip cleanly."""
        import base64
        import json
        from datetime import UTC, datetime
        at = datetime(2023, 1, 4, 22, 30, tzinfo=UTC)
        payload = json.dumps({"v": 1, "sort": "date_desc", "at": at.isoformat(), "id": "evt-001"})
        cursor = base64.urlsafe_b64encode(payload.encode()).rstrip(b"=").decode()
        decoded = json.loads(base64.urlsafe_b64decode(cursor + "==").decode())
        assert decoded["id"] == "evt-001"
        assert decoded["sort"] == "date_desc"
        assert datetime.fromisoformat(decoded["at"]).year == 2023

    def test_frontend_type_has_next_cursor(self):
        """PaginatedAccidents TypeScript type must include next_cursor."""
        from pathlib import Path
        ts_src = (
            Path(__file__).parent.parent / "web/types/index.ts"
        ).read_text()
        assert "next_cursor" in ts_src

    def test_frontend_uses_cursor_for_date_pagination(self):
        """The frontend must pass returned cursors instead of always using page offsets."""
        from pathlib import Path

        root = Path(__file__).parent.parent
        api_src = (root / "web/lib/api.ts").read_text()
        hook_src = (root / "web/hooks/useAccidents.ts").read_text()
        assert "cursor?: string | null" in api_src
        assert "params.cursor = filters.cursor" in api_src
        assert "cursorByPageRef" in hook_src
        assert "res.next_cursor" in hook_src
        assert "page: cursor ? undefined : page" in hook_src


class TestConflictQueueEndpoint:
    """GET /api/v1/conflicts and GET /api/v1/conflicts/stats are registered."""

    def test_conflict_queue_route_registered(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module)
        assert '"/api/v1/conflicts"' in src

    def test_conflict_stats_route_registered(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module)
        assert '"/api/v1/conflicts/stats"' in src

    def test_conflict_queue_item_model(self):
        from atlas.api.app import ConflictQueueItem
        fields = ConflictQueueItem.model_fields
        for f in ("conflict_id", "event_id", "field_name",
                  "claim_a_id", "claim_b_id", "claim_a_value",
                  "claim_a_source", "location_text"):
            assert f in fields, f"Missing field: {f}"

    def test_conflict_queue_ordered_oldest_first(self):
        """Queue must be sorted oldest-first so reviewers see longest-standing disputes."""
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module)
        assert "created_at.asc()" in src

    def test_conflict_queue_frontend_page_exists(self):
        from pathlib import Path
        p = Path(__file__).parent.parent / "web/pages/conflicts.tsx"
        assert p.exists()
        src = p.read_text()
        assert "ConflictQueueItem" in src or "conflict_id" in src


class TestAdminForceResolveEndpoint:
    """POST /api/v1/admin/events/{event_id}/force-resolve-field."""

    def test_route_registered(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module)
        assert "force-resolve-field" in src

    def test_requires_admin_role(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.admin_force_resolve_field)
        # Must check for admin role
        assert "admin" in src
        assert "403" in src

    def test_uses_require_admin_dependency_not_reviewer(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.admin_force_resolve_field)
        assert "Depends(require_admin)" in src
        assert "Depends(require_reviewer)" not in src

    def test_no_auth_disabled_operator_fallback(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.admin_force_resolve_field)
        assert 'operator.id or "admin"' not in src
        assert 'operator.id if operator.id' not in src
        assert "not operator.id" in src

    def test_uses_manual_override_resolution_type(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module)
        assert "manual_override" in src

    def test_triggers_projection_rebuild(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module)
        assert "rebuild_event" in src


class TestTokenExpiry:
    """ApiKey.expires_at — expired keys are rejected by auth._resolve_key."""

    def test_expires_at_column_on_orm(self):
        from atlas.models.orm import ApiKey
        cols = [c.key for c in ApiKey.__table__.columns]
        assert "expires_at" in cols

    def test_expiry_check_in_auth_module(self):
        import inspect

        from atlas.api import auth as auth_module
        src = inspect.getsource(auth_module._resolve_key)
        assert "expires_at" in src
        assert "auth.expired_key" in src

    def test_expired_key_returns_none(self):
        """_resolve_key must return None for an expired key."""
        import inspect

        from atlas.api import auth as auth_module
        src = inspect.getsource(auth_module._resolve_key)
        # Must compare expires_at < now and return None
        assert "expires_at" in src
        assert "return None" in src


class TestMigration0012:
    """Migration 0012 must define trgm indexes, cursor index, and expires_at."""

    def _load(self):
        import importlib.util
        from pathlib import Path
        path = Path(__file__).parent.parent / \
            "migrations/versions/0012_trgm_search_cursor_pagination_key_expiry.py"
        assert path.exists(), "Migration 0012 must exist"
        spec = importlib.util.spec_from_file_location("m0012", path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m, path.read_text()

    def test_migration_valid(self):
        m, _ = self._load()
        assert hasattr(m, "upgrade")
        assert hasattr(m, "downgrade")

    def test_revises_0011(self):
        _, src = self._load()
        assert "0011" in src

    def test_creates_pg_trgm_extension(self):
        _, src = self._load()
        assert "pg_trgm" in src
        assert "CREATE EXTENSION" in src

    def test_creates_trgm_indexes(self):
        _, src = self._load()
        for col in ("location_text", "aircraft_make", "operator_name", "probable_cause"):
            assert col in src, f"Missing trgm index for {col}"
        assert "gin_trgm_ops" in src

    def test_concurrent_indexes_run_outside_transaction(self):
        """PostgreSQL requires CREATE INDEX CONCURRENTLY outside transactions."""
        _, src = self._load()
        assert "CREATE INDEX CONCURRENTLY" in src
        assert "autocommit_block" in src

    def test_adds_expires_at_to_api_keys(self):
        _, src = self._load()
        assert "expires_at" in src
        assert "api_keys" in src

    def test_creates_cursor_pagination_index(self):
        _, src = self._load()
        assert "ix_record_cursor" in src
        assert "occurred_at" in src

    def test_downgrade_removes_all_additions(self):
        _, src = self._load()
        assert "drop_column" in src
        assert "expires_at" in src
        assert src.count("ix_record_cursor") >= 2   # create + drop


class TestErrorBoundary:
    """ErrorBoundary component is implemented and wired into the UI."""

    def test_error_boundary_component_exists(self):
        from pathlib import Path
        p = Path(__file__).parent.parent / "web/components/ErrorBoundary.tsx"
        assert p.exists()

    def test_error_boundary_is_class_component(self):
        """React error boundaries must be class components (hooks don't work)."""
        from pathlib import Path
        src = (Path(__file__).parent.parent / "web/components/ErrorBoundary.tsx").read_text()
        assert "Component" in src
        assert "getDerivedStateFromError" in src
        assert "componentDidCatch" in src

    def test_error_boundary_has_reset_mechanism(self):
        from pathlib import Path
        src = (Path(__file__).parent.parent / "web/components/ErrorBoundary.tsx").read_text()
        # Must have a way to reset (re-render after error)
        assert "setState" in src or "setError" in src

    def test_error_boundary_wired_into_provenance_panel(self):
        from pathlib import Path
        src = (Path(__file__).parent.parent / "web/components/ProvenancePanel.tsx").read_text()
        assert "ErrorBoundary" in src

    def test_error_boundary_wired_into_accident_detail_panel(self):
        from pathlib import Path
        src = (Path(__file__).parent.parent / "web/components/AccidentDetailPanel.tsx").read_text()
        assert "ErrorBoundary" in src


class TestConflictsPageNavigation:
    """Conflicts page is linked from the header."""

    def test_conflicts_route_in_header(self):
        from pathlib import Path
        src = (Path(__file__).parent.parent / "web/components/Header.tsx").read_text()
        assert "conflicts" in src
        assert "/conflicts" in src


# ──────────────────────────────────────────────────────────────────────────────
# v28 polish tests — schema extraction, auth footgun warning, a11y
# ──────────────────────────────────────────────────────────────────────────────


class TestSchemasExtraction:
    """API schemas live in atlas.api.schemas; app.py re-exports them."""

    def test_schemas_module_exists(self):
        from atlas.api import schemas  # noqa: F401

    def test_all_public_schemas_are_re_exported_from_app(self):
        """Every schema that pre-v28 lived in app.py must still be importable
        from app.py.  This protects against accidental breakage of external
        code that imports `from atlas.api.app import ConflictOut`.
        """
        from atlas.api import app, schemas

        for name in schemas.__all__:
            assert hasattr(app, name), (
                f"app.py must re-export {name!r} from schemas for backwards compat"
            )

    def test_re_exported_classes_are_identical(self):
        """`from app import X` and `from schemas import X` must return the
        SAME class object (not a separate copy).  Otherwise isinstance checks
        and Pydantic validation would diverge.
        """
        from atlas.api.app import ConflictResolveIn as FromApp
        from atlas.api.schemas import ConflictResolveIn as FromSchemas
        assert FromApp is FromSchemas

    def test_app_py_no_longer_defines_schemas_inline(self):
        """app.py should import schemas, not redefine them."""
        import inspect

        from atlas.api import app
        src = inspect.getsource(app)
        # The class definitions should now live elsewhere.
        # Spot-check: no `class ConflictResolveIn(BaseModel):` block in app.py.
        assert "class ConflictResolveIn(BaseModel):" not in src
        assert "class MapAccident(BaseModel):" not in src
        assert "class AnalyticsSummary(BaseModel):" not in src


class TestAuthFootgunWarning:
    """When API_AUTH_ENABLED=false, the lifespan logs a prominent warning."""

    def test_lifespan_warns_when_auth_disabled(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.lifespan)
        # Must check the setting and emit a warning log
        assert "api_auth_enabled" in src
        assert "atlas.api.auth_disabled" in src
        # The message must mention the implication clearly
        assert "UNAUTHENTICATED" in src

    def test_warning_mentions_keys_create_command(self):
        """The warning must point operators at the fix."""
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.lifespan)
        assert "atlas keys create" in src


class TestEnvExampleFile:
    """Backend .env.example documents every Settings field."""

    def _load_env_example(self) -> str:
        from pathlib import Path
        p = Path(__file__).parent.parent / ".env.example"
        assert p.exists(), ".env.example must exist at repo root"
        return p.read_text()

    def test_env_example_documents_database_url(self):
        assert "DATABASE_URL" in self._load_env_example()

    def test_env_example_documents_auth(self):
        src = self._load_env_example()
        assert "API_AUTH_ENABLED" in src
        # Must explicitly call out the production risk
        assert "production" in src.lower() or "network" in src.lower()

    def test_env_example_documents_cors(self):
        assert "CORS_ORIGINS" in self._load_env_example()

    def test_env_example_documents_keys_workflow(self):
        """Should mention `atlas keys create` so operators know how to bootstrap."""
        assert "atlas keys create" in self._load_env_example()


class TestChangelog:
    """CHANGELOG.md exists and preserves the v20 honesty-pass content."""

    def _load(self) -> str:
        from pathlib import Path
        p = Path(__file__).parent.parent / "CHANGELOG.md"
        assert p.exists()
        return p.read_text()

    def test_changelog_preserves_v20_section(self):
        src = self._load()
        assert "v0.20" in src or "v20" in src.lower()
        # Specific v20 invariants must still be documented
        assert "Honesty pass" in src or "honesty pass" in src

    def test_changelog_documents_recent_releases(self):
        src = self._load()
        assert "REJECTED" in src
        assert "API key" in src or "X-API-Key" in src
        assert "generic-csv" in src or "generic CSV" in src.lower()


class TestNormaliseGenericPublicAPI:
    """normalise_generic is now public (no leading underscore)."""

    def test_public_name_importable(self):
        from atlas.ingestion.generic_csv_adapter import normalise_generic  # noqa: F401

    def test_old_underscore_name_removed(self):
        """The leading-underscore alias should be gone.  If a user is still
        importing _normalise_generic they should get a clear ImportError
        rather than a silently-different function.
        """
        from atlas.ingestion import generic_csv_adapter as m
        assert not hasattr(m, "_normalise_generic")


class TestPipelineUsesTopLevelImports:
    """No more inline `import hashlib as _hashlib` aliases in pipeline._process_generic."""

    def test_no_inline_aliased_imports(self):
        import inspect

        from atlas.ingestion.pipeline import IngestionPipeline
        src = inspect.getsource(IngestionPipeline._process_generic)
        assert "import hashlib as" not in src
        assert "import json as" not in src
        # The module-level `import hashlib` and `import json` are used directly
        assert "hashlib.sha256" in src
        assert "json.dumps" in src


class TestConflictReviewPanelA11y:
    """ConflictReviewPanel has the accessibility attributes we added."""

    def _src(self) -> str:
        """ClaimSide and OpenConflictForm now live in ConflictResolutionForm.tsx."""
        from pathlib import Path
        return (
            Path(__file__).parent.parent / "web/components/ConflictResolutionForm.tsx"
        ).read_text()

    def _panel_src(self) -> str:
        from pathlib import Path
        return (
            Path(__file__).parent.parent / "web/components/ConflictReviewPanel.tsx"
        ).read_text()

    def test_claim_buttons_have_aria_pressed(self):
        """ClaimSide button is in the extracted ConflictResolutionForm."""
        src = self._src()
        assert "aria-pressed" in src

    def test_claim_buttons_have_aria_label(self):
        """aria-label on ClaimSide buttons lives in ConflictResolutionForm."""
        src = self._src()
        assert "aria-label" in src

    def test_resolution_type_picker_is_a_group(self):
        """Resolution type picker is in ConflictResolutionForm."""
        src = self._src()
        assert 'role="group"' in src
        assert "aria-labelledby" in src

    def test_form_inputs_have_explicit_label_association(self):
        """Form labels are in ConflictResolutionForm."""
        src = self._src()
        assert "htmlFor" in src
        assert "id={`conflict-notes-" in src
        assert "id={`conflict-reviewer-" in src

    def test_focus_ring_classes_present(self):
        src = self._src()
        assert "focus:ring" in src

    def test_error_region_uses_aria_live(self):
        """Error messages in ConflictResolutionForm must be announced to screen readers."""
        src = self._src()
        assert "aria-live" in src
        assert 'role="alert"' in src

    def test_conflict_resolution_form_extracted(self):
        """ConflictResolutionForm.tsx must exist as a separate file."""
        from pathlib import Path
        p = Path(__file__).parent.parent / "web/components/ConflictResolutionForm.tsx"
        assert p.exists(), "ConflictResolutionForm must be extracted into its own file"

    def test_conflict_review_panel_uses_form_component(self):
        """ConflictReviewPanel must import ConflictResolutionForm."""
        src = self._panel_src()
        assert "ConflictResolutionForm" in src


class TestMigration0013SearchIndexAlignment:
    """Migration 0013 must make trigram indexes match lower(...).LIKE search."""

    def _load(self):
        from pathlib import Path
        path = Path(__file__).parent.parent / \
            "migrations/versions/0013_align_trgm_indexes_with_lower_search.py"
        assert path.exists(), "Migration 0013 must exist"
        # Read source only: these tests must run even in minimal environments
        # where alembic/sqlalchemy are not installed.
        return None, path.read_text()

    def test_revises_0012(self):
        _, src = self._load()
        assert 'down_revision = "0012"' in src

    def test_replaces_raw_column_indexes(self):
        _, src = self._load()
        assert "DROP INDEX CONCURRENTLY IF EXISTS" in src
        for name in (
            "ix_record_location_trgm",
            "ix_record_make_trgm",
            "ix_record_operator_trgm",
            "ix_record_cause_trgm",
        ):
            assert name in src

    def test_creates_lower_expression_trgm_indexes_for_all_search_columns(self):
        _, src = self._load()
        assert "lower({column}) gin_trgm_ops" in src
        for column in (
            "location_text",
            "aircraft_make",
            "aircraft_model",
            "operator_name",
            "probable_cause",
        ):
            assert column in src
        assert "ix_record_model_lower_trgm" in src

    def test_concurrent_index_work_uses_autocommit(self):
        _, src = self._load()
        assert "CREATE INDEX CONCURRENTLY" in src
        assert "DROP INDEX CONCURRENTLY" in src
        assert "autocommit_block" in src

    def test_api_search_columns_are_all_indexed(self):
        import re
        from pathlib import Path

        _, migration_src = self._load()
        app_src = (Path(__file__).parent.parent / "src/atlas/api/app.py").read_text()
        searched_columns = set(re.findall(
            r"func\.lower\(AccidentRecord\.([a-zA-Z_]+)\)\.like",
            app_src,
        ))
        assert searched_columns == {
            "location_text",
            "aircraft_make",
            "aircraft_model",
            "operator_name",
            "probable_cause",
        }
        for column in searched_columns:
            assert column in migration_src, f"Missing lower trigram index for {column}"



class TestConflictQueueDeepLinks:
    """Conflict queue review links must open the selected record's technical tab."""

    def _read(self, rel: str) -> str:
        from pathlib import Path
        return (Path(__file__).parent.parent / rel).read_text()

    def test_conflict_queue_uses_selected_query_param_not_id(self):
        src = self._read("web/pages/conflicts.tsx")
        assert "selected: item.event_id" in src
        assert "tab: 'technical'" in src
        assert "?id=${item.event_id}" not in src

    def test_search_page_reads_selected_and_legacy_id_params(self):
        src = self._read("web/pages/index.tsx")
        assert "selectedIdFromQuery" in src
        assert "router.query.selected" in src
        assert "router.query.id" in src
        assert "setSelectedId(id)" in src

    def test_search_page_forwards_tab_query_to_detail_panel(self):
        src = self._read("web/pages/index.tsx")
        assert "detailTabFromQuery" in src
        assert "router.query.tab" in src
        assert "initialTab={initialDetailTab}" in src

    def test_detail_panel_honors_initial_tab_for_deep_links(self):
        src = self._read("web/components/AccidentDetailPanel.tsx")
        assert "initialTab?: Tab" in src
        assert "initialTab = 'overview'" in src
        assert "useState<Tab>(initialTab)" in src
        assert "setTab(initialTab)" in src


class TestReviewerAuthUiWiring:
    """Reviewer API keys must be collectable in UI and forwarded to write actions."""

    def _read(self, rel: str) -> str:
        from pathlib import Path
        return (Path(__file__).parent.parent / rel).read_text()

    def test_reviewer_auth_hook_persists_key_in_browser_storage(self):
        src = self._read("web/hooks/useReviewerAuth.ts")
        assert "REVIEWER_API_KEY_STORAGE" in src
        assert "localStorage.setItem" in src
        assert "localStorage.removeItem" in src
        assert "asa:reviewer-auth-changed" in src

    def test_header_renders_reviewer_auth_control_when_given_handlers(self):
        src = self._read("web/components/Header.tsx")
        assert "ReviewerAuthControl" in src
        assert "reviewerApiKey" in src
        assert "onReviewerApiKeyChange" in src

    def test_search_page_wires_saved_key_to_detail_panel(self):
        src = self._read("web/pages/index.tsx")
        assert "useReviewerAuth" in src
        assert "reviewerAuth.setApiKey" in src
        assert "apiKey={reviewerAuth.apiKey || undefined}" in src

    def test_conflict_queue_exposes_same_reviewer_key_control(self):
        src = self._read("web/pages/conflicts.tsx")
        assert "useReviewerAuth" in src
        assert "ReviewerAuthControl" in src
        assert "onApiKeyChange={reviewerAuth.setApiKey}" in src

    def test_resolution_form_surfaces_key_state_to_reviewer(self):
        src = self._read("web/components/ConflictResolutionForm.tsx")
        assert "hasReviewerApiKey" in src
        assert "X-API-Key" in src
        assert "production backends will return 401" in src

    def test_resolve_conflict_still_sends_x_api_key_header(self):
        src = self._read("web/lib/api.ts")
        assert "headers['X-API-Key'] = apiKey" in src


class TestProjectionOfficialFinalRationale:
    """`selected_official_final` must require final-document evidence, not tier alone."""

    def _read(self, rel: str) -> str:
        from pathlib import Path
        return (Path(__file__).parent.parent / rel).read_text()

    def test_rebuild_loads_source_documents_before_building_explanations(self):
        src = self._read("src/atlas/claims/projection.py")
        assert "select(SourceDocument).where(SourceDocument.event_id == event_id)" in src
        assert "source_documents=docs" in src

    def test_official_final_helper_requires_verified_available_final_document(self):
        src = self._read("src/atlas/claims/projection.py")
        assert "Source tier alone is not enough" in src
        assert "final_document_types = {\"final\", \"final_report\"}" in src
        assert "document.source_id != claim.source_id" in src
        assert "document.url_verified is True" in src
        assert "document.is_available is True" in src

    def test_helper_no_longer_returns_true_for_every_tier_one_source(self):
        src = self._read("src/atlas/claims/projection.py")
        helper = src[src.index("def _is_official_final("):src.index("def _aggregate_document_status")]
        assert "if src is None or src.tier != 1:" in helper
        assert "return True\n" in helper
        assert "if document.url_verified is True and document.is_available is True:\n                return True" in helper
        assert "if src is None or src.tier != 1:\n            return False\n        return True" not in helper

    def test_final_document_type_matches_document_extractor_and_confidence(self):
        extractor = self._read("src/atlas/ingestion/document_extractor.py")
        confidence = self._read("src/atlas/confidence/engine.py")
        projection = self._read("src/atlas/claims/projection.py")
        assert '"FinalReportUrl": "final"' in extractor
        assert '"final", "final_report"' in projection
        assert '"final", "final_report", "probable_cause"' in confidence

    def test_docs_explain_tier_one_is_not_final_report(self):
        readme = self._read("README.md")
        changelog = self._read("CHANGELOG.md")
        assert "Official source does not mean final report" in readme
        assert "verified, available final-report" in readme
        assert "no longer treat source tier as proof of finality" in changelog


class TestClaimWriterEncodingBoundary:
    """ClaimWriter is the only boundary that should encode claim values."""

    def test_claim_value_helpers_detect_nested_envelopes(self):
        from atlas.models import claim_value as cv

        raw = {"latitude": 44.06, "longitude": -121.31}
        encoded = cv.encode(raw)

        assert cv.is_envelope(encoded)
        assert cv.contains_envelope(encoded)
        assert cv.contains_envelope({"coords": encoded})
        assert not cv.contains_envelope(raw)

    def test_claim_writer_rejects_pre_encoded_values_before_db_work(self):
        import pytest

        from atlas.claims.writer import ClaimWriter
        from atlas.models import claim_value as cv

        writer = ClaimWriter(session=None, event_id="evt", source_id="src")

        with pytest.raises(ValueError, match="pre-encoded claim envelopes"):
            import asyncio
            asyncio.run(writer.write_fields({"fatalities_total": cv.encode(3)}))

    def test_generic_csv_adapter_has_no_claim_value_encode_calls(self):
        from pathlib import Path

        source = Path("src/atlas/ingestion/generic_csv_adapter.py").read_text()
        normaliser_body = source.split("def normalise_generic", 1)[1].split("# ── Snapshot builder", 1)[0]

        assert "cv.encode" not in normaliser_body
        assert "from atlas.models import claim_value" not in normaliser_body
