from __future__ import annotations

from datetime import UTC, date, datetime

from atlas.api.schemas import AccidentProvenance, DataQualityIssueOut, DuplicateCandidateOut
from atlas.ingestion.deduplicator import DuplicateDetector
from atlas.retention.archive import ArchiveRunResult, ArchiveTableResult


def test_duplicate_detector_scores_registration_date_and_location() -> None:
    incoming = {
        "event_id": "incoming:asn:1",
        "occurred_at": date(2020, 1, 1),
        "latitude": 26.1,
        "longitude": -80.1,
        "aircraft_registration": "N12345",
        "aircraft_make": "Cessna",
        "aircraft_model": "172S",
        "operator_name": "Fixture Air",
        "fatalities_total": 1,
    }
    existing = [{
        "event_id": "evt-existing",
        "occurred_at": date(2020, 1, 1),
        "latitude": 26.11,
        "longitude": -80.11,
        "aircraft_registration": "N12345",
        "aircraft_make": "Cessna",
        "aircraft_model": "172S",
        "operator_name": "Fixture Air LLC",
        "fatalities_total": 1,
    }]
    candidates = DuplicateDetector().find_candidates(incoming, existing)
    assert candidates
    assert candidates[0].event_id_b == "evt-existing"
    assert "registration" in candidates[0].match_fields
    assert candidates[0].match_score >= 0.5


def test_operational_schemas_include_reviewer_workflows() -> None:
    assert "match_score" in DuplicateCandidateOut.model_fields
    assert "issue_code" in DataQualityIssueOut.model_fields
    assert "data_quality_issues" in AccidentProvenance.model_fields


def test_archive_manifest_shape_is_stable() -> None:
    result = ArchiveRunResult(
        manifest_id="manifest-1",
        cutoff_at=datetime(2024, 1, 1, tzinfo=UTC),
        output_dir="/tmp/archive",
        execute=False,
        tables=[ArchiveTableResult(table="event_revisions", exported=10, deleted=0, file="events.jsonl")],
    )
    manifest = result.to_manifest()
    assert manifest["manifest_id"] == "manifest-1"
    assert manifest["format"] == "jsonl-v1"
    assert manifest["tables"][0]["table"] == "event_revisions"
