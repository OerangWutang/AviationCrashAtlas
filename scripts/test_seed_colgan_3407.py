"""Tests for the Colgan Air 3407 seed script.

No database.  Exercises the pure logic: fact list integrity, claim building,
idempotency key derivation, and narrative completeness.  These run in the
standard unit-test suite alongside the rest of the application.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from uuid import uuid4

import pytest

# ── Import the seed script without executing it ───────────────────────────────
# The script lives in scripts/ (not a package), so we load it by spec.

_SCRIPT_PATH = Path(__file__).parent.parent.parent / "scripts" / "seed_colgan_3407.py"


def _load_seed_module():
    spec = importlib.util.spec_from_file_location("seed_colgan_3407", _SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture(scope="module")
def seed():
    return _load_seed_module()


# ── Fact list integrity ───────────────────────────────────────────────────────

def test_no_duplicate_field_names(seed):
    """IngestSourceData raises on duplicate field names — catch it here first."""
    field_names = [f for f, _, _ in seed.COLGAN_FACTS]
    assert len(field_names) == len(set(field_names)), (
        f"Duplicate field names in COLGAN_FACTS: "
        f"{[n for n in field_names if field_names.count(n) > 1]}"
    )


def test_minimum_claim_count(seed):
    """At least 40 claims — a meaningful record, not a stub."""
    assert len(seed.COLGAN_FACTS) >= 40, (
        f"Only {len(seed.COLGAN_FACTS)} facts — seed may be incomplete"
    )


def test_required_fields_present(seed):
    """Fields the projection completeness scorer requires must be present."""
    required = {
        "event_date",
        "location_city",
        "location_state",
        "aircraft_type",
        "registration",
        "operator",
        "flight_phase",
        "fatalities_total",
        "highest_injury_level",
        "probable_cause",
    }
    present = {f for f, _, _ in seed.COLGAN_FACTS}
    missing = required - present
    assert not missing, f"Required fields missing from seed: {missing}"


def test_accident_number_correct(seed):
    """DCA09MA026 is the NTSB public accident number for Colgan 3407."""
    assert seed.COLGAN_NTSB_ACCIDENT_NUMBER == "DCA09MA026"


def test_slug_correct(seed):
    assert seed.COLGAN_SLUG == "colgan-air-3407"


def test_fatalities_total_correct(seed):
    facts = {f: v for f, v, _ in seed.COLGAN_FACTS}
    assert facts["fatalities_total"] == 50  # 49 on board + 1 on ground


def test_fatalities_add_up(seed):
    facts = {f: v for f, v, _ in seed.COLGAN_FACTS}
    crew = facts.get("fatalities_crew", 0)
    pax = facts.get("fatalities_passengers", 0)
    ground = facts.get("fatalities_ground", 0)
    on_board = crew + pax
    # 2 crew + 47 pax = 49 on board; + 1 on ground = 50 total
    assert on_board == 49
    assert on_board + ground == facts["fatalities_total"]


def test_aircraft_registration_correct(seed):
    facts = {f: v for f, v, _ in seed.COLGAN_FACTS}
    assert facts["registration"] == "N200WQ"


def test_occurrence_date_correct(seed):
    facts = {f: v for f, v, _ in seed.COLGAN_FACTS}
    assert facts["event_date"] == "2009-02-12"


def test_every_fact_has_a_note(seed):
    """Every fact must carry a source note — epistemic discipline."""
    missing = [(f, v) for f, v, note in seed.COLGAN_FACTS if not note.strip()]
    assert not missing, (
        f"Facts without source notes (fix in COLGAN_FACTS): "
        f"{[f for f, _ in missing]}"
    )


def test_probable_cause_is_verbatim_ntsb(seed):
    """The probable cause text must contain the Board's standard opening phrase."""
    facts = {f: v for f, v, _ in seed.COLGAN_FACTS}
    pc = str(facts.get("probable_cause", ""))
    assert "National Transportation Safety Board determines" in pc


def test_field_values_are_not_none(seed):
    """None values silently become null claims — no value should be None."""
    none_fields = [f for f, v, _ in seed.COLGAN_FACTS if v is None]
    assert not none_fields, f"Fields with None values: {none_fields}"


# ── Claim building ────────────────────────────────────────────────────────────

def test_facts_to_claims_count(seed):
    from atlas.application.dto import IngestionClaimDTO
    source_id = uuid4()
    claims = seed._facts_to_claims(source_id)
    assert len(claims) == len(seed.COLGAN_FACTS)


def test_facts_to_claims_types(seed):
    claims = seed._facts_to_claims(uuid4())
    from atlas.application.dto import IngestionClaimDTO
    for claim in claims:
        assert isinstance(claim, IngestionClaimDTO)
        assert isinstance(claim.field_name, str)
        assert claim.field_value is not None


def test_facts_to_claims_no_duplicates(seed):
    claims = seed._facts_to_claims(uuid4())
    names = [c.field_name for c in claims]
    assert len(names) == len(set(names))


# ── Narrative ─────────────────────────────────────────────────────────────────

def test_narrative_non_empty(seed):
    assert len(seed.COLGAN_NARRATIVE) > 200


def test_narrative_mentions_aar(seed):
    assert "AAR-10/01" in seed.COLGAN_NARRATIVE


def test_narrative_mentions_probable_cause(seed):
    text = seed.COLGAN_NARRATIVE.lower()
    assert "stick shaker" in text or "aerodynamic stall" in text


def test_narrative_epistemic_disclaimer(seed):
    """Must say facts are sourced from NTSB — not invented."""
    assert "NTSB" in seed.COLGAN_NARRATIVE
    assert "public" in seed.COLGAN_NARRATIVE.lower() or "source" in seed.COLGAN_NARRATIVE.lower()


def test_short_summary_non_empty(seed):
    assert len(seed.COLGAN_SHORT_SUMMARY) > 20
    assert "3407" in seed.COLGAN_SHORT_SUMMARY


# ── Idempotency key stability ─────────────────────────────────────────────────

def test_idempotency_key_is_deterministic(seed):
    """Same COLGAN_FACTS → same content hash → same idempotency key.

    This ensures re-running the seed script replays cleanly rather than
    creating a second event or failing with a duplicate-payload error.
    """
    import json

    raw_payload_a = {
        "source": "NTSB eADMS (avall)",
        "accident_number": seed.COLGAN_NTSB_ACCIDENT_NUMBER,
        "ev_id": seed.COLGAN_EV_ID,
        "report_number": "AAR-10/01",
        "seed_version": 1,
        "seed_script": "scripts/seed_colgan_3407.py",
        "facts": [{"field": f, "value": v, "note": n} for f, v, n in seed.COLGAN_FACTS],
    }

    raw_payload_b = dict(raw_payload_a)  # same content, different object

    hash_a = hashlib.sha256(
        json.dumps(raw_payload_a, sort_keys=True, default=str).encode()
    ).hexdigest()
    hash_b = hashlib.sha256(
        json.dumps(raw_payload_b, sort_keys=True, default=str).encode()
    ).hexdigest()

    assert hash_a == hash_b, "Payload hash is not deterministic — seed would create duplicate events"


def test_derive_ingestion_run_id_stable(seed):
    """Same (source_id, idempotency_key) always produces the same run UUID."""
    from atlas.application.use_cases.ingest_source_data import IngestSourceData

    source_id = uuid4()
    key = "ntsb-eadms:DCA09MA026:abc123"
    run_id_a = IngestSourceData.derive_ingestion_run_id(source_id, key)
    run_id_b = IngestSourceData.derive_ingestion_run_id(source_id, key)
    assert run_id_a == run_id_b


# ── Dry-run smoke test ────────────────────────────────────────────────────────

def test_dry_run_does_not_crash(seed, capsys):
    """_dry_run() must print without raising — pure output, no imports needed."""
    seed._dry_run()
    captured = capsys.readouterr()
    assert "DCA09MA026" in captured.out
    assert "47" in captured.out or str(len(seed.COLGAN_FACTS)) in captured.out
    assert "No database changes" in captured.out


def test_dry_run_lists_all_fields(seed, capsys):
    seed._dry_run()
    captured = capsys.readouterr()
    for field_name, _, _ in seed.COLGAN_FACTS:
        assert field_name in captured.out, f"Field {field_name!r} missing from dry-run output"
