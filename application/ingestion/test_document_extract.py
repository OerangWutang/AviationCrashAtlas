"""Unit tests for the document extraction pure mapper.

No DB, no PDF library, no I/O.  Tests the claim extraction logic in isolation.
"""

from __future__ import annotations

import hashlib

import pytest

from atlas.application.ingestion.sources.document_extract import (
    _extract_high_confidence_claims,
    build_extract_result,
)


# ── Claim extraction tests ────────────────────────────────────────────────────

def test_ntsb_accession_extracted():
    text = "NTSB Accident Report\nAccession Number: LAX12FA123\nDate of accident: 12/15/2012"
    claims = _extract_high_confidence_claims(text)
    ntsb = next((c for c in claims if c.field_name == "ntsb_accession_number"), None)
    assert ntsb is not None
    assert ntsb.field_value == "LAX12FA123"


def test_ntsb_various_formats():
    texts = [
        "Ref: DFW20MA001",
        "Accident: NYC95FA100B",
        "Case CHI02LA055",
    ]
    for text in texts:
        claims = _extract_high_confidence_claims(text)
        ntsb = next((c for c in claims if c.field_name == "ntsb_accession_number"), None)
        assert ntsb is not None, f"Should have found NTSB number in: {text}"


def test_date_extracted_with_explicit_label():
    text = "Date: January 15, 2020\nSome other content."
    claims = _extract_high_confidence_claims(text)
    date_claim = next((c for c in claims if c.field_name == "event_date_raw"), None)
    assert date_claim is not None
    assert "January 15, 2020" in str(date_claim.field_value)


def test_date_not_extracted_without_label():
    # Date-like strings without an explicit label should not be extracted.
    text = "The flight had 2020-01-15 passengers onboard."
    claims = _extract_high_confidence_claims(text)
    date_claim = next((c for c in claims if c.field_name == "event_date_raw"), None)
    assert date_claim is None


def test_registration_extracted():
    text = "Registration: N12345\nOperator: Acme Air"
    claims = _extract_high_confidence_claims(text)
    reg = next((c for c in claims if c.field_name == "registration"), None)
    assert reg is not None
    assert reg.field_value == "N12345"


def test_no_claims_from_empty_text():
    claims = _extract_high_confidence_claims("")
    assert claims == []


def test_no_claims_from_unstructured_text():
    text = "The quick brown fox jumps over the lazy dog."
    claims = _extract_high_confidence_claims(text)
    assert claims == []


# ── build_extract_result tests ────────────────────────────────────────────────

def test_result_has_raw_payload():
    result = build_extract_result(
        text="NTSB Report LAX12FA123",
        filename="report.pdf",
        content_sha256="abc123",
        source_id_str="00000000-0000-0000-0000-000000000001",
    )
    assert result.raw_payload["filename"] == "report.pdf"
    assert result.raw_payload["content_sha256"] == "abc123"
    assert result.raw_payload["extracted_text"] == "NTSB Report LAX12FA123"


def test_result_has_ntsb_source_record_id():
    result = build_extract_result(
        text="Accident: DFW20MA001",
        filename="dfw.pdf",
        content_sha256="def456",
        source_id_str="00000000-0000-0000-0000-000000000002",
    )
    assert result.source_record_id == "DFW20MA001"


def test_result_idempotency_key_is_deterministic():
    kwargs = dict(
        text="Some text",
        filename="a.pdf",
        content_sha256="aabbcc",
        source_id_str="00000000-0000-0000-0000-000000000003",
    )
    result1 = build_extract_result(**kwargs)
    result2 = build_extract_result(**kwargs)
    assert result1.idempotency_key == result2.idempotency_key


def test_result_idempotency_key_changes_with_content():
    base = dict(
        text="Some text",
        filename="a.pdf",
        source_id_str="00000000-0000-0000-0000-000000000004",
    )
    r1 = build_extract_result(**base, content_sha256="aabbcc")
    r2 = build_extract_result(**base, content_sha256="ddeeff")
    assert r1.idempotency_key != r2.idempotency_key


def test_result_source_record_id_falls_back_to_hash():
    result = build_extract_result(
        text="No structured fields here.",
        filename="unstructured.pdf",
        content_sha256="ff00ff00ff00ff00",
        source_id_str="00000000-0000-0000-0000-000000000005",
    )
    assert result.source_record_id is not None
    assert result.source_record_id.startswith("sha256:")


def test_page_count_in_raw_payload():
    result = build_extract_result(
        text="text",
        filename="f.pdf",
        content_sha256="aa",
        source_id_str="x",
        page_count=42,
    )
    assert result.raw_payload["page_count"] == 42


def test_metadata_in_raw_payload():
    result = build_extract_result(
        text="text",
        filename="f.pdf",
        content_sha256="aa",
        source_id_str="x",
        metadata={"title": "Accident Report", "author": "NTSB"},
    )
    assert result.raw_payload["metadata"]["title"] == "Accident Report"
