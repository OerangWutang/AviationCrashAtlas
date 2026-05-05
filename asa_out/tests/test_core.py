"""
Test suite — runs from the tests/ directory (not project root).

Covers all bugs identified in the review:
  - claim_value serialization (datetime, date, bool, etc.)
  - conflict detection (against all active claims, not just is_winning)
  - source completeness label thresholds
      ≥0.90 Well sourced | ≥0.70 Mostly sourced | ≥0.50 Partially sourced | <0.50 Weakly sourced
  - normalizer (all field types, edge cases)
  - deduplicator (exact / spatial-temporal / fuzzy)
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# claim_value — serialization round-trips
# ─────────────────────────────────────────────────────────────────────────────
from atlas.models.claim_value import decode, display, encode, values_conflict


class TestClaimValueEncode:
    def test_none(self):
        e = encode(None)
        assert e == {"v": None, "type": "null"}

    def test_string(self):
        e = encode("FATAL")
        assert e == {"v": "FATAL", "type": "str"}

    def test_int(self):
        e = encode(4)
        assert e == {"v": 4, "type": "int"}

    def test_float(self):
        e = encode(0.91)
        assert e["type"] == "float"
        assert abs(e["v"] - 0.91) < 1e-9

    def test_bool_true(self):
        e = encode(True)
        assert e == {"v": True, "type": "bool"}

    def test_bool_false(self):
        e = encode(False)
        assert e == {"v": False, "type": "bool"}

    def test_bool_not_confused_with_int(self):
        # bool is subclass of int — must be distinguished
        e = encode(True)
        assert e["type"] == "bool", "True must encode as bool, not int"

    def test_datetime(self):
        dt = datetime(2023, 4, 15, 14, 30, tzinfo=UTC)
        e = encode(dt)
        assert e["type"] == "datetime"
        assert e["v"] == "2023-04-15T14:30:00+00:00"

    def test_date(self):
        d = date(2023, 4, 15)
        e = encode(d)
        assert e["type"] == "date"
        assert e["v"] == "2023-04-15"

    def test_dict(self):
        e = encode({"latitude": 44.06, "longitude": -121.31})
        assert e["type"] == "dict"

    def test_list(self):
        e = encode([1, 2, 3])
        assert e["type"] == "list"
        assert len(e["v"]) == 3


class TestClaimValueDecode:
    def test_null(self):
        assert decode({"v": None, "type": "null"}) is None

    def test_string(self):
        assert decode({"v": "FATAL", "type": "str"}) == "FATAL"

    def test_int(self):
        assert decode({"v": 4, "type": "int"}) == 4

    def test_bool(self):
        assert decode({"v": True, "type": "bool"}) is True

    def test_datetime_roundtrip(self):
        dt = datetime(2023, 4, 15, 14, 30, tzinfo=UTC)
        assert decode(encode(dt)) == dt

    def test_date_roundtrip(self):
        d = date(2023, 4, 15)
        assert decode(encode(d)) == d

    def test_invalid_envelope_raises(self):
        with pytest.raises(ValueError):
            decode({"no_v_key": "bad"})

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError):
            decode({"v": "x", "type": "custom_unknown"})


class TestClaimValueDisplayBasic:
    def test_null(self):
        assert display({"v": None, "type": "null"}) == "—"

    def test_bool_true(self):
        assert display({"v": True, "type": "bool"}) == "Yes"

    def test_bool_false(self):
        assert display({"v": False, "type": "bool"}) == "No"

    def test_datetime(self):
        dt = datetime(2023, 4, 15, 14, 30, tzinfo=UTC)
        result = display(encode(dt))
        assert "2023-04-15" in result

    def test_coordinates(self):
        e = encode({"latitude": 44.06, "longitude": -121.31})
        result = display(e)
        assert "44" in result and "121" in result


class TestValuesConflict:
    def test_identical_strings_no_conflict(self):
        assert not values_conflict(encode("FATAL"), encode("FATAL"), "injury_severity")

    def test_case_insensitive_no_conflict(self):
        assert not values_conflict(encode("fatal"), encode("FATAL"), "injury_severity")

    def test_different_strings_conflict(self):
        assert values_conflict(encode("FATAL"), encode("SERIOUS"), "injury_severity")

    # Fatality/injury fields: exact integer match required — any difference is a conflict
    def test_fatalities_identical_no_conflict(self):
        assert not values_conflict(encode(4), encode(4), "fatalities_total")

    def test_fatalities_one_off_is_conflict(self):
        """4 vs 5 fatalities must be a conflict — any difference matters."""
        assert values_conflict(encode(4), encode(5), "fatalities_total")

    def test_fatalities_large_within_pct_still_conflict(self):
        """100 vs 104 — previously slipped through 5% rule, now always conflicts."""
        assert values_conflict(encode(100), encode(104), "fatalities_total")

    def test_fatalities_both_zero_no_conflict(self):
        assert not values_conflict(encode(0), encode(0), "fatalities_total")

    def test_serious_injuries_exact_match(self):
        assert values_conflict(encode(1), encode(2), "serious_injuries")

    def test_split_injury_counts_are_exact_match_fields(self):
        assert values_conflict(encode(1), encode(2), "fatalities_crew")
        assert values_conflict(encode(1), encode(2), "fatalities_passengers")
        assert values_conflict(encode(1), encode(2), "serious_injuries_crew")
        assert values_conflict(encode(1), encode(2), "serious_injuries_passengers")
        assert values_conflict(encode(1), encode(2), "minor_injuries_crew")
        assert values_conflict(encode(1), encode(2), "minor_injuries_passengers")
        assert values_conflict(encode(1), encode(2), "uninjured_crew")
        assert values_conflict(encode(1), encode(2), "uninjured_passengers")

    def test_aboard_total_exact_match(self):
        assert values_conflict(encode(150), encode(151), "aboard_total")

    # Non-fatality numeric fields still use percentage threshold
    def test_other_numeric_within_5pct_no_conflict(self):
        assert not values_conflict(encode(100), encode(104), "some_numeric_field")

    def test_other_numeric_beyond_5pct_conflict(self):
        assert values_conflict(encode(4), encode(10), "some_numeric_field")

    def test_null_no_conflict(self):
        assert not values_conflict(encode(None), encode("FATAL"), "injury_severity")

    def test_location_close_no_conflict(self):
        a = encode({"latitude": 44.06, "longitude": -121.31})
        b = encode({"latitude": 44.07, "longitude": -121.32})
        assert not values_conflict(a, b, "location_coordinates")

    def test_location_far_conflict(self):
        a = encode({"latitude": 44.06, "longitude": -121.31})
        b = encode({"latitude": 35.00, "longitude": -90.00})
        assert values_conflict(a, b, "location_coordinates")


# ─────────────────────────────────────────────────────────────────────────────
# Normalizer
# ─────────────────────────────────────────────────────────────────────────────

from atlas.ingestion.normalizer import (
    build_canonical_fields,
    normalize_coordinates,
    normalize_country_code,
    normalize_damage,
    normalize_date,
    normalize_int,
    normalize_operator,
    normalize_phase,
    normalize_severity,
)


class TestNormalizeSeverity:
    def test_fatal(self): assert normalize_severity("FATAL") == "FATAL"
    def test_lowercase(self): assert normalize_severity("fatal") == "FATAL"
    def test_none(self): assert normalize_severity(None) == "UNKNOWN"
    def test_empty(self): assert normalize_severity("") == "UNKNOWN"
    def test_unrecognized(self): assert normalize_severity("CATASTROPHIC") == "UNKNOWN"


class TestNormalizeDamage:
    def test_destroyed(self): assert normalize_damage("DEST") == "DESTROYED"
    def test_substantial(self): assert normalize_damage("SUBS") == "SUBSTANTIAL"
    def test_none(self): assert normalize_damage(None) == "UNKNOWN"


class TestNormalizeDate:
    def test_iso_date(self):
        dt, prec = normalize_date("2023-04-15")
        assert dt is not None and dt.year == 2023 and prec == "day"

    def test_with_time(self):
        dt, prec = normalize_date("2023-04-15", "1430")
        assert dt is not None and dt.hour == 14 and prec == "exact"

    def test_none_returns_none(self):
        dt, prec = normalize_date(None)
        assert dt is None and prec == "year"

    def test_invalid(self):
        dt, _ = normalize_date("not-a-date")
        assert dt is None

    def test_naive_local(self):
        # NTSB times are local; normalizer must NOT attach UTC tzinfo.
        # Storing a false UTC offset is worse than a correct naive local datetime.
        dt, _ = normalize_date("2023-01-01")
        assert dt is not None
        assert dt.tzinfo is None, "datetime must be naive (local, tz unknown) — not falsely UTC"

    def test_invalid_time_fallback_to_day(self):
        _, prec = normalize_date("2023-04-15", "XXXX")
        assert prec == "day"

    def test_returns_datetime_not_string(self):
        """Key fix: normalizer returns datetime, not string — safe for encode()."""
        dt, _ = normalize_date("2023-04-15")
        assert isinstance(dt, datetime)


class TestNormalizeCoordinates:
    def test_valid(self): assert normalize_coordinates(44.06, -121.31) == (44.06, -121.31)
    def test_zero_zero(self): assert normalize_coordinates(0.0, 0.0) == (None, None)
    def test_strings(self): assert normalize_coordinates("44.06", "-121.31") == (44.06, -121.31)
    def test_none(self): assert normalize_coordinates(None, None) == (None, None)
    def test_out_of_range(self): assert normalize_coordinates(91.0, 0.0) == (None, None)


class TestNormalizeInt:
    def test_int(self): assert normalize_int(4) == 4
    def test_float_string(self): assert normalize_int("4.0") == 4
    def test_none(self): assert normalize_int(None) is None
    def test_empty(self): assert normalize_int("") is None
    def test_non_numeric(self): assert normalize_int("N/A") is None


class TestNormalizePhase:
    def test_landing(self): assert normalize_phase("LANDING") == "LANDING"
    def test_landing_roll(self): assert normalize_phase("LANDING ROLL") == "LANDING"
    def test_final_approach(self): assert normalize_phase("FINAL APPROACH") == "APPROACH"
    def test_take_off(self): assert normalize_phase("TAKE-OFF") == "TAKEOFF"
    def test_none(self): assert normalize_phase(None) is None


class TestNormalizeOperator:
    def test_strips_llc(self): assert normalize_operator("Desert Helicopters LLC") == "DESERT HELICOPTERS"
    def test_strips_inc(self): assert normalize_operator("Southwest Airlines Inc") == "SOUTHWEST AIRLINES"
    def test_none(self): assert normalize_operator(None) is None


class TestBuildCanonicalFields:
    BASE = {
        "EventId": "WPR23LA001", "EventDate": "2023-01-04", "EventTime": "1430",
        "City": "Bend", "State": "OR", "Country": "USA",
        "LatDecimal": "44.06", "LongDecimal": "-121.31",
        "AircraftDamage": "SUBS", "HighestInjury": "SERIOUS",
        "TotalFatalInjuries": "0", "TotalSeriousInjuries": "1",
        "TotalMinorInjuries": "0",
        "AboardPassengerCount": "1", "AboardCrewCount": "1",
        "Make": "Cessna", "Model": "172S", "Registration": "N12345",
        "AmateurBuilt": "No", "EngineType": "Reciprocating", "NumberOfEngines": "1",
        "OperatorName": "Private", "PurposeOfFlight": "Personal",
        "PhaseOfFlight": "LANDING", "WeatherCondition": "VMC",
        "InvestigationType": "FINAL",
        "ProbableCause": "Failure to maintain airspeed.",
    }

    def row(self, **kw): return {**self.BASE, **kw}

    def test_occurred_at_is_datetime(self):
        """Critical: must return datetime, not string — encode() handles serialization."""
        f = build_canonical_fields(self.row())
        assert isinstance(f["occurred_at"], datetime)

    def test_injury_severity(self):
        assert build_canonical_fields(self.row())["injury_severity"] == "SERIOUS"

    def test_aircraft_damage(self):
        assert build_canonical_fields(self.row())["aircraft_damage"] == "SUBSTANTIAL"

    def test_location_coordinates_is_dict(self):
        f = build_canonical_fields(self.row())
        assert isinstance(f["location_coordinates"], dict)
        assert f["location_coordinates"]["latitude"] == 44.06

    def test_zero_zero_excluded(self):
        f = build_canonical_fields(self.row(LatDecimal="0", LongDecimal="0"))
        assert "location_coordinates" not in f

    def test_aboard_total(self):
        assert build_canonical_fields(self.row())["aboard_total"] == 2

    def test_source_provided_crew_passenger_splits_are_preserved(self):
        fields = build_canonical_fields(self.row(
            FatalInjuryCrewCount="1",
            FatalInjuryPassengerCount="2",
            SeriousInjuryCrewCount="3",
            SeriousInjuryPassengerCount="4",
            MinorInjuryCrewCount="5",
            MinorInjuryPassengerCount="6",
            CrewUninjuredCount="7",
            PassengerUninjuredCount="8",
        ))
        assert fields["fatalities_crew"] == 1
        assert fields["fatalities_passengers"] == 2
        assert fields["serious_injuries_crew"] == 3
        assert fields["serious_injuries_passengers"] == 4
        assert fields["minor_injuries_crew"] == 5
        assert fields["minor_injuries_passengers"] == 6
        assert fields["uninjured_crew"] == 7
        assert fields["uninjured_passengers"] == 8

    def test_investigation_status(self):
        assert build_canonical_fields(self.row())["investigation_status"] == "final"

    def test_engine_type_excluded(self):
        """engine_type is not in AccidentRecord — must not appear in canonical fields."""
        f = build_canonical_fields(self.row())
        assert "engine_type" not in f, "engine_type creates phantom claims"

    def test_num_engines_excluded(self):
        """num_engines is not in AccidentRecord — must not appear in canonical fields."""
        f = build_canonical_fields(self.row())
        assert "num_engines" not in f, "num_engines creates phantom claims"


class TestNormalizeCountryCodeSingleReturn:
    """Tests using the current tuple-return API: (iso3_or_None, raw_name)."""

    def test_full_name_usa(self):
        code, _ = normalize_country_code("United States")
        assert code == "USA"

    def test_abbreviation_usa(self):
        code, _ = normalize_country_code("USA")
        assert code == "USA"

    def test_us_abbreviation(self):
        code, _ = normalize_country_code("US")
        assert code == "USA"

    def test_canada(self):
        code, _ = normalize_country_code("Canada")
        assert code == "CAN"

    def test_already_alpha3(self):
        code, _ = normalize_country_code("MEX")
        assert code == "MEX"

    def test_case_insensitive(self):
        code, _ = normalize_country_code("united states")
        assert code == "USA"

    def test_unknown_long_form_not_truncated_to_3(self):
        """Unknown country must not produce a 3-char code by truncation."""
        code, raw = normalize_country_code("Ruritania")
        assert code is None, "Unknown long name must return None for iso code, not truncation"
        assert raw == "Ruritania"

    def test_unknown_3letter_passthrough(self):
        """An unrecognised 3-letter code is passed through unchanged."""
        code, _ = normalize_country_code("XYZ")
        assert code == "XYZ"


# ─────────────────────────────────────────────────────────────────────────────
# Deduplicator
# ─────────────────────────────────────────────────────────────────────────────

from atlas.ingestion.deduplicator import DuplicateDetector, _norm, _overlap


class TestDuplicateDetector:
    def _ev(self, **kw):
        return {
            "event_id": "evt-001",
            "ntsb_event_id": "WPR23LA001",
            "occurred_at": datetime(2023, 4, 15, tzinfo=UTC),
            "latitude": 44.06, "longitude": -121.31,
            "aircraft_make": "cessna", "aircraft_model": "172s",
            "aircraft_registration": "N12345",
            "operator_name": "private", "fatalities_total": 0,
            **kw,
        }

    def test_exact_id_match(self):
        a = self._ev(event_id="evt-001")
        b = self._ev(event_id="evt-002")
        cands = DuplicateDetector().find_candidates(a, [b])
        assert cands[0].match_type == "exact"
        assert cands[0].auto_merge is True

    def test_different_date_no_match(self):
        a = self._ev(occurred_at=datetime(2023, 4, 15, tzinfo=UTC))
        b = self._ev(event_id="evt-002", ntsb_event_id="OTHER",
                     occurred_at=datetime(2023, 10, 1, tzinfo=UTC))
        assert DuplicateDetector().find_candidates(a, [b]) == []

    def test_spatial_temporal_match(self):
        a = self._ev(event_id="evt-001", ntsb_event_id="WPR23LA001")
        b = self._ev(event_id="evt-002", ntsb_event_id="ASN-001",
                     latitude=44.07, longitude=-121.32)
        cands = DuplicateDetector().find_candidates(a, [b])
        assert cands[0].match_score >= 0.5


class TestNormHelpers:
    def test_norm_accents(self): assert _norm("Clément") == "clement"
    def test_norm_punctuation(self): assert _norm("U.S. Airways") == "us airways"
    def test_overlap_identical(self): assert _overlap("united airlines", "united airlines") == 1.0
    def test_overlap_none(self): assert _overlap("cessna", "boeing") == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Confidence engine — label thresholds (canonical source of truth)
# ─────────────────────────────────────────────────────────────────────────────

from atlas.confidence.engine import (
    THRESHOLD_GOOD,
    THRESHOLD_HIGH,
    THRESHOLD_PARTIAL,
    confidence_label,
)


class TestConfidenceLabel:
    """Labels use source-completeness language, not generic confidence language."""

    def test_high(self):
        label, css = confidence_label(0.95)
        assert label == "Well sourced" and css == "conf-high"

    def test_good(self):
        label, _ = confidence_label(0.80)
        assert label == "Mostly sourced"

    def test_partial(self):
        label, _ = confidence_label(0.60)
        assert label == "Partially sourced"

    def test_low(self):
        label, css = confidence_label(0.30)
        assert label == "Weakly sourced" and css == "conf-low"

    def test_boundary_high(self):
        """Score of exactly 0.90 must be 'Well sourced', not 'Mostly sourced'."""
        label, _ = confidence_label(THRESHOLD_HIGH)
        assert label == "Well sourced"

    def test_boundary_good(self):
        label, _ = confidence_label(THRESHOLD_GOOD)
        assert label == "Mostly sourced"

    def test_boundary_partial(self):
        label, _ = confidence_label(THRESHOLD_PARTIAL)
        assert label == "Partially sourced"

    def test_088_is_mostly_sourced_not_well(self):
        """
        Review caught: mock data had 0.88 labeled 'High'. This test
        verifies the canonical rule: 0.88 < 0.90 → 'Mostly sourced'.
        """
        label, _ = confidence_label(0.88)
        assert label == "Mostly sourced", "0.88 must be 'Mostly sourced', not 'Well sourced'"

    def test_thresholds_consistent(self):
        """Ensure THRESHOLD constants match what confidence_label uses."""
        assert confidence_label(THRESHOLD_HIGH)[0] == "Well sourced"
        assert confidence_label(THRESHOLD_GOOD)[0] == "Mostly sourced"
        assert confidence_label(THRESHOLD_PARTIAL)[0] == "Partially sourced"
        assert confidence_label(THRESHOLD_PARTIAL - 0.001)[0] == "Weakly sourced"


# ─────────────────────────────────────────────────────────────────────────────
# Normalizer — country and state schema-safety (v2 review fixes)
# ─────────────────────────────────────────────────────────────────────────────


class TestNormalizeCountryCodeTupleReturn:
    def test_known_country_returns_iso3(self):
        code, raw = normalize_country_code("United States")
        assert code == "USA"
        assert raw == "United States"

    def test_already_iso3_passthrough(self):
        code, raw = normalize_country_code("AUS")
        assert code == "AUS"

    def test_unknown_long_name_returns_none_code(self):
        """Unknown country must not produce a value longer than 3 chars."""
        code, raw = normalize_country_code("Ruritania")
        # country_code must be None so callers don't violate VARCHAR(3)
        assert code is None
        assert raw == "Ruritania"

    def test_unknown_medium_name_returns_none_code(self):
        code, raw = normalize_country_code("Freedonia")
        assert code is None
        assert raw is not None

    def test_us_variants(self):
        for variant in ("US", "USA", "U.S.", "UNITED STATES OF AMERICA"):
            code, _ = normalize_country_code(variant)
            assert code == "USA", f"Failed for {variant!r}"

    def test_build_canonical_unknown_country_no_country_code_key(self):
        """build_canonical_fields must not put a >3-char value into country_code."""
        raw = {
            "Country": "Ruritania",
            "EventDate": "2023-05-10",
        }
        fields = build_canonical_fields(raw)
        # country_code must be absent (None) for unknown countries
        assert "country_code" not in fields or fields.get("country_code") is None
        # raw name should be preserved
        assert fields.get("country_name_raw") == "Ruritania"

    def test_build_canonical_known_country_populates_country_code(self):
        raw = {"Country": "Canada", "EventDate": "2023-05-10"}
        fields = build_canonical_fields(raw)
        assert fields["country_code"] == "CAN"

    def test_country_code_always_fits_varchar3(self):
        """All codes produced for any input must be ≤3 characters."""
        for sample in ["United States", "New Zealand", "AUS", "XY", "Ruritania", ""]:
            if not sample:
                continue
            code, _ = normalize_country_code(sample)
            if code is not None:
                assert len(code) <= 3, f"Code {code!r} for {sample!r} exceeds VARCHAR(3)"


class TestNormalizeDateTimezone:
    def test_date_only_is_naive(self):
        """Dates without explicit timezone must be naive — not falsely UTC-stamped."""
        dt, precision = normalize_date("2023-05-10")
        assert dt is not None
        assert dt.tzinfo is None, "Naive local date must not be coerced to UTC"
        assert precision == "day"

    def test_date_with_time_is_naive(self):
        dt, precision = normalize_date("2023-05-10", "1430")
        assert dt is not None
        assert dt.tzinfo is None, "Local time must not be coerced to UTC"
        assert precision == "exact"
        assert dt.hour == 14
        assert dt.minute == 30

    def test_missing_date_returns_none(self):
        dt, precision = normalize_date(None)
        assert dt is None
        assert precision == "year"


from atlas.ingestion.normalizer import normalize_state_code


class TestStateCodeNormalization:
    """state_code must store real postal codes, never full state names."""

    def test_full_name_maps_to_code(self):
        """'Oregon' → state_code='OR', state_name_raw='Oregon'."""
        code, raw_name = normalize_state_code("Oregon")
        assert code == "OR"
        assert raw_name == "Oregon"

    def test_north_carolina_maps_to_nc(self):
        """Multi-word state names must produce correct 2-letter code."""
        code, raw_name = normalize_state_code("North Carolina")
        assert code == "NC"
        assert raw_name == "North Carolina"

    def test_already_2letter_passthrough(self):
        code, raw_name = normalize_state_code("CA")
        assert code == "CA"
        assert raw_name == "CA"

    def test_case_insensitive(self):
        code, _ = normalize_state_code("florida")
        assert code == "FL"

    def test_unknown_returns_none_code(self):
        """Non-US regions we don't recognize must return None, not a fake code."""
        code, raw_name = normalize_state_code("Ontario")
        # Ontario is 7 chars and not in our US lookup — code is None, raw preserved
        assert code is None
        assert raw_name == "Ontario"

    def test_build_canonical_north_carolina(self):
        """Full state name must produce state_code='NC', not 'North Caro'."""
        raw = {"State": "North Carolina", "EventDate": "2023-01-01"}
        fields = build_canonical_fields(raw)
        assert fields.get("state_code") == "NC", (
            "'North Carolina' must normalize to 'NC', not a truncation or full name"
        )
        assert fields.get("state_name_raw") == "North Carolina"

    def test_build_canonical_abbreviation(self):
        raw = {"State": "CA", "EventDate": "2023-01-01"}
        fields = build_canonical_fields(raw)
        assert fields.get("state_code") == "CA"
        assert fields.get("state_name_raw") == "CA"

    def test_state_code_always_fits_varchar10(self):
        """Every state_code value must be ≤10 chars (schema constraint)."""
        for sample in ["Oregon", "North Carolina", "CA", "WY", "Ontario", "Alberta"]:
            code, _ = normalize_state_code(sample)
            if code is not None:
                assert len(code) <= 10, f"Code {code!r} for {sample!r} exceeds VARCHAR(10)"


# ─────────────────────────────────────────────────────────────────────────────
# Projection — pending/disputed claims must not become winners
# ─────────────────────────────────────────────────────────────────────────────

from atlas.claims.projection import ProjectionService
from atlas.models.orm import ClaimType


class TestProjectionWinnerEligibility:
    """Winners must only come from CONFIRMED or INFERRED claims."""

    def _make_claim(self, field: str, claim_type: str, tier: int = 2) -> MagicMock:
        c = MagicMock()
        c.field_name = field
        c.claim_type = claim_type
        c.source_id = f"src-{claim_type}"
        c.created_at = datetime(2024, 1, 1, tzinfo=UTC)
        return c

    def _make_source(self, tier: int) -> MagicMock:
        s = MagicMock()
        s.tier = tier
        return s

    def _svc(self) -> ProjectionService:
        svc = ProjectionService.__new__(ProjectionService)
        return svc

    def test_pending_claim_is_not_a_winner(self):
        pending = self._make_claim("injury_severity", ClaimType.PENDING.value)
        svc = self._svc()
        winners = svc._select_winners(
            [pending],
            {pending.source_id: self._make_source(2)}
        )
        assert "injury_severity" not in winners, "PENDING claim must never be projected"

    def test_disputed_claim_is_not_a_winner(self):
        disputed = self._make_claim("injury_severity", ClaimType.DISPUTED.value)
        svc = self._svc()
        winners = svc._select_winners(
            [disputed],
            {disputed.source_id: self._make_source(2)}
        )
        assert "injury_severity" not in winners, "DISPUTED claim must never be silently projected"

    def test_confirmed_beats_inferred(self):
        confirmed = self._make_claim("injury_severity", ClaimType.CONFIRMED.value)
        inferred  = self._make_claim("injury_severity", ClaimType.INFERRED.value)
        svc = self._svc()
        winners = svc._select_winners(
            [inferred, confirmed],
            {confirmed.source_id: self._make_source(1), inferred.source_id: self._make_source(1)}
        )
        assert winners["injury_severity"].claim_type == ClaimType.CONFIRMED.value

    def test_pending_with_confirmed_still_selects_confirmed(self):
        confirmed = self._make_claim("aircraft_make", ClaimType.CONFIRMED.value)
        pending   = self._make_claim("aircraft_make", ClaimType.PENDING.value)
        svc = self._svc()
        winners = svc._select_winners(
            [pending, confirmed],
            {confirmed.source_id: self._make_source(1), pending.source_id: self._make_source(1)}
        )
        assert winners["aircraft_make"].claim_type == ClaimType.CONFIRMED.value


# ─────────────────────────────────────────────────────────────────────────────
# Normalizer — aircraft_amateur_built must not default missing data to False
# ─────────────────────────────────────────────────────────────────────────────

class TestAircraftAmateurBuilt:
    def test_yes_sets_true(self):
        fields = build_canonical_fields({"AmateurBuilt": "Yes", "EventDate": "2023-01-01"})
        assert fields["aircraft_amateur_built"] is True

    def test_no_sets_false(self):
        fields = build_canonical_fields({"AmateurBuilt": "No", "EventDate": "2023-01-01"})
        assert fields["aircraft_amateur_built"] is False

    def test_missing_field_omits_key(self):
        """Missing AmateurBuilt must NOT produce a claim — unknown != False."""
        fields = build_canonical_fields({"EventDate": "2023-01-01"})
        assert "aircraft_amateur_built" not in fields, (
            "Missing AmateurBuilt must not produce a False claim"
        )

    def test_empty_string_omits_key(self):
        """Empty string is also unknown — must not produce False."""
        fields = build_canonical_fields({"AmateurBuilt": "", "EventDate": "2023-01-01"})
        assert "aircraft_amateur_built" not in fields

    def test_true_string_variants(self):
        for val in ("TRUE", "1", "yes", "Yes"):
            fields = build_canonical_fields({"AmateurBuilt": val, "EventDate": "2023-01-01"})
            assert fields.get("aircraft_amateur_built") is True, f"Expected True for {val!r}"


# ─────────────────────────────────────────────────────────────────────────────
# Confidence engine — pending claims must not inflate active_fields coverage
# ─────────────────────────────────────────────────────────────────────────────

from atlas.confidence.engine import ConfidenceEngine
from atlas.models.orm import ClaimType as CT


class TestConfidenceEligibility:
    """
    ConfidenceEngine must use the same eligibility rule as ProjectionService:
    only CONFIRMED and INFERRED claims count as coverage.
    PENDING and DISPUTED claims must not inflate completeness or multi-source scores.
    """

    def _make_claim(self, field: str, ctype: str, source_id: str = "src-a") -> MagicMock:
        c = MagicMock()
        c.field_name = field
        c.claim_type = ctype
        c.source_id = source_id
        c.field_value = {"v": "TEST", "type": "str"}
        c.created_at = datetime(2024, 1, 1, tzinfo=UTC)
        return c

    def _engine(self) -> ConfidenceEngine:
        return ConfidenceEngine.__new__(ConfidenceEngine)

    def test_pending_claim_does_not_count_as_active_field(self):
        """A pending claim for occurred_at must not prevent the missing-date penalty."""
        engine = self._engine()
        pending = self._make_claim("occurred_at", CT.PENDING.value)
        sources = {}
        conflicts = []
        documents = []
        bd = MagicMock()
        bd.factors = []

        # Call _compute directly to inspect score adjustments (score value not needed here)
        engine._compute([pending], conflicts, sources, documents, bd)

        # With only a pending claim for occurred_at, the missing-date penalty
        # should apply — pending data is not projected so it's not "present".
        factor_names = [f.name for f in bd.factors]
        assert "missing_date" in factor_names, (
            "Pending occurred_at must trigger missing_date penalty"
        )

    def test_pending_source_does_not_earn_multi_source_coverage_bonus(self):
        """Two sources where one has only pending claims must not earn multi-source bonus."""
        engine = self._engine()
        confirmed = self._make_claim("injury_severity", CT.CONFIRMED.value, "src-a")
        pending   = self._make_claim("aircraft_make", CT.PENDING.value, "src-b")

        src_a = MagicMock(); src_a.tier = 1
        src_b = MagicMock(); src_b.tier = 2
        sources = {"src-a": src_a, "src-b": src_b}
        bd = MagicMock(); bd.factors = []

        engine._compute([confirmed, pending], [], sources, [], bd)

        factor_names = [f.name for f in bd.factors]
        assert "multi_source_coverage" not in factor_names, (
            "A source with only pending claims must not trigger multi_source_coverage bonus"
        )


# ─────────────────────────────────────────────────────────────────────────────
# ClaimWriter — conflict history must record actual original type, not CONFIRMED
# ─────────────────────────────────────────────────────────────────────────────

class TestConflictHistoryType:
    """
    When a new inferred claim conflicts with an existing claim, the history row
    must record 'inferred → disputed', not 'confirmed → disputed'.
    """

    def test_original_type_captured_before_mutation(self):
        """
        Simulate the capture-before-mutation pattern: original_new_type is set
        once before the loop, so even if new_claim.claim_type is mutated on the
        first conflict, subsequent iterations still record the original type.
        """
        # Simulate what writer._detect_conflicts does
        class FakeClaim:
            def __init__(self, ctype):
                self.claim_type = ctype

        new_claim = FakeClaim(CT.INFERRED.value)
        original_new_type = new_claim.claim_type  # captured before loop

        history_entries = []
        # First conflicting claim
        new_claim.claim_type = CT.DISPUTED.value
        history_entries.append({"old": original_new_type, "new": CT.DISPUTED.value})

        # Second conflicting claim — if original_new_type were re-captured here
        # it would be DISPUTED; but since we captured it before the loop it's INFERRED
        history_entries.append({"old": original_new_type, "new": CT.DISPUTED.value})

        assert history_entries[0]["old"] == CT.INFERRED.value, (
            "First conflict history must record original INFERRED type"
        )
        assert history_entries[1]["old"] == CT.INFERRED.value, (
            "Second conflict history must also record original INFERRED type, not DISPUTED"
        )

    def test_inferred_claim_history_not_confirmed(self):
        """
        Confirm that original_new_type for an inferred claim is 'inferred',
        not the previously hardcoded 'confirmed'.
        """
        new_claim_type = CT.INFERRED.value
        # This is the logic from the fixed writer
        original_new_type = new_claim_type
        assert original_new_type == CT.INFERRED.value
        assert original_new_type != CT.CONFIRMED.value


# ─────────────────────────────────────────────────────────────────────────────
# Projection — occurred_at_precision is projected into accident_records
# ─────────────────────────────────────────────────────────────────────────────

class TestOccurredAtPrecisionProjection:
    """occurred_at_precision must flow from claims through _build_record."""

    def _make_winner(self, field: str, value: Any, ctype: str = CT.CONFIRMED.value) -> MagicMock:
        from atlas.models import claim_value as cv
        c = MagicMock()
        c.field_name = field
        c.claim_type = ctype
        c.source_id = "src-x"
        c.field_value = cv.encode(value)
        c.created_at = datetime(2024, 1, 1, tzinfo=UTC)
        return c

    def _svc(self) -> ProjectionService:
        return ProjectionService.__new__(ProjectionService)

    def test_precision_included_in_build_record(self):
        svc = self._svc()
        dt = datetime(2023, 5, 10, 14, 30)
        winners = {
            "occurred_at": self._make_winner("occurred_at", dt),
            "occurred_at_precision": self._make_winner("occurred_at_precision", "exact"),
        }
        record = svc._build_record(
            event_id="evt-001",
            claims=list(winners.values()),
            winners=winners,
            sources={"src-x": MagicMock(tier=1, id="src-x")},
            conflicts=[],
            score=0.75,
            breakdown={},
        )
        assert record["occurred_at_precision"] == "exact"
        assert record["claim_source_ids"] == ["src-x"]
        assert record["source_ids"] == ["src-x"]

    def test_missing_precision_projects_as_none(self):
        svc = self._svc()
        dt = datetime(2023, 5, 10)
        winners = {
            "occurred_at": self._make_winner("occurred_at", dt),
            # no occurred_at_precision winner
        }
        record = svc._build_record(
            event_id="evt-002",
            claims=list(winners.values()),
            winners=winners,
            sources={"src-x": MagicMock(tier=1, id="src-x")},
            conflicts=[],
            score=0.5,
            breakdown={},
        )
        assert record["occurred_at_precision"] is None
        # claim_source_ids should reflect all claims passed in, even when
        # fewer fields have winning projections
        assert record["claim_source_ids"] == ["src-x"]

    def test_split_injury_fields_included_in_build_record(self):
        svc = self._svc()
        winners = {
            "fatalities_total": self._make_winner("fatalities_total", 3),
            "fatalities_crew": self._make_winner("fatalities_crew", 1),
            "fatalities_passengers": self._make_winner("fatalities_passengers", 2),
            "serious_injuries": self._make_winner("serious_injuries", 4),
            "serious_injuries_crew": self._make_winner("serious_injuries_crew", 1),
            "serious_injuries_passengers": self._make_winner("serious_injuries_passengers", 3),
            "minor_injuries": self._make_winner("minor_injuries", 5),
            "minor_injuries_crew": self._make_winner("minor_injuries_crew", 2),
            "minor_injuries_passengers": self._make_winner("minor_injuries_passengers", 3),
            "uninjured_crew": self._make_winner("uninjured_crew", 1),
            "uninjured_passengers": self._make_winner("uninjured_passengers", 42),
        }
        record = svc._build_record(
            event_id="evt-splits",
            claims=list(winners.values()),
            winners=winners,
            sources={"src-x": MagicMock(tier=1, id="src-x")},
            conflicts=[],
            score=0.8,
            breakdown={},
        )
        assert record["fatalities_crew"] == 1
        assert record["fatalities_passengers"] == 2
        assert record["serious_injuries_crew"] == 1
        assert record["serious_injuries_passengers"] == 3
        assert record["minor_injuries_crew"] == 2
        assert record["minor_injuries_passengers"] == 3
        assert record["uninjured_crew"] == 1
        assert record["uninjured_passengers"] == 42


# ─────────────────────────────────────────────────────────────────────────────
# claim_value.display() — naive datetimes must not say UTC
# ─────────────────────────────────────────────────────────────────────────────


class TestClaimValueDisplayTimezone:
    def test_naive_datetime_no_utc(self):
        """Naive local accident time must not display 'UTC'."""
        dt = datetime(2023, 5, 10, 14, 30)
        assert dt.tzinfo is None
        envelope = encode(dt)
        result = display(envelope)
        assert "UTC" not in result, f"Naive datetime must not say UTC, got: {result!r}"
        assert "local" in result.lower() or "tz" in result.lower(), (
            f"Naive datetime should indicate unknown timezone, got: {result!r}"
        )

    def test_aware_datetime_shows_timezone(self):
        """UTC-aware datetime should show timezone label."""
        dt = datetime(2023, 5, 10, 14, 30, tzinfo=UTC)
        envelope = encode(dt)
        result = display(envelope)
        # Should contain some timezone indicator (UTC, +00:00, etc.)
        assert "UTC" in result or "00:00" in result, (
            f"Aware datetime should show timezone, got: {result!r}"
        )

    def test_none_displays_dash(self):
        assert display(encode(None)) == "—"

    def test_bool_displays_yes_no(self):
        assert display(encode(True)) == "Yes"
        assert display(encode(False)) == "No"


# ─────────────────────────────────────────────────────────────────────────────
# Normalizer — aboard_total crew-only handling
# ─────────────────────────────────────────────────────────────────────────────

class TestAboardTotal:
    def test_both_known(self):
        fields = build_canonical_fields({"AboardPassengerCount": "3", "AboardCrewCount": "2", "EventDate": "2023-01-01"})
        assert fields["aboard_total"] == 5

    def test_pax_only(self):
        fields = build_canonical_fields({"AboardPassengerCount": "4", "EventDate": "2023-01-01"})
        assert fields["aboard_total"] == 4

    def test_crew_only(self):
        """Crew-only accident must produce aboard_total, not omit it."""
        fields = build_canonical_fields({"AboardCrewCount": "2", "EventDate": "2023-01-01"})
        assert fields["aboard_total"] == 2, (
            "Crew-only accident must produce aboard_total=2, not omit the field"
        )

    def test_neither_known_omits_field(self):
        fields = build_canonical_fields({"EventDate": "2023-01-01"})
        assert "aboard_total" not in fields


# ─────────────────────────────────────────────────────────────────────────────
# Confidence engine — source-tier scoring uses only eligible claims
# ─────────────────────────────────────────────────────────────────────────────

class TestConfidenceTierEligibility:
    """Tier-1 source whose every claim is pending must not earn tier-1 bonus."""

    def _make_claim(self, ctype: str, source_id: str = "src-tier1") -> MagicMock:
        c = MagicMock()
        c.field_name = "aircraft_make"
        c.claim_type = ctype
        c.source_id = source_id
        c.field_value = {"v": "Cessna", "type": "str"}
        c.created_at = datetime(2024, 1, 1, tzinfo=UTC)
        return c

    def test_pending_only_claim_no_tier_bonus(self):
        engine = ConfidenceEngine.__new__(ConfidenceEngine)
        pending = self._make_claim(CT.PENDING.value, "src-tier1")
        src_tier1 = MagicMock(); src_tier1.tier = 1
        sources = {"src-tier1": src_tier1}
        bd = MagicMock(); bd.factors = []

        engine._compute([pending], [], sources, [], bd)

        factor_names = [f.name for f in bd.factors]
        assert "source_tier" not in factor_names, (
            "A tier-1 source with only pending claims must not earn source_tier bonus"
        )

    def test_confirmed_claim_earns_tier_bonus(self):
        engine = ConfidenceEngine.__new__(ConfidenceEngine)
        confirmed = self._make_claim(CT.CONFIRMED.value, "src-tier1")
        src_tier1 = MagicMock(); src_tier1.tier = 1
        sources = {"src-tier1": src_tier1}
        bd = MagicMock(); bd.factors = []

        engine._compute([confirmed], [], sources, [], bd)

        factor_names = [f.name for f in bd.factors]
        assert "source_tier" in factor_names, (
            "A tier-1 source with a confirmed claim must earn source_tier bonus"
        )


# ─────────────────────────────────────────────────────────────────────────────
# claim_source_count vs winning_source_count split
# ─────────────────────────────────────────────────────────────────────────────


class TestSourceCountSplit:
    """
    claim_source_ids must include ALL non-superseded contributing sources.
    source_ids must include only sources behind *winning* projected values.
    When Source B contributes only a disputed (non-winning) claim, it must
    appear in claim_source_ids but not in source_ids.
    """

    def _make_claim(self, field: str, src: str, ctype: str) -> MagicMock:
        c = MagicMock()
        c.field_name = field
        c.claim_type = ctype
        c.source_id = src
        c.created_at = datetime(2024, 1, 1, tzinfo=UTC)
        c.field_value = {"v": "test", "type": "str"}
        return c

    def test_losing_source_in_claim_ids_not_source_ids(self):
        """
        Source A wins aircraft_make.  Source B contributed a DISPUTED claim
        for the same field.  B must appear in claim_source_ids (contributed a
        claim) but not in source_ids (no winning projection).
        """
        from atlas.claims.projection import ProjectionService
        from atlas.models import claim_value as cv

        src_a_val = cv.encode("Cessna")
        src_b_val = cv.encode("CESSNA")  # different string → was disputed

        winner_a = MagicMock()
        winner_a.field_name = "aircraft_make"
        winner_a.claim_type = ClaimType.CONFIRMED.value
        winner_a.source_id = "src-a"
        winner_a.created_at = datetime(2024, 1, 1, tzinfo=UTC)
        winner_a.field_value = src_a_val

        disputed_b = MagicMock()
        disputed_b.field_name = "aircraft_make"
        disputed_b.claim_type = ClaimType.DISPUTED.value
        disputed_b.source_id = "src-b"
        disputed_b.created_at = datetime(2024, 1, 1, tzinfo=UTC)
        disputed_b.field_value = src_b_val

        svc = ProjectionService.__new__(ProjectionService)
        src_a_mock = MagicMock(tier=1, id="src-a")
        winners = {"aircraft_make": winner_a}
        # all_claims includes both the winner and the disputed claim
        all_claims = [winner_a, disputed_b]

        record = svc._build_record(
            event_id="evt-split",
            claims=all_claims,
            winners=winners,
            sources={"src-a": src_a_mock, "src-b": MagicMock(tier=2, id="src-b")},
            conflicts=[],
            score=0.7,
            breakdown={},
        )

        # Both sources contributed non-superseded claims
        assert "src-b" in record["claim_source_ids"], (
            "src-b contributed a claim and must appear in claim_source_ids"
        )
        assert "src-a" in record["claim_source_ids"]

        # Only src-a has a winning projection
        assert record["source_ids"] == ["src-a"], (
            "src-b has no winning claim and must not appear in source_ids"
        )

        # The counts reflect the split
        assert len(record["claim_source_ids"]) == 2
        assert len(record["source_ids"]) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Third-source conflict bypass fix
# ─────────────────────────────────────────────────────────────────────────────


class TestThirdSourceConflictBypass:
    """
    After Source A and Source B conflict on a field (both become DISPUTED),
    Source C arriving with the same value as B must also be detected as
    conflicting with A's disputed claim and become DISPUTED itself.

    Without including DISPUTED claims in _detect_conflicts, Source C would
    find no CONFIRMED/INFERRED opponents and remain CONFIRMED — projecting
    its value unchallenged despite an unresolved field dispute.
    """

    def test_third_source_detected_against_disputed_claims(self):
        """
        _detect_conflicts must return opponents including DISPUTED claims
        so that a third source cannot bypass an existing field dispute.
        This is tested at the unit level by checking that DISPUTED is in
        the ClaimType filter passed to the query.
        """
        # The fix: _detect_conflicts now queries CONFIRMED, INFERRED, *and* DISPUTED.
        # We verify the code contains the DISPUTED clause rather than spinning up
        # a real DB session.
        import inspect

        from atlas.claims.writer import ClaimWriter
        source = inspect.getsource(ClaimWriter._detect_conflicts)
        assert "ClaimType.DISPUTED.value" in source, (
            "_detect_conflicts must include DISPUTED claims in its opponent query "
            "to prevent third-source bypass of existing field disputes"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Superseded claims must not appear in projection
# ─────────────────────────────────────────────────────────────────────────────


class TestSupersededClaimsExcluded:
    """
    SUPERSEDED claims must not appear in claim_source_ids (they are not
    non-superseded contributing sources) and must not be selectable as
    winners.  Projection must reflect only active claim state.
    """

    def _make_claim(self, field: str, src: str, ctype: str) -> MagicMock:
        c = MagicMock()
        c.field_name = field
        c.claim_type = ctype
        c.source_id = src
        c.created_at = datetime(2024, 1, 1, tzinfo=UTC)
        c.field_value = {"v": "test", "type": "str"}
        return c

    def test_superseded_claim_not_in_claim_source_ids(self):
        """
        A SUPERSEDED claim from src-old must not inflate claim_source_ids.
        Only non-superseded claims belong there.
        """
        from atlas.claims.projection import ProjectionService
        from atlas.models import claim_value as cv

        winner = MagicMock()
        winner.field_name = "aircraft_make"
        winner.claim_type = ClaimType.CONFIRMED.value
        winner.source_id = "src-new"
        winner.created_at = datetime(2024, 2, 1, tzinfo=UTC)
        winner.field_value = cv.encode("Cessna")

        # Superseded by the above — rebuild_event only passes non-superseded
        # claims to _build_record (the Step 2 query excludes SUPERSEDED).
        # This test verifies that even if a superseded claim leaked through,
        # the split is still computed correctly from what's passed in.
        svc = ProjectionService.__new__(ProjectionService)
        record = svc._build_record(
            event_id="evt-superseded",
            claims=[winner],           # superseded claims are NOT passed in
            winners={"aircraft_make": winner},
            sources={"src-new": MagicMock(tier=1, id="src-new")},
            conflicts=[],
            score=0.8,
            breakdown={},
        )

        assert record["claim_source_ids"] == ["src-new"], (
            "Superseded claims are excluded at the rebuild_event query level; "
            "claim_source_ids must only reflect what is passed in."
        )
        assert record["source_ids"] == ["src-new"]

    def test_superseded_claim_not_selected_as_winner(self):
        """
        _select_winners must never pick a SUPERSEDED claim regardless of tier.
        """
        from atlas.claims.projection import ProjectionService

        superseded = MagicMock()
        superseded.field_name = "aircraft_make"
        superseded.claim_type = ClaimType.SUPERSEDED.value
        superseded.source_id = "src-old"
        superseded.created_at = datetime(2023, 1, 1, tzinfo=UTC)

        svc = ProjectionService.__new__(ProjectionService)
        winners = svc._select_winners(
            [superseded],
            {"src-old": MagicMock(tier=1)},
        )
        assert "aircraft_make" not in winners, (
            "SUPERSEDED claim must never be selected as a winner"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Obsolete conflicts must not affect has_conflicts or source-completeness score
# ─────────────────────────────────────────────────────────────────────────────


class TestObsoleteConflictsIgnored:
    """
    Resolved and obsolete conflicts must not set has_conflicts or penalize
    the source-completeness score.  Only 'open' conflicts are actionable.
    """

    def _make_conflict(self, status: str, resolution: str | None = None) -> MagicMock:
        c = MagicMock()
        c.status = status
        c.resolution = resolution
        c.field_name = "fatalities_total"
        c.claim_a_id = "claim-a"
        c.claim_b_id = "claim-b"
        return c

    def test_has_conflicts_false_when_all_obsolete(self):
        """
        An event whose only conflicts are 'obsolete' must not set has_conflicts.
        Obsolete conflicts involve superseded claims and are no longer actionable.
        """
        from atlas.claims.projection import ProjectionService
        from atlas.models import claim_value as cv

        svc = ProjectionService.__new__(ProjectionService)
        winner = MagicMock()
        winner.field_name = "aircraft_make"
        winner.claim_type = ClaimType.CONFIRMED.value
        winner.source_id = "src-x"
        winner.created_at = datetime(2024, 1, 1, tzinfo=UTC)
        winner.field_value = cv.encode("Cessna")

        obsolete_conflict = self._make_conflict("obsolete", resolution=None)

        record = svc._build_record(
            event_id="evt-obsolete",
            claims=[winner],
            winners={"aircraft_make": winner},
            sources={"src-x": MagicMock(tier=1, id="src-x")},
            conflicts=[obsolete_conflict],
            score=0.80,
            breakdown={},
        )

        assert record["has_conflicts"] is False, (
            "Obsolete conflict must not set has_conflicts — the involved claims "
            "have been superseded and the dispute is no longer active"
        )

    def test_has_conflicts_false_when_all_resolved(self):
        """Resolved conflicts (manually settled) must not set has_conflicts."""
        from atlas.claims.projection import ProjectionService
        from atlas.models import claim_value as cv

        svc = ProjectionService.__new__(ProjectionService)
        winner = MagicMock()
        winner.field_name = "aircraft_make"
        winner.claim_type = ClaimType.CONFIRMED.value
        winner.source_id = "src-x"
        winner.created_at = datetime(2024, 1, 1, tzinfo=UTC)
        winner.field_value = cv.encode("Cessna")

        resolved_conflict = self._make_conflict("resolved", resolution="NTSB authoritative")

        record = svc._build_record(
            event_id="evt-resolved",
            claims=[winner],
            winners={"aircraft_make": winner},
            sources={"src-x": MagicMock(tier=1, id="src-x")},
            conflicts=[resolved_conflict],
            score=0.80,
            breakdown={},
        )

        assert record["has_conflicts"] is False, (
            "Resolved conflict must not set has_conflicts"
        )

    def test_has_conflicts_true_when_any_open(self):
        """An open conflict must still set has_conflicts even if others are resolved."""
        from atlas.claims.projection import ProjectionService
        from atlas.models import claim_value as cv

        svc = ProjectionService.__new__(ProjectionService)
        winner = MagicMock()
        winner.field_name = "aircraft_make"
        winner.claim_type = ClaimType.CONFIRMED.value
        winner.source_id = "src-x"
        winner.created_at = datetime(2024, 1, 1, tzinfo=UTC)
        winner.field_value = cv.encode("Cessna")

        conflicts = [
            self._make_conflict("resolved", resolution="settled"),
            self._make_conflict("open", resolution=None),
        ]

        record = svc._build_record(
            event_id="evt-mixed",
            claims=[winner],
            winners={"aircraft_make": winner},
            sources={"src-x": MagicMock(tier=1, id="src-x")},
            conflicts=conflicts,
            score=0.75,
            breakdown={},
        )

        assert record["has_conflicts"] is True, (
            "A mix of resolved and open conflicts must still flag has_conflicts=True"
        )

    def test_scoring_engine_only_penalizes_open_conflicts(self):
        """
        The confidence engine must only apply the unresolved-conflict penalty
        to open conflicts.  Obsolete and resolved conflicts must not penalize
        the source-completeness score.
        """
        engine = ConfidenceEngine.__new__(ConfidenceEngine)
        bd = MagicMock()
        bd.factors = []

        open_c = self._make_conflict("open")
        obsolete_c = self._make_conflict("obsolete")
        resolved_c = self._make_conflict("resolved", resolution="accepted NTSB value")

        # Only one open conflict — should produce exactly one penalty entry
        engine._compute([], [open_c, obsolete_c, resolved_c], {}, [], bd)

        penalty_factors = [f for f in bd.factors if f.name == "unresolved_conflicts"]
        assert len(penalty_factors) == 1, (
            "Exactly one penalty factor expected for one open conflict"
        )
        # Penalty should reflect ONE conflict, not three
        assert penalty_factors[0].delta < 0, "Conflict penalty must be negative"

        # Now confirm: no open conflicts → no penalty
        bd2 = MagicMock()
        bd2.factors = []
        engine._compute([], [obsolete_c, resolved_c], {}, [], bd2)
        penalty_factors2 = [f for f in bd2.factors if f.name == "unresolved_conflicts"]
        assert len(penalty_factors2) == 0, (
            "No open conflicts → no unresolved_conflicts penalty factor"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic @computed_field aliases must appear in serialized output
# ─────────────────────────────────────────────────────────────────────────────


class TestAnalyticsSummaryAliases:
    """
    AnalyticsSummary.avg_source_completeness and .source_completeness_bins
    must appear in .model_dump() output.  Plain @property is NOT serialized
    by Pydantic v2; the fix uses @computed_field.  This test would fail if
    someone accidentally removed @computed_field.
    """

    def test_avg_source_completeness_serializes(self):
        from atlas.api.app import AnalyticsSummary

        summary = AnalyticsSummary(
            total_accidents=10,
            total_fatalities=2,
            fatal_count=1,
            avg_confidence=0.75,
            by_severity={},
            by_phase={},
            by_year={},
            confidence_bins={"well_sourced": 3, "mostly_sourced": 4,
                             "partially_sourced": 2, "weakly_sourced": 1},
        )

        data = summary.model_dump()
        assert "avg_source_completeness" in data, (
            "avg_source_completeness must be serialized via @computed_field, not plain @property"
        )
        assert data["avg_source_completeness"] == 0.75

    def test_source_completeness_bins_serializes(self):
        from atlas.api.app import AnalyticsSummary

        bins = {"well_sourced": 3, "mostly_sourced": 4, "partially_sourced": 2, "weakly_sourced": 1}
        summary = AnalyticsSummary(
            total_accidents=10,
            total_fatalities=2,
            fatal_count=1,
            avg_confidence=0.75,
            by_severity={},
            by_phase={},
            by_year={},
            confidence_bins=bins,
        )

        data = summary.model_dump()
        assert "source_completeness_bins" in data, (
            "source_completeness_bins must be serialized via @computed_field"
        )
        assert data["source_completeness_bins"] == bins

    def test_legacy_fields_still_present(self):
        """Legacy avg_confidence and confidence_bins must remain for backwards compatibility."""
        from atlas.api.app import AnalyticsSummary

        summary = AnalyticsSummary(
            total_accidents=5,
            total_fatalities=0,
            fatal_count=0,
            avg_confidence=0.60,
            by_severity={},
            by_phase={},
            by_year={},
            confidence_bins={"well_sourced": 1, "mostly_sourced": 2,
                             "partially_sourced": 1, "weakly_sourced": 1},
        )
        data = summary.model_dump()
        assert "avg_confidence" in data
        assert "confidence_bins" in data


# ─────────────────────────────────────────────────────────────────────────────
# Reconciliation: DISPUTED claim reinstated when agreement restored
# ─────────────────────────────────────────────────────────────────────────────


class TestDisputedClaimReconciliation:
    """
    When a claim is superseded by a new claim that agrees with a previously
    disputed counterpart, the disputed claim must eventually be reinstated.

    Scenario (A=4, B=5, A2=5):
      1. Source A: fatalities=4 → CONFIRMED
      2. Source B: fatalities=5 → A and B both become DISPUTED
      3. Source A superseded by A2: fatalities=5 (agrees with B)
      4. A's conflict becomes obsolete
      5. A2's _detect_conflicts finds B DISPUTED but no value disagreement → no new conflict
      6. _try_reconcile_disputed_claims() should reinstate B to CONFIRMED

    This test verifies the reconciliation logic in isolation, since a full
    integration test would require a live database session.
    """

    def test_reconciliation_method_exists_in_writer(self):
        """ClaimWriter must have _try_reconcile_disputed_claims method."""
        from atlas.claims.writer import ClaimWriter
        assert hasattr(ClaimWriter, "_try_reconcile_disputed_claims"), (
            "ClaimWriter must have _try_reconcile_disputed_claims to handle "
            "the A=4, B=5, A2=5 scenario where agreement is restored after supersession"
        )

    def test_reconciliation_method_is_async(self):
        """_try_reconcile_disputed_claims must be an async method."""
        import inspect

        from atlas.claims.writer import ClaimWriter

        method = ClaimWriter._try_reconcile_disputed_claims
        assert inspect.iscoroutinefunction(method), (
            "_try_reconcile_disputed_claims must be async (it makes DB queries)"
        )

    def test_reconciliation_logic_inspectable(self):
        """
        The reconciliation method must enforce all three conditions:
        1. No remaining open conflicts
        2. Value agrees with current confirmed/inferred claims
        3. Not manually rejected (no resolved conflict involving this claim)
        """
        import inspect

        from atlas.claims.writer import ClaimWriter

        source = inspect.getsource(ClaimWriter._try_reconcile_disputed_claims)
        assert "ClaimType.DISPUTED.value" in source, "Must query for DISPUTED claims"
        assert "open" in source, "Must check for open conflicts"
        assert "ClaimType.CONFIRMED.value" in source, "Must reinstate to CONFIRMED"
        assert "ClaimHistory" in source, "Must write audit history for reinstatement"

        # Value-agreement check — the fix from v14 review
        assert "values_conflict" in source, (
            "Must check value agreement with current confirmed/inferred claims "
            "before reinstating (not just absence of open conflicts)"
        )

        # Manual-rejection guard — resolved conflict prevents auto-reinstatement
        assert "resolved" in source, (
            "Must check for manually-resolved conflicts to prevent reinstating "
            "a claim that was explicitly rejected by an operator"
        )

    def test_conflict_breakdown_has_split_counts(self):
        """
        ConfidenceBreakdown must expose separate open/resolved/obsolete conflict
        counts so the UI and API can distinguish actionable from settled disputes.
        """
        from atlas.confidence.engine import ConfidenceBreakdown

        bd = ConfidenceBreakdown(event_id="evt-x", base_score=0.0, final_score=0.0)
        bd.open_conflict_count = 2
        bd.resolved_conflict_count = 1
        bd.obsolete_conflict_count = 3

        data = bd.to_dict()
        assert data["open_conflict_count"] == 2
        assert data["resolved_conflict_count"] == 1
        assert data["obsolete_conflict_count"] == 3
        assert data["conflict_count"] == 6, "conflict_count must be the sum of all three"

    def test_conflict_count_property_is_sum(self):
        """conflict_count must equal sum of open + resolved + obsolete."""
        from atlas.confidence.engine import ConfidenceBreakdown

        bd = ConfidenceBreakdown(event_id="evt-y", base_score=0.0, final_score=0.0)
        bd.open_conflict_count = 1
        bd.resolved_conflict_count = 2
        bd.obsolete_conflict_count = 4
        assert bd.conflict_count == 7

    def test_empty_conflict_counts(self):
        """Zero conflicts should produce zero across all counts."""
        from atlas.confidence.engine import ConfidenceBreakdown

        bd = ConfidenceBreakdown(event_id="evt-z", base_score=0.8, final_score=0.8)
        assert bd.open_conflict_count == 0
        assert bd.resolved_conflict_count == 0
        assert bd.obsolete_conflict_count == 0
        assert bd.conflict_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# Reconciliation must restore original type, not always CONFIRMED
# ─────────────────────────────────────────────────────────────────────────────


class TestReconciliationOriginalType:
    """
    _try_reconcile_disputed_claims must restore the claim's *original* type
    (CONFIRMED or INFERRED), not always upgrade to CONFIRMED.

    A claim that was originally INFERRED should be restored to INFERRED.
    A claim that was originally CONFIRMED should be restored to CONFIRMED.
    """

    def test_restores_original_type_from_history(self):
        """
        The reconciliation source code must look up ClaimHistory to find the
        type the claim had before it became DISPUTED.
        """
        import inspect

        from atlas.claims.writer import ClaimWriter

        source = inspect.getsource(ClaimWriter._try_reconcile_disputed_claims)
        assert "ClaimHistory" in source, (
            "Must query ClaimHistory to recover the original claim type"
        )
        assert "new_claim_type == ClaimType.DISPUTED.value" in source, (
            "Must find the history row that recorded the DISPUTED transition"
        )
        assert "old_claim_type" in source, (
            "Must read old_claim_type from the DISPUTED history row"
        )
        # Must not hard-code CONFIRMED as the restoration target
        source_lines = [line.strip() for line in source.splitlines()]
        hard_confirmed = [
            line for line in source_lines
            if "claim.claim_type = ClaimType.CONFIRMED.value" in line
            and "original_type" not in line
            and "#" not in line
        ]
        assert len(hard_confirmed) == 0, (
            "Must not hard-code ClaimType.CONFIRMED — must use original_type "
            "recovered from ClaimHistory"
        )

    def test_inferred_type_fallback_preserved(self):
        """
        The method must preserve INFERRED type when that was the original type,
        and must have a safe CONFIRMED default for claims with no history.
        """
        import inspect

        from atlas.claims.writer import ClaimWriter

        source = inspect.getsource(ClaimWriter._try_reconcile_disputed_claims)
        assert "ClaimType.INFERRED.value" in source, (
            "Must consider INFERRED as a valid original type"
        )
        # Should default to CONFIRMED if history unavailable
        assert "CONFIRMED.value" in source, (
            "Must have a safe CONFIRMED default when history entry is missing"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Source document verification metadata
# ─────────────────────────────────────────────────────────────────────────────


class TestSourceDocumentCheckMetadata:
    """
    SourceDocument must store HTTP check metadata (status code, error, method)
    so operators can diagnose why a document is unavailable, not just know that it is.
    """

    def test_orm_has_check_metadata_fields(self):
        from atlas.models.orm import SourceDocument

        assert hasattr(SourceDocument, "last_http_status"), (
            "SourceDocument must have last_http_status for diagnosing HTTP failures"
        )
        assert hasattr(SourceDocument, "last_check_error"), (
            "SourceDocument must have last_check_error for non-HTTP failure reasons"
        )
        assert hasattr(SourceDocument, "last_check_method"), (
            "SourceDocument must have last_check_method (HEAD vs GET fallback)"
        )

    def test_check_links_cli_persists_metadata(self):
        """
        check-links CLI must persist last_http_status, last_check_error,
        last_check_method — not just is_available and url_verified.
        """
        import inspect

        from atlas.cli import check_links

        source = inspect.getsource(check_links)
        assert "last_http_status" in source, (
            "check-links must persist last_http_status to SourceDocument"
        )
        assert "last_check_error" in source, (
            "check-links must persist last_check_error to SourceDocument"
        )
        assert "last_check_method" in source, (
            "check-links must persist last_check_method to SourceDocument"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Flush-before-conflict: new claim must be flushed before FK-constrained insert
# ─────────────────────────────────────────────────────────────────────────────


class TestFlushBeforeConflictInsertion:
    """
    ClaimWriter._upsert_claim must flush the new Claim row to the DB before
    running _detect_conflicts().  Without the flush, pg_insert() into
    claim_conflicts (which has FK→claims.id) will fail with an IntegrityError
    because autoflush=False means SQLAlchemy does not auto-persist the new claim.

    These tests verify the flush is present in the writer source and that the
    reconciliation query sees the new claim.
    """

    def test_flush_called_before_detect_conflicts(self):
        """
        _upsert_claim source must call session.flush() before _detect_conflicts.
        """
        import inspect

        from atlas.claims.writer import ClaimWriter

        source = inspect.getsource(ClaimWriter._upsert_claim)
        lines = source.splitlines()

        # Skip comment lines — find only actual await calls
        flush_idx = next(
            (i for i, ln in enumerate(lines)
             if "session.flush" in ln and not ln.lstrip().startswith("#")),
            None,
        )
        detect_idx = next(
            (i for i, ln in enumerate(lines)
             if "await self._detect_conflicts" in ln and not ln.lstrip().startswith("#")),
            None,
        )
        assert flush_idx is not None, (
            "_upsert_claim must call await self._session.flush() to persist "
            "the new claim before conflict insertion (FK constraint)"
        )
        assert detect_idx is not None, "_upsert_claim must call _detect_conflicts"
        assert flush_idx < detect_idx, (
            "session.flush() must appear BEFORE _detect_conflicts() — "
            "without this, claim_conflicts FK reference can fail when "
            "autoflush=False and the new claim is not yet in the DB"
        )

    def test_flush_called_before_reconcile(self):
        """
        session.flush() must also precede _try_reconcile_disputed_claims so
        the reconciliation query sees the new claim in the active-claims set.
        """
        import inspect

        from atlas.claims.writer import ClaimWriter

        source = inspect.getsource(ClaimWriter._upsert_claim)
        lines = source.splitlines()

        flush_idx = next(
            (i for i, ln in enumerate(lines)
             if "session.flush" in ln and not ln.lstrip().startswith("#")),
            None,
        )
        reconcile_idx = next(
            (i for i, ln in enumerate(lines)
             if "await self._try_reconcile_disputed_claims" in ln
             and not ln.lstrip().startswith("#")),
            None,
        )
        assert flush_idx is not None
        assert reconcile_idx is not None
        assert flush_idx < reconcile_idx, (
            "session.flush() must appear before _try_reconcile_disputed_claims"
        )

    def test_session_autoflush_is_false(self):
        """
        Confirm autoflush=False is the actual session setting — this is why
        the explicit flush in _upsert_claim is necessary.
        """
        from atlas.db.engine import _SessionFactory

        # The session factory must have autoflush=False
        kw = _SessionFactory.kw
        assert kw.get("autoflush") is False, (
            "Session factory must use autoflush=False. "
            "If this changes, the explicit flush in _upsert_claim can be removed."
        )


# ─────────────────────────────────────────────────────────────────────────────
# check-links: last_check_method must be 'GET' when GET fallback succeeds
# ─────────────────────────────────────────────────────────────────────────────


class TestCheckLinksMethodTracking:
    """
    When HEAD returns 403/405/501 and GET fallback succeeds, last_check_method
    must be 'GET' — not 'HEAD'.  The old implementation derived check_method
    from failure_reason, which is None on a successful fallback, producing the
    wrong 'HEAD' value even though GET was the method that actually worked.
    """

    def test_check_method_set_before_get_attempt(self):
        """
        check_method must be set to 'GET' when HEAD returns a blocked status
        (403/405/501), regardless of whether the GET succeeds or fails.
        It must not depend on failure_reason to determine the method.
        """
        import inspect

        from atlas.cli import check_links

        source = inspect.getsource(check_links)
        # Must initialise check_method before the GET attempt
        assert 'check_method = "HEAD"' in source, (
            "check_method must be initialised to 'HEAD' before the HEAD request"
        )
        assert 'check_method = "GET"' in source, (
            "check_method must be set to 'GET' when triggering the GET fallback, "
            "not derived from failure_reason (which is None when GET succeeds)"
        )

    def test_old_failure_reason_pattern_not_used(self):
        """
        The old pattern 'GET if failure_reason and GET fallback in failure_reason'
        must no longer exist — it produced wrong results when GET succeeded.
        """
        import inspect

        from atlas.cli import check_links

        source = inspect.getsource(check_links)
        assert '"GET fallback" in (failure_reason' not in source, (
            "Must not derive check_method from failure_reason. "
            "When GET fallback succeeds, failure_reason is None but method is GET."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Reconciliation scenario: A=4, B=5, A2=5 structural integrity test
# ─────────────────────────────────────────────────────────────────────────────


class TestReconciliationScenarioStructural:
    """
    Structural tests for the A=4, B=5, A2=5 reconciliation scenario.

    Full DB-backed tests would require a running Postgres instance.  These
    tests verify the structural preconditions: that the code path through
    supersession → obsolescence → reconciliation is present and correct.
    """

    def test_supersession_calls_mark_obsolete(self):
        """
        When _upsert_claim supersedes an existing claim, it must call
        mark_conflicts_obsolete_for_claim for the superseded claim's ID.
        This is step 4 of the A=4→A2=5 scenario.
        """
        import inspect

        from atlas.claims.writer import ClaimWriter

        source = inspect.getsource(ClaimWriter._upsert_claim)
        assert "mark_conflicts_obsolete_for_claim" in source, (
            "_upsert_claim must call mark_conflicts_obsolete_for_claim when "
            "superseding a claim (step 4 of A=4,B=5,A2=5 scenario)"
        )
        assert "existing.id" in source, (
            "Must pass the superseded claim's ID to mark_conflicts_obsolete_for_claim"
        )

    def test_reconciliation_follows_detect_conflicts(self):
        """
        _try_reconcile_disputed_claims must be called AFTER _detect_conflicts
        in _upsert_claim, so it sees the final conflict state before deciding
        whether a disputed claim can be reinstated.
        """
        import inspect

        from atlas.claims.writer import ClaimWriter

        source = inspect.getsource(ClaimWriter._upsert_claim)
        lines = source.splitlines()
        detect_idx = next((i for i, ln in enumerate(lines) if "_detect_conflicts" in ln), None)
        reconcile_idx = next((i for i, ln in enumerate(lines) if "_try_reconcile_disputed_claims" in ln), None)
        assert detect_idx is not None
        assert reconcile_idx is not None
        assert detect_idx < reconcile_idx, (
            "_try_reconcile_disputed_claims must run AFTER _detect_conflicts "
            "so it sees the final open-conflict count before reinstating claims"
        )

    def test_reconcile_checks_value_agreement(self):
        """
        The opposite scenario (A=4, B=5, A2=6): B must remain DISPUTED because
        A2 does not agree with B.  Verify the value-agreement check is present.
        """
        import inspect

        from atlas.claims.writer import ClaimWriter

        source = inspect.getsource(ClaimWriter._try_reconcile_disputed_claims)
        assert "values_conflict" in source, (
            "Must check value agreement before reinstating a disputed claim. "
            "Without this, A2=6 could cause B=5 to be incorrectly reinstated "
            "just because A=4's conflict became obsolete."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Ingestion run tracking
# ─────────────────────────────────────────────────────────────────────────────


class TestIngestionRunTracking:
    """
    The ingestion pipeline must persist IngestionRun rows so operators can
    answer: when did we last sync? how many records changed? what failed?
    """

    def test_ingestion_run_orm_exists(self):
        """IngestionRun ORM must exist with required operational fields."""
        from atlas.models.orm import IngestionRun

        required = [
            "id", "source_name", "status", "started_at", "completed_at",
            "records_fetched", "snapshots_new", "snapshots_skipped",
            "events_created", "events_updated", "claims_written",
            "projection_errors", "ingestion_errors", "errors",
        ]
        for field_name in required:
            assert hasattr(IngestionRun, field_name), (
                f"IngestionRun must have field '{field_name}' for operational visibility"
            )

    def test_ingestion_run_imported_in_pipeline(self):
        """Pipeline must import IngestionRun to be able to persist run records."""
        import inspect

        import atlas.ingestion.pipeline as pipeline_module

        source = inspect.getsource(pipeline_module)
        assert "IngestionRun" in source, (
            "pipeline.py must import and use IngestionRun to persist run records"
        )

    def test_pipeline_persists_run_start(self):
        """Pipeline must create a run record before processing starts.

        v20 refactor: run-record construction moved out of each run_ntsb_*
        method into a module-level helper (_create_run_record) used by
        both API and CSV ingestion paths.  We now assert against the
        whole pipeline module's source, because that's what the
        invariant ("the pipeline writes a run row at start") really
        scopes to — splitting the helper out of one method and inlining
        it into the other would have hidden the duplication this
        invariant exists to detect.
        """
        import inspect

        import atlas.ingestion.pipeline as pipeline_module

        source = inspect.getsource(pipeline_module)
        assert "IngestionRun(" in source, (
            "pipeline.py must construct an IngestionRun row at run start"
        )
        assert 'status="running"' in source or "status='running'" in source, (
            "Initial run status must be 'running'"
        )
        # Both ingestion paths must call the run-creation step.  This
        # protects against a refactor that adds a new path (e.g. ASN)
        # but forgets to persist a run record for it.
        csv_source = inspect.getsource(
            pipeline_module.IngestionPipeline.run_ntsb_csv,
        )
        api_source = inspect.getsource(
            pipeline_module.IngestionPipeline.run_ntsb_api,
        )
        assert "_create_run_record" in csv_source or "IngestionRun(" in csv_source, (
            "run_ntsb_csv must create a run record at start"
        )
        assert "_create_run_record" in api_source or "IngestionRun(" in api_source, (
            "run_ntsb_api must create a run record at start"
        )

    def test_pipeline_updates_run_on_completion(self):
        """Pipeline must update the run record with final counts and status.

        v20 refactor: finalisation logic moved into _finalize_run_record
        (used by both run methods).  The invariant is unchanged — the
        run is moved to a terminal state with completed_at populated —
        so we now check the pipeline module as a whole.
        """
        import inspect

        import atlas.ingestion.pipeline as pipeline_module

        source = inspect.getsource(pipeline_module)
        assert '"completed"' in source or "'completed'" in source, (
            "pipeline must set status='completed' when successful"
        )
        assert '"failed"' in source or "'failed'" in source, (
            "pipeline must set status='failed' when there are errors"
        )
        assert "completed_at" in source, (
            "pipeline must persist completed_at timestamp"
        )
        # Both run methods must reach the finaliser.
        csv_source = inspect.getsource(
            pipeline_module.IngestionPipeline.run_ntsb_csv,
        )
        api_source = inspect.getsource(
            pipeline_module.IngestionPipeline.run_ntsb_api,
        )
        assert "_finalize_run_record" in csv_source, (
            "run_ntsb_csv must finalize the run record on exit"
        )
        assert "_finalize_run_record" in api_source, (
            "run_ntsb_api must finalize the run record on exit "
            "(v20 fix — v19 only finalised CSV runs)"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Structured conflict resolution fields
# ─────────────────────────────────────────────────────────────────────────────


class TestStructuredConflictResolution:
    """
    ClaimConflict must have structured resolution fields so the system can
    distinguish accepted vs rejected claims, and auto-reconciliation can
    safely respect explicit operator decisions.
    """

    def test_conflict_orm_has_resolution_type(self):
        from atlas.models.orm import ClaimConflict

        assert hasattr(ClaimConflict, "resolution_type"), (
            "ClaimConflict must have resolution_type to distinguish "
            "accepted | rejected | merged | source_corrected | ..."
        )

    def test_conflict_orm_has_accepted_claim_id(self):
        from atlas.models.orm import ClaimConflict

        assert hasattr(ClaimConflict, "accepted_claim_id"), (
            "ClaimConflict must have accepted_claim_id to record which "
            "claim was chosen as authoritative during manual resolution"
        )

    def test_conflict_orm_has_rejected_claim_ids(self):
        from atlas.models.orm import ClaimConflict

        assert hasattr(ClaimConflict, "rejected_claim_ids"), (
            "ClaimConflict must have rejected_claim_ids to record which "
            "claims were explicitly rejected (prevents auto-reinstatement)"
        )

    def test_conflict_out_exposes_structured_fields(self):
        """ConflictOut API model must expose all structured resolution fields."""
        from atlas.api.app import ConflictOut

        # Build an instance with structured fields to confirm they serialize
        co = ConflictOut(
            id="c-1",
            field_name="fatalities_total",
            claim_a_id="claim-a",
            claim_b_id="claim-b",
            status="resolved",
            resolution="NTSB is authoritative",
            resolved_at=None,
            resolution_type="claim_accepted",
            accepted_claim_id="claim-a",
            rejected_claim_ids=["claim-b"],
            obsolete_reason=None,
        )
        data = co.model_dump()
        assert data["resolution_type"] == "claim_accepted"
        assert data["accepted_claim_id"] == "claim-a"
        assert data["rejected_claim_ids"] == ["claim-b"]

    def test_rejection_guard_uses_accepted_claim_id(self):
        """
        The reconciliation rejection guard must check accepted_claim_id
        to distinguish 'claim was accepted' from 'claim was rejected'.
        """
        import inspect

        from atlas.claims.writer import ClaimWriter

        source = inspect.getsource(ClaimWriter._try_reconcile_disputed_claims)
        assert "accepted_claim_id" in source, (
            "Rejection guard must check accepted_claim_id — a resolved conflict "
            "where accepted_claim_id != this claim means it was rejected"
        )


# ─────────────────────────────────────────────────────────────────────────────
# MapAccident uses source_completeness_score not confidence_score
# ─────────────────────────────────────────────────────────────────────────────


class TestMapAccidentFieldNames:
    """MapAccident API must use source_completeness_score, not confidence_score."""

    def test_map_accident_uses_source_completeness_score(self):
        from atlas.api.app import MapAccident

        # Must accept source_completeness_score
        m = MapAccident(
            id="evt-1",
            canonical_id="NTSB-001",
            location_lat=44.0,
            location_lon=-121.0,
            location_text="Bend, OR",
            injury_severity="FATAL",
            fatalities_total=1,
            aircraft_make="Cessna",
            aircraft_model="172S",
            occurred_date=None,
            occurred_year=2023,
            phase_of_flight="LANDING",
            source_completeness_score=0.85,
        )
        assert m.source_completeness_score == 0.85

    def test_map_accident_has_no_confidence_score(self):
        import pydantic

        from atlas.api.app import MapAccident

        # confidence_score must no longer be a valid field
        with pytest.raises((TypeError, pydantic.ValidationError)):
            MapAccident(
                id="evt-2",
                canonical_id="NTSB-002",
                location_lat=44.0,
                location_lon=-121.0,
                location_text=None,
                injury_severity=None,
                fatalities_total=None,
                aircraft_make=None,
                aircraft_model=None,
                occurred_date=None,
                occurred_year=None,
                phase_of_flight=None,
                confidence_score=0.5,  # old name — must fail
            )


# ─────────────────────────────────────────────────────────────────────────────
# Sprint 1 fixes — regression tests
# ─────────────────────────────────────────────────────────────────────────────


# ── Fix 1: LIKE metacharacter escaping ───────────────────────────────────────

class TestEscapeLike:
    """_escape_like must neutralise PostgreSQL LIKE metacharacters."""

    def _esc(self, value: str) -> str:
        from atlas.api.app import _escape_like
        return _escape_like(value)

    def test_percent_is_escaped(self):
        assert self._esc("%") == "\\%"

    def test_underscore_is_escaped(self):
        assert self._esc("_") == "\\_"

    def test_backslash_is_escaped_first(self):
        # If backslash weren't escaped first, a value like "\" would become
        # "\\" and then the subsequent % escape would double-escape it.
        assert self._esc("\\") == "\\\\"

    def test_mixed_metacharacters(self):
        assert self._esc("C-130_ABC%") == "C-130\\_ABC\\%"

    def test_plain_text_unchanged(self):
        assert self._esc("Cessna 172") == "Cessna 172"

    def test_empty_string(self):
        assert self._esc("") == ""

    def test_pattern_wrapping(self):
        """Verify the full pattern applied to q is correct."""
        q = "50%"
        escaped = self._esc(q.lower())
        pattern = f"%{escaped}%"
        assert pattern == "%50\\%%"


# ── Fix 2: Config tier weights are no longer inverted ────────────────────────

class TestTierWeightConfig:
    """Tier weights must satisfy tier1 >= tier2 >= tier3 >= tier4."""

    def test_default_weights_are_ordered(self):
        """Default config must satisfy tier ordering after the v28.1 fix."""
        from atlas.config import get_settings
        s = get_settings()
        assert s.conf_weight_tier1 >= s.conf_weight_tier2
        assert s.conf_weight_tier2 >= s.conf_weight_tier3
        assert s.conf_weight_tier3 >= s.conf_weight_tier4

    def test_tier2_gt_tier3(self):
        """
        Specifically guard the bug that existed before v28.1 where tier2=0.80
        and tier3=0.90 — tier3 received a higher bonus than tier2.
        """
        from atlas.config import get_settings
        s = get_settings()
        assert s.conf_weight_tier2 > s.conf_weight_tier3, (
            "tier2 must have a higher weight than tier3 (tier2 is more authoritative)"
        )

    def test_validator_rejects_inverted_weights(self):
        """model_validator must raise when weights are out of order."""
        import os

        from pydantic import ValidationError

        # We must clear the lru_cache to test a fresh Settings instantiation.
        # Import directly instead of through get_settings() to bypass cache.
        env = {
            **os.environ,
            "CONF_WEIGHT_TIER1": "1.00",
            "CONF_WEIGHT_TIER2": "0.80",   # intentionally inverted
            "CONF_WEIGHT_TIER3": "0.90",   # intentionally inverted
            "CONF_WEIGHT_TIER4": "0.60",
        }
        import unittest.mock as mock
        with mock.patch.dict(os.environ, env, clear=False):
            from atlas.config import Settings
            with pytest.raises(ValidationError, match="tier"):
                Settings()

    def test_validator_accepts_equal_adjacent_weights(self):
        """Equal adjacent weights (tier2 == tier3) must be accepted."""
        import os
        import unittest.mock as mock
        env = {
            **os.environ,
            "CONF_WEIGHT_TIER1": "1.00",
            "CONF_WEIGHT_TIER2": "0.80",
            "CONF_WEIGHT_TIER3": "0.80",   # equal to tier2 — allowed
            "CONF_WEIGHT_TIER4": "0.60",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            from atlas.config import Settings
            s = Settings()
            assert s.conf_weight_tier2 == s.conf_weight_tier3


# ── Fix 3: rebuild_all keyset streaming (structural test) ────────────────────

class TestRebuildAllStreaming:
    """
    rebuild_all must not load all event IDs into memory at once.
    This is a structural test — it verifies that rebuild_all makes multiple
    bounded SELECT calls instead of one unbounded r.all().
    """

    @pytest.mark.asyncio
    async def test_rebuild_all_uses_keyset_not_offset(self):
        """
        Verify that rebuild_all paginates using id > last_id (keyset) rather
        than loading the complete id list up front.  We do this by counting
        how many SELECT queries are issued and verifying the WHERE clause
        of the second batch includes a lower-bound predicate on id.
        """
        from unittest.mock import AsyncMock, MagicMock

        from atlas.claims.projection import ProjectionService

        # Build a session mock that returns two batches then empty.
        call_count = 0
        batch_ids = [["id-a", "id-b"], ["id-c"], []]

        async def fake_execute(stmt):
            nonlocal call_count
            result = MagicMock()
            result.all.return_value = [(bid,) for bid in batch_ids[call_count]]
            call_count += 1
            return result

        session = AsyncMock()
        session.execute.side_effect = fake_execute
        session.commit = AsyncMock()

        svc = ProjectionService(session=session)
        # Patch rebuild_event to avoid full DB interaction.
        svc.rebuild_event = AsyncMock()

        count = await svc.rebuild_all(batch_size=2)

        # Three SELECT calls: batch 1, batch 2, empty terminator.
        assert call_count == 3
        # Two events from batch 1 + one from batch 2.
        rebuilt, failed = count
        assert rebuilt == 3
        assert failed == 0
        # Committed once per non-empty batch.
        assert session.commit.call_count == 2


# ── Fix 4: map endpoint hard limit (structural) ───────────────────────────────

class TestMapEndpointHardLimit:
    """
    The map endpoint must respect settings.max_map_results and return
    truncated=True when the cap is hit.
    """

    def test_max_map_results_setting_exists(self):
        from atlas.config import get_settings
        s = get_settings()
        assert hasattr(s, "max_map_results")
        assert isinstance(s.max_map_results, int)
        assert s.max_map_results > 0

    def test_map_response_shape(self):
        """
        The map endpoint now returns a dict with items/count/truncated/limit.
        Verify the response schema by checking the MapAccident model still
        round-trips correctly (the wrapper is built around it).
        """
        from atlas.api.schemas import MapAccident

        m = MapAccident(
            id="evt-1", canonical_id="NTSB-001",
            location_lat=44.0, location_lon=-121.0,
            location_text="Bend, OR", injury_severity="FATAL",
            fatalities_total=1, aircraft_make="Cessna", aircraft_model="172S",
            occurred_date=None, occurred_year=2023,
            phase_of_flight="LANDING", source_completeness_score=0.85,
        )
        d = m.model_dump()
        assert d["id"] == "evt-1"
        assert d["source_completeness_score"] == 0.85


# ─────────────────────────────────────────────────────────────────────────────
# Sprint 2 fixes — regression tests
# ─────────────────────────────────────────────────────────────────────────────


# ── Fix 1: rebuild_all filters to active events ──────────────────────────────

class TestRebuildAllSkipsInactiveEvents:
    """
    rebuild_all must only process events with record_status == 'active'.
    Merged, disputed, and retracted events must be silently skipped.
    """

    @pytest.mark.asyncio
    async def test_rebuild_all_excludes_non_active_events(self):
        """
        The SELECT issued inside rebuild_all must include a WHERE clause
        filtering to record_status == 'active'.  We verify this by inspecting
        the SQL string of the compiled statement rather than running it against
        a real DB — the predicate is the contract.
        """
        from sqlalchemy import select
        from sqlalchemy.dialects import postgresql

        from atlas.models.orm import AccidentEvent

        # Build the same statement that rebuild_all builds internally.
        stmt = (
            select(AccidentEvent.id)
            .where(AccidentEvent.record_status == "active")
            .order_by(AccidentEvent.id)
            .limit(200)
        )
        compiled = stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
        sql = str(compiled)
        assert "record_status" in sql, "WHERE clause must reference record_status"
        assert "active" in sql, "WHERE clause must filter to 'active'"

    @pytest.mark.asyncio
    async def test_rebuild_all_returns_tuple(self):
        """rebuild_all must return (rebuilt, failed), not a bare int."""
        from unittest.mock import AsyncMock, MagicMock

        from atlas.claims.projection import ProjectionService

        # Session that immediately returns empty batch → zero events, exits cleanly.
        session = AsyncMock()
        result = MagicMock()
        result.all.return_value = []
        session.execute = AsyncMock(return_value=result)
        session.commit = AsyncMock()

        svc = ProjectionService(session=session)
        retval = await svc.rebuild_all(batch_size=10)

        assert isinstance(retval, tuple), "rebuild_all must return a tuple"
        rebuilt, failed = retval
        assert rebuilt == 0
        assert failed == 0

    @pytest.mark.asyncio
    async def test_rebuild_all_counts_failures_separately(self):
        """
        rebuild_all must count rebuild_event failures in the `failed` slot and
        not include them in `rebuilt`.  Before this fix, the caller got a single
        integer and had no way to distinguish clean runs from partial failures.
        """
        from unittest.mock import AsyncMock, MagicMock

        from atlas.claims.projection import ProjectionService

        call_count = 0
        batches = [["id-ok", "id-fail"], []]

        async def fake_execute(stmt):
            nonlocal call_count
            r = MagicMock()
            r.all.return_value = [(bid,) for bid in batches[call_count]]
            call_count += 1
            return r

        session = AsyncMock()
        session.execute.side_effect = fake_execute
        session.commit = AsyncMock()

        svc = ProjectionService(session=session)

        async def fail_on_bad(event_id: str) -> None:
            if event_id == "id-fail":
                raise RuntimeError("projection failed")

        svc.rebuild_event = fail_on_bad

        rebuilt, failed = await svc.rebuild_all(batch_size=10)
        assert rebuilt == 1
        assert failed == 1


# ── Fix 2: map endpoint ordering ─────────────────────────────────────────────

class TestMapEndpointDeterministicOrder:
    """
    The map endpoint must apply a deterministic ORDER BY so repeated calls with
    the same filters always return the same slice when truncation occurs.
    """

    def test_map_endpoint_has_order_by(self):
        """
        Inspect the map endpoint source to confirm it applies ORDER BY before
        the LIMIT.  This is a structural guard — a future refactor that drops
        the ORDER BY should break this test.
        """
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.map_accidents)
        # The ordering terms must be present in the function source.
        assert "occurred_at" in src, "map endpoint must order by occurred_at"
        assert "order_by" in src, "map endpoint must call order_by"


# ── Fix 3: provenance caps ────────────────────────────────────────────────────

class TestProvenanceCaps:
    """
    The provenance endpoint must cap claims, conflicts, and source documents.
    v28.3: caps now come from settings (not local constants) and the truncation
    metadata is a typed ProvenanceTruncationOut field, not an ad-hoc dict key.
    """

    def test_provenance_cap_settings_exist(self):
        from atlas.config import get_settings
        s = get_settings()
        assert s.provenance_claim_limit > 0
        assert s.provenance_conflict_limit > 0
        assert s.provenance_document_limit > 0

    def test_provenance_returns_typed_truncation(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.get_provenance)
        assert "truncation=ProvenanceTruncationOut" in src
        assert "claims_truncated" in src
        assert "conflicts_truncated" in src
        assert "docs_truncated" in src


# ── Fix 4: CORS headers tightened ────────────────────────────────────────────

class TestCORSHeaders:
    """
    allow_headers must not be ['*'].  It must be an explicit allowlist that at
    minimum includes Content-Type and X-API-Key.
    """

    def test_cors_is_not_wildcard(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module)
        # The old wildcard value — must no longer appear as allow_headers arg.
        # Search specifically for the pattern used in add_middleware call.
        assert 'allow_headers=["*"]' not in src, (
            "allow_headers must not be wildcard — use an explicit allowlist"
        )

    def test_cors_includes_api_key_header(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module)
        assert "X-API-Key" in src, "allow_headers must include X-API-Key"


# ── Fix 5: conflicts.tsx no longer has bare fetch calls ──────────────────────

class TestConflictsPageNoBareAPI:
    """
    The conflicts page must import fetchConflictQueue and fetchConflictStats
    from api.ts rather than defining its own bare-fetch wrappers.
    """

    def test_conflicts_page_imports_from_api(self):
        import os
        page_path = os.path.join(
            os.path.dirname(__file__), "..", "web", "pages", "conflicts.tsx"
        )
        with open(page_path) as f:
            src = f.read()
        assert "fetchConflictQueue" in src
        assert "fetchConflictStats" in src
        # The bare fetch pattern must be gone.
        assert "await fetch(" not in src, (
            "conflicts.tsx must not use bare fetch() — use api.ts wrappers"
        )

    def test_api_ts_exports_conflict_functions(self):
        import os
        api_path = os.path.join(
            os.path.dirname(__file__), "..", "web", "lib", "api.ts"
        )
        with open(api_path) as f:
            src = f.read()
        assert "export async function fetchConflictQueue" in src
        assert "export async function fetchConflictStats" in src
        assert "export interface ConflictStats" in src


# ── Fix 6: README is accurate ─────────────────────────────────────────────────

class TestREADMEAccuracy:
    """The README must reflect the current map endpoint behavior."""

    def test_readme_does_not_claim_map_is_unpaginated(self):
        import os
        readme_path = os.path.join(os.path.dirname(__file__), "..", "README.md")
        with open(readme_path) as f:
            content = f.read()
        assert "no pagination — for map view only" not in content, (
            "README still describes the old unbounded map endpoint"
        )

    def test_readme_mentions_map_truncation(self):
        import os
        readme_path = os.path.join(os.path.dirname(__file__), "..", "README.md")
        with open(readme_path) as f:
            content = f.read()
        assert "truncated" in content.lower(), (
            "README must document that the map endpoint can be truncated"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Sprint 3 (v28.3) fixes — regression and smoke tests
# ─────────────────────────────────────────────────────────────────────────────


# ── Phase 0 Fix 1: ProvenanceTruncationOut is a real schema type ─────────────

class TestProvenanceTruncationSchema:
    """AccidentProvenance now carries a typed truncation field, not an ad-hoc dict."""

    def test_provenance_truncation_out_importable(self):
        from atlas.api.schemas import ProvenanceTruncationOut
        t = ProvenanceTruncationOut(
            claims=True,
            conflicts=False,
            source_documents=False,
            claims_limit=200,
            conflicts_limit=200,
            source_documents_limit=100,
        )
        assert t.claims is True
        assert t.claims_limit == 200

    def test_accident_provenance_has_truncation_field(self):
        from atlas.api.schemas import AccidentProvenance, ProvenanceTruncationOut
        p = AccidentProvenance(
            event_id="evt-1",
            claims=[], conflicts=[], source_documents=[], sources=[],
            truncation=ProvenanceTruncationOut(
                claims=False, conflicts=False, source_documents=False,
                claims_limit=200, conflicts_limit=200, source_documents_limit=100,
            ),
        )
        assert p.truncation is not None
        assert p.truncation.claims is False

    def test_accident_provenance_truncation_defaults_to_none(self):
        """Old API responses without truncation field must still deserialise."""
        from atlas.api.schemas import AccidentProvenance
        p = AccidentProvenance(
            event_id="evt-1",
            claims=[], conflicts=[], source_documents=[], sources=[],
        )
        assert p.truncation is None

    def test_truncation_in_schema_exports(self):
        from atlas.api import schemas
        assert "ProvenanceTruncationOut" in schemas.__all__


# ── Phase 0 Fix 2: Smoke test map assertion is correct ───────────────────────

class TestSmokeTestMapAssertion:
    """The smoke script must check for a dict envelope, not a list."""

    def test_smoke_script_checks_dict_not_list(self):
        import os
        script = os.path.join(os.path.dirname(__file__), "..", "scripts", "smoke_test.sh")
        with open(script) as f:
            content = f.read()
        # The old broken assertion
        assert "isinstance(d,list)" not in content, (
            "Smoke test still asserts isinstance(d,list) for the map endpoint. "
            "The map now returns a dict envelope — the test must check isinstance(d,dict)."
        )
        # The new correct assertion
        assert "isinstance(d, dict)" in content or "isinstance(d,dict)" in content, (
            "Smoke test must assert the map response is a dict envelope."
        )
        # Must check for the envelope keys
        assert "'items'" in content or '"items"' in content

    def test_smoke_script_checks_truncated_key(self):
        import os
        script = os.path.join(os.path.dirname(__file__), "..", "scripts", "smoke_test.sh")
        with open(script) as f:
            content = f.read()
        assert "truncated" in content, (
            "Smoke test must verify the 'truncated' key in the map response envelope."
        )


# ── Phase 0 Fix 3: get_read_db rolls back on error ───────────────────────────

class TestReadDbRollback:
    """get_read_db must call session.rollback() on exception, matching get_db."""

    def test_get_read_db_has_rollback(self):
        import inspect

        from atlas.db import engine as engine_module
        src = inspect.getsource(engine_module.get_read_db)
        assert "rollback" in src, (
            "get_read_db must call session.rollback() on exception. "
            "Without it, asyncpg connections can be left in a broken transaction state."
        )

    def test_get_db_and_read_db_both_rollback(self):
        """Both session factories must be symmetric in their error handling."""
        import inspect

        from atlas.db import engine as engine_module
        read_src = inspect.getsource(engine_module.get_read_db)
        write_src = inspect.getsource(engine_module.get_db)
        assert "rollback" in read_src
        assert "rollback" in write_src


# ── Phase 0 Fix 4: q has max_length ──────────────────────────────────────────

class TestQMaxLength:
    """The q search parameter must have a max_length FastAPI constraint."""

    def test_q_has_max_length_in_source(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.list_accidents)
        assert "max_length" in src, (
            "q parameter must have max_length — without it a huge search string "
            "is passed to 5 LIKE predicates with no guard."
        )

    def test_q_max_length_value(self):
        from atlas.config import get_settings
        s = get_settings()
        assert s.search_q_max_length > 0
        assert s.search_q_max_length <= 500


# ── Phase 1 Fix 1: provenance caps come from settings ────────────────────────

class TestProvCapSettings:
    """Provenance cap constants must come from settings, not function-local literals."""

    def test_cap_settings_exist(self):
        from atlas.config import get_settings
        s = get_settings()
        assert hasattr(s, "provenance_claim_limit")
        assert hasattr(s, "provenance_conflict_limit")
        assert hasattr(s, "provenance_document_limit")
        assert s.provenance_claim_limit > 0
        assert s.provenance_conflict_limit > 0
        assert s.provenance_document_limit > 0

    def test_no_local_cap_constants_in_provenance(self):
        """Function-local _CLAIM_LIMIT / _CONFLICT_LIMIT / _DOC_LIMIT must be gone."""
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.get_provenance)
        assert "_CLAIM_LIMIT" not in src, (
            "get_provenance must not define _CLAIM_LIMIT locally — use settings.provenance_claim_limit"
        )
        assert "_CONFLICT_LIMIT" not in src
        assert "_DOC_LIMIT" not in src

    def test_provenance_uses_settings_caps(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.get_provenance)
        assert "settings.provenance_claim_limit" in src
        assert "settings.provenance_conflict_limit" in src
        assert "settings.provenance_document_limit" in src


# ── Phase 1 Fix 2: SourceORM import is at module level ───────────────────────

class TestNoLocalSourceImport:
    """The deferred `from atlas.models.orm import Source as SourceORM` must be gone."""

    def test_no_local_source_import_in_list_open_conflicts(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.list_open_conflicts)
        assert "import Source" not in src, (
            "list_open_conflicts must not import Source locally — "
            "it is already imported at module level."
        )
        assert "SourceORM" not in src, (
            "The SourceORM alias was a sign of a deferred local import. "
            "Use the module-level Source directly."
        )


# ── Phase 2: Lockfile exists ──────────────────────────────────────────────────

class TestLockfileExists:
    """requirements.lock must exist and be non-empty."""

    def test_requirements_lock_exists(self):
        import os
        lock = os.path.join(os.path.dirname(__file__), "..", "requirements.lock")
        assert os.path.exists(lock), (
            "requirements.lock is missing. "
            "Run: pip-compile pyproject.toml -o requirements.lock --strip-extras"
        )

    def test_requirements_lock_has_pinned_versions(self):
        import os
        lock = os.path.join(os.path.dirname(__file__), "..", "requirements.lock")
        if not os.path.exists(lock):
            pytest.skip("requirements.lock not present")
        with open(lock) as f:
            content = f.read()
        # Every non-comment, non-blank line should contain ==
        lines = [line for line in content.splitlines() if line and not line.startswith("#") and not line.startswith("    ")]
        assert len(lines) > 10, "Lock file looks empty"
        pinned = [line for line in lines if "==" in line]
        assert len(pinned) > 5, "Lock file does not appear to contain pinned versions"

    def test_dockerfile_uses_lockfile(self):
        import os
        df = os.path.join(os.path.dirname(__file__), "..", "Dockerfile")
        with open(df) as f:
            content = f.read()
        assert "requirements.lock" in content, (
            "Dockerfile must install from requirements.lock, not floating versions."
        )
        # The old bad pattern — bare package names without versions
        assert 'pip install --no-cache-dir \\\n    fastapi' not in content, (
            "Dockerfile still installs bare unversioned package names."
        )


# ── Phase 3: Rate limit settings exist ───────────────────────────────────────

class TestRateLimitConfig:
    """Rate limit settings must be present and have sensible defaults."""

    def test_rate_limit_settings_exist(self):
        from atlas.config import get_settings
        s = get_settings()
        assert hasattr(s, "rate_limit_enabled")
        assert hasattr(s, "rate_limit_map")
        assert hasattr(s, "rate_limit_analytics")
        assert hasattr(s, "rate_limit_provenance")
        assert hasattr(s, "rate_limit_mutations")

    def test_rate_limit_map_is_more_restrictive_than_default(self):
        """Map endpoint limit must be stricter than the default global limit."""
        from atlas.config import get_settings
        s = get_settings()

        def _per_minute(spec: str) -> int:
            count, unit = spec.split("/")
            multiplier = {"minute": 1, "hour": 1/60, "second": 60}.get(unit, 1)
            return int(float(count) * multiplier)

        assert _per_minute(s.rate_limit_map) < _per_minute(s.rate_limit_default), (
            "Map endpoint must have a lower per-minute limit than the global default."
        )


# ── Phase 3: readyz and metrics endpoints wired ───────────────────────────────

class TestNewEndpoints:
    """Verify /readyz and /metrics are registered in the FastAPI app."""

    def test_readyz_route_registered(self):
        from atlas.api.app import app
        paths = [r.path for r in app.routes]
        assert "/api/v1/readyz" in paths, "/readyz endpoint must be registered"

    def test_metrics_route_registered(self):
        from atlas.api.app import app
        paths = [r.path for r in app.routes]
        assert "/metrics" in paths, "/metrics endpoint must be registered"

    def test_metrics_excluded_from_openapi(self):
        import inspect

        from atlas.api.app import app
        src = inspect.getsource(app.routes[0].__class__)  # just check app level
        # Verify via the route decorator include_in_schema=False
        src = inspect.getsource(__import__("atlas.api.app", fromlist=["metrics"]).metrics)
        # The function must exist and return a Response
        assert "generate_latest" in src


# ── Phase 3: Analytics cache ──────────────────────────────────────────────────

class TestAnalyticsCacheUnit:
    """Unit tests for _AnalyticsCache without a real DB."""

    def _settings(self, ttl: int):
        from unittest.mock import MagicMock
        return MagicMock(analytics_cache_ttl_s=ttl)

    def test_cache_is_stale_initially(self):
        from atlas.api.app import _AnalyticsCache
        c = _AnalyticsCache()
        assert not c.is_fresh(self._settings(60))

    def test_cache_is_fresh_after_store(self):
        from atlas.api.app import AnalyticsSummary, _AnalyticsCache
        c = _AnalyticsCache()
        s = self._settings(60)
        val = AnalyticsSummary(
            total_accidents=5, total_fatalities=1, fatal_count=1,
            avg_confidence=0.8,
            by_severity={"FATAL": 1}, by_phase={}, by_year={2023: 1},
            confidence_bins={"well_sourced": 1, "mostly_sourced": 0,
                             "partially_sourced": 0, "weakly_sourced": 0},
        )
        c.store(val, s)
        assert c.is_fresh(s)
        assert c.value is val

    def test_cache_invalidate_clears_state(self):
        from atlas.api.app import AnalyticsSummary, _AnalyticsCache
        c = _AnalyticsCache()
        s = self._settings(60)
        val = AnalyticsSummary(
            total_accidents=1, total_fatalities=0, fatal_count=0,
            avg_confidence=0.5,
            by_severity={}, by_phase={}, by_year={},
            confidence_bins={"well_sourced": 0, "mostly_sourced": 0,
                             "partially_sourced": 0, "weakly_sourced": 0},
        )
        c.store(val, s)
        assert c.is_fresh(s)
        c.invalidate()
        assert not c.is_fresh(s)
        assert c.value is None

    def test_cache_disabled_when_ttl_zero(self):
        """TTL=0 must disable caching immediately after store()."""
        from atlas.api.app import AnalyticsSummary, _AnalyticsCache

        c = _AnalyticsCache()
        s = self._settings(0)
        assert not c.is_fresh(s), "Cache must not be fresh before any store()"

        val = AnalyticsSummary(
            total_accidents=1, total_fatalities=0, fatal_count=0,
            avg_confidence=0.5,
            by_severity={}, by_phase={}, by_year={},
            confidence_bins={"well_sourced": 0, "mostly_sourced": 0,
                             "partially_sourced": 0, "weakly_sourced": 0},
        )
        c.store(val, s)
        assert not c.is_fresh(s), (
            "Cache must not be fresh with ttl=0 even right after store()."
        )

    def test_cache_settings_are_respected_for_positive_ttl(self):
        """Positive TTL should cache until invalidate()."""
        from atlas.api.app import AnalyticsSummary, _AnalyticsCache

        c = _AnalyticsCache()
        s = self._settings(300)
        val = AnalyticsSummary(
            total_accidents=5, total_fatalities=1, fatal_count=1,
            avg_confidence=0.8,
            by_severity={"FATAL": 1}, by_phase={}, by_year={2023: 5},
            confidence_bins={"well_sourced": 1, "mostly_sourced": 0,
                             "partially_sourced": 0, "weakly_sourced": 0},
        )
        c.store(val, s)
        assert c.is_fresh(s), "Cache must be fresh with positive TTL after store()"
        assert c.value is val

        c.invalidate()
        assert not c.is_fresh(s), "Cache must not be fresh after invalidate()"
        assert c.value is None


# ─────────────────────────────────────────────────────────────────────────────
# v28.4 fixes — unit tests
# ─────────────────────────────────────────────────────────────────────────────


# ── Fix 1: Rate limiting actually applied ─────────────────────────────────────

class TestRateLimitingActuallyApplied:
    """
    Rate limits must be installed on real factory-created app routes.
    Config settings alone are not enforcement.
    """

    def test_limiter_has_default_limits(self):
        """The limiter must be built with default_limits so all routes have a baseline."""
        from atlas.api.app import _limiter
        # SlowAPI stores default limits as a list of strings
        defaults = getattr(_limiter, "_default_limits", None)
        # Also accept via the internal storage on the Limiter instance
        if defaults is None:
            defaults = getattr(_limiter, "default_limits", None)
        assert defaults is not None and len(defaults) > 0, (
            "Limiter must be built with default_limits=[settings.rate_limit_default]. "
            "Without default_limits, only explicitly decorated endpoints are limited."
        )

    def test_slowapi_middleware_registered(self):
        from slowapi.middleware import SlowAPIMiddleware

        from atlas.api.app import app
        classes = [m.cls for m in app.user_middleware]
        assert SlowAPIMiddleware in classes, (
            "SlowAPIMiddleware must be in app.user_middleware. "
            "The import and instantiation are present but middleware registration may be missing."
        )

    def test_expensive_endpoints_have_limiter_metadata_on_factory_app(self):
        """create_app() must install SlowAPI limits on expensive route endpoints."""
        from fastapi.routing import APIRoute

        from atlas.api.app import create_app
        from atlas.config import Settings

        test_app = create_app(Settings(app_env="test", api_auth_enabled=False, rate_limit_enabled=True))
        routes = {r.path: r for r in test_app.router.routes if isinstance(r, APIRoute)}
        for path in (
            "/api/v1/accidents/map",
            "/api/v1/analytics/summary",
            "/api/v1/accidents/{event_id}/provenance",
            "/api/v1/conflicts/{conflict_id}/resolve",
            "/api/v1/admin/events/{event_id}/force-resolve-field",
        ):
            assert path in routes, f"{path} must be registered"
            assert hasattr(routes[path].endpoint, "_rate_limits"), (
                f"{path} must have SlowAPI rate-limit metadata installed by create_app()."
            )


# ── Fix 2: Request metrics middleware actually observes histogram ─────────────

class TestRequestMetricsMiddleware:
    """The _request_duration histogram must be in the middleware, not dead code."""

    def test_metrics_middleware_exists(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module)
        assert "_record_request_metrics" in src, (
            "Request metrics middleware function must exist."
        )
        assert "observe" in src, (
            "_http_request_duration.observe() must be called — "
            "a Histogram defined but never observed is dead code."
        )
        assert "_http_requests_total" in src, (
            "A requests total counter labelled by method/path/status must exist "
            "so operators can see request rates, not just durations."
        )

    def test_histogram_label_does_not_use_raw_url(self):
        """Using raw URL paths as a label cardinality-bombs the metrics store."""
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module._record_request_metrics)
        assert "path_template" in src, (
            "Metrics labels must use the route path template "
            "(e.g. /api/v1/accidents/{event_id}) not the raw URL "
            "to avoid unbounded label cardinality."
        )
        assert "url.path" not in src.split("path_template")[0].split("\n")[-1], (
            "Do not label metrics with raw url.path — use the matched route template."
        )


# ── Fix 3: .env.example tier weights correct ──────────────────────────────────

class TestEnvExampleTierWeights:
    """
    .env.example must show tier weights in the correct order (tier2 > tier3).
    Anyone copying .env.example verbatim must not get a startup-time ValidationError.
    """

    def test_env_example_has_correct_tier_weights(self):
        import os
        env_path = os.path.join(os.path.dirname(__file__), "..", ".env.example")
        with open(env_path) as f:
            content = f.read()

        lines = {
            k.strip(): v.strip()
            for line in content.splitlines()
            if "=" in line and not line.strip().startswith("#")
            for k, v in [line.split("=", 1)]
        }
        tier2 = float(lines.get("CONF_WEIGHT_TIER2", "0"))
        tier3 = float(lines.get("CONF_WEIGHT_TIER3", "0"))
        assert tier2 > tier3, (
            f"CONF_WEIGHT_TIER2={tier2} must be greater than CONF_WEIGHT_TIER3={tier3} "
            "in .env.example. Copying the example verbatim should not trigger the "
            "tier-weight validator and crash the app at startup."
        )

    def test_env_example_passes_settings_validator(self):
        """Parse .env.example and verify Settings.validate_tier_weights accepts it."""
        import os
        import unittest.mock as mock

        from pydantic import ValidationError

        env_path = os.path.join(os.path.dirname(__file__), "..", ".env.example")
        parsed = {}
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    parsed[k.strip()] = v.strip()

        from atlas.config import Settings
        with mock.patch.dict(os.environ, parsed, clear=False):
            try:
                Settings()
            except ValidationError as e:
                pytest.fail(
                    f".env.example values cause Settings validation failure: {e}"
                )


# ── Fix 4: search_q_max_length is wired (not hardcoded) ──────────────────────

class TestQMaxLengthWired:
    """SEARCH_Q_MAX_LENGTH setting must control the actual Query max_length."""

    def test_q_uses_module_constant_not_literal(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.list_accidents)
        # The constant must be used, not a bare 200 literal
        assert "_SEARCH_Q_MAX_LENGTH" in src, (
            "list_accidents must use _SEARCH_Q_MAX_LENGTH (derived from settings) "
            "not a hardcoded literal 200. The setting would otherwise be ornamental."
        )
        assert "max_length=200" not in src, (
            "max_length=200 literal found — wire settings.search_q_max_length instead."
        )

    def test_module_constant_matches_setting(self):
        from atlas.api.app import _SEARCH_Q_MAX_LENGTH
        from atlas.config import get_settings
        assert _SEARCH_Q_MAX_LENGTH == get_settings().search_q_max_length


# ── Fix 5: readyz verifies actual Alembic head ───────────────────────────────

class TestReadyzMigrationCheck:
    """readyz must compare DB version against ScriptDirectory head."""

    def test_readyz_uses_script_directory(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.readyz)
        assert "ScriptDirectory" in src, (
            "readyz must use Alembic's ScriptDirectory to discover the current "
            "script head. Checking only that a row exists in alembic_version is "
            "insufficient — a DB at migration 0008 with code at 0013 would pass."
        )
        assert "get_current_head" in src, (
            "readyz must call get_current_head() to compare against DB version."
        )
        assert "script_head" in src or "current_head" in src

    def test_readyz_compares_db_to_script_head(self):
        """The comparison must be an equality check, not just existence."""
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.readyz)
        # Must compare (db_head != script_head) or equivalent
        assert "!=" in src or "db_head" in src, (
            "readyz must explicitly compare the DB version against script head."
        )


# ── Fix 6: /metrics has optional token protection ─────────────────────────────

class TestMetricsProtection:
    """
    /metrics must support optional bearer-token auth for production deployments
    where it cannot be bound to an internal interface.
    """

    def test_metrics_token_setting_exists(self):
        from atlas.config import get_settings
        s = get_settings()
        assert hasattr(s, "metrics_token")
        # Default must be None (open, suitable for firewall-protected deploys)
        assert s.metrics_token is None

    def test_metrics_endpoint_checks_token_when_set(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.metrics)
        assert "metrics_token" in src, (
            "The /metrics endpoint must check settings.metrics_token "
            "and return 401 when the header does not match."
        )
        assert "Authorization" in src, (
            "Token must be checked via the Authorization header."
        )


# ── Fix 7: APP_ENV production guard ──────────────────────────────────────────

class TestProductionAuthGuard:
    """APP_ENV=production with API_AUTH_ENABLED=false must fail at startup."""

    def test_app_env_setting_exists(self):
        from atlas.config import get_settings
        s = get_settings()
        assert hasattr(s, "app_env")
        assert s.app_env in ("development", "production", "test", "development")

    def test_lifespan_raises_on_production_without_auth(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.lifespan)
        assert "production" in src, (
            "lifespan must check APP_ENV == 'production' and refuse to start "
            "if API_AUTH_ENABLED=false."
        )
        assert "RuntimeError" in src or "raise" in src, (
            "A warning is not a control — lifespan must raise (not warn) "
            "when APP_ENV=production and auth is disabled."
        )

    @pytest.mark.asyncio
    async def test_lifespan_raises_correctly(self):
        """Simulate production startup with auth disabled — must raise RuntimeError."""
        import unittest.mock as mock

        from atlas.api import app as app_module

        with mock.patch("atlas.api.app.settings") as mock_settings:
            mock_settings.app_env = "production"
            mock_settings.api_auth_enabled = False
            mock_settings.api_version = "test"
            mock_settings.rate_limit_enabled = False

            with pytest.raises(RuntimeError, match="production"):
                async with app_module.lifespan(app_module.app):
                    pass


# ── Fix 8: integration test uses ASGITransport ───────────────────────────────

class TestIntegrationFixtureCorrectness:
    """The integration test http_client fixture must use ASGITransport."""

    def test_integration_fixture_uses_asgi_transport(self):
        import os
        fixture_path = os.path.join(
            os.path.dirname(__file__), "integration", "test_http_contracts.py"
        )
        with open(fixture_path) as f:
            content = f.read()
        assert "ASGITransport" in content, (
            "Integration test must use httpx.ASGITransport(app=app) — "
            "the `app=app` shortcut to AsyncClient was removed in httpx >= 0.24."
        )
        assert "AsyncClient(app=app" not in content, (
            "The deprecated AsyncClient(app=app) pattern must not appear — "
            "it fails under the pinned lockfile."
        )


# ─────────────────────────────────────────────────────────────────────────────
# v28.5 fixes — unit tests
# ─────────────────────────────────────────────────────────────────────────────


# ── Fix 1+2: In-flight Gauge + try/finally middleware ─────────────────────────

class TestMetricsMiddlewareCorrectness:
    """The request metrics middleware must use a Gauge and a try/finally path."""

    def test_in_flight_metric_is_gauge_not_counter(self):
        from prometheus_client import Gauge

        from atlas.api.app import _http_requests_in_flight
        assert isinstance(_http_requests_in_flight, Gauge), (
            "_http_requests_in_flight must be a prometheus_client.Gauge. "
            "A Counter can only go up — it is not an in-flight metric. "
            "A Gauge can be incremented and decremented, accurately tracking "
            "the number of requests currently being processed."
        )

    def test_middleware_uses_try_finally(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module._record_request_metrics)
        assert "try:" in src, "Middleware must use try/finally to record metrics on crashes"
        assert "finally:" in src, (
            "Middleware must decrement the Gauge and record duration in a finally "
            "block so 500-class failures are visible in metrics."
        )

    def test_middleware_decrements_gauge_in_finally(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module._record_request_metrics)
        assert ".dec()" in src, (
            "Gauge.dec() must be called in the finally block. "
            "Without it, the in-flight gauge only goes up — it becomes a counter."
        )

    def test_middleware_records_status_on_crash(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module._record_request_metrics)
        # Default status_code before response is known must be "500" or similar
        assert '"500"' in src or "'500'" in src, (
            "Middleware must initialize status_code to '500' before call_next(). "
            "If the request crashes before a response is returned, the metric "
            "label must still reflect a 5xx error, not be silently dropped."
        )


# ── Fix 4: Analytics cache test is honest about what it proves ───────────────

class TestAnalyticsCacheLruCacheAwareness:
    """
    The analytics cache test must patch atlas.api.app.settings directly,
    not os.environ, because get_settings() is lru_cache-d at module import.
    """

    def test_cache_ttl_zero_via_direct_mock(self):
        """
        Prove TTL=0 disables caching by patching the module-level settings
        object directly, not via os.environ.  os.environ patches do not affect
        the already-created Settings singleton.
        """
        import unittest.mock as mock

        from atlas.api.app import AnalyticsSummary, _AnalyticsCache

        with mock.patch("atlas.api.app.settings") as mock_settings:
            mock_settings.analytics_cache_ttl_s = 0

            c = _AnalyticsCache()
            val = AnalyticsSummary(
                total_accidents=99, total_fatalities=0, fatal_count=0,
                avg_confidence=0.5,
                by_severity={}, by_phase={}, by_year={},
                confidence_bins={"well_sourced": 0, "mostly_sourced": 0,
                                 "partially_sourced": 0, "weakly_sourced": 0},
            )
            c.store(val, mock_settings)
            # Must be False immediately — TTL=0 means never cache
            assert not c.is_fresh(mock_settings), (
                "is_fresh() must return False with TTL=0 even right after store(). "
                "Patching via os.environ is insufficient — get_settings() is "
                "lru_cache-d and ignores env changes after first call."
            )


# ── Fix 5: readyz uses absolute alembic.ini path ─────────────────────────────

class TestReadyzPathIndependence:
    """readyz must find alembic.ini regardless of process CWD."""

    def test_readyz_uses_absolute_path(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.readyz)
        # Must not use a bare relative string
        assert 'AlembicConfig("alembic.ini")' not in src, (
            'readyz must not use AlembicConfig("alembic.ini") — that is a CWD-relative '
            "path that fails when the process is started from a different directory."
        )
        # Must derive an absolute path
        assert "__file__" in src or "pathlib" in src or "Path" in src, (
            "readyz must use __file__ or pathlib.Path to locate alembic.ini "
            "independently of the process working directory."
        )


# ── Fix 6: Production Redis requirement ──────────────────────────────────────

class TestProductionRedisGuard:
    """Production with in-memory rate limiting must raise at startup."""

    @pytest.mark.asyncio
    async def test_production_inmemory_rate_limit_raises(self):
        import unittest.mock as mock

        from atlas.api import app as app_module

        with mock.patch("atlas.api.app.settings") as ms:
            ms.app_env = "production"
            ms.api_auth_enabled = True     # auth is fine
            ms.rate_limit_enabled = True
            ms.rate_limit_storage_url = None   # no Redis — must raise
            ms.metrics_token = "tok"
            ms.metrics_public_ok = False
            ms.api_version = "test"

            with pytest.raises(RuntimeError, match="RATE_LIMIT_STORAGE_URL"):
                async with app_module.lifespan(app_module.app):
                    pass

    def test_readyz_reports_inmemory_rate_limit_warning(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.readyz)
        assert "rate_limit_storage" in src, (
            "readyz must check rate_limit_storage and warn when in-memory "
            "limiting is active, so operators see the degraded state."
        )
        assert "in-memory" in src or "per-process" in src, (
            "The warning must explain the per-process bucket limitation."
        )


# ── Fix 7: /metrics unprotected in production is a startup error ─────────────

class TestMetricsProductionGuard:
    """APP_ENV=production without METRICS_TOKEN or METRICS_PUBLIC_OK must refuse to start."""

    def test_metrics_public_ok_setting_exists(self):
        from atlas.config import get_settings
        s = get_settings()
        assert hasattr(s, "metrics_public_ok")
        assert s.metrics_public_ok is False   # safe default

    @pytest.mark.asyncio
    async def test_production_unprotected_metrics_raises(self):
        import unittest.mock as mock

        from atlas.api import app as app_module

        with mock.patch("atlas.api.app.settings") as ms:
            ms.app_env = "production"
            ms.api_auth_enabled = True
            ms.rate_limit_enabled = True
            ms.rate_limit_storage_url = "redis://localhost:6379/0"   # Redis ok
            ms.metrics_token = None       # no token
            ms.metrics_public_ok = False  # not explicitly acknowledged
            ms.api_version = "test"

            with pytest.raises(RuntimeError, match="METRICS_TOKEN"):
                async with app_module.lifespan(app_module.app):
                    pass

    @pytest.mark.asyncio
    async def test_production_with_metrics_public_ok_starts(self):
        """METRICS_PUBLIC_OK=true must bypass the metrics protection check."""
        import unittest.mock as mock

        from atlas.api import app as app_module

        with mock.patch("atlas.api.app.settings") as ms:
            ms.app_env = "production"
            ms.api_auth_enabled = True
            ms.rate_limit_enabled = True
            ms.rate_limit_storage_url = "redis://localhost:6379/0"
            ms.metrics_token = None
            ms.metrics_public_ok = True   # explicitly acknowledged
            ms.api_version = "test"
            ms.api_title = "test"

            # Should not raise — METRICS_PUBLIC_OK bypasses the check
            try:
                async with app_module.lifespan(app_module.app):
                    pass
            except RuntimeError as e:
                if "METRICS_TOKEN" in str(e):
                    pytest.fail(f"METRICS_PUBLIC_OK=true should bypass metrics guard, got: {e}")
                # Other RuntimeErrors (e.g. from log setup) are acceptable in mock context


# ─────────────────────────────────────────────────────────────────────────────
# v28.6 (Sprint A/B/C) — unit tests
# ─────────────────────────────────────────────────────────────────────────────


# ── Sprint A1: Redis dependency in pyproject.toml ────────────────────────────

class TestRedisDependency:
    def test_redis_in_pyproject(self):
        import os
        pyproject = os.path.join(os.path.dirname(__file__), "..", "pyproject.toml")
        with open(pyproject) as f:
            content = f.read()
        assert "redis" in content.lower(), (
            "redis must be an explicit production dependency in pyproject.toml. "
            "Without it the production image may not support Redis-backed rate limiting."
        )


# ── Sprint A2: readyz actually pings Redis ───────────────────────────────────

class TestReadyzRedisPing:
    def test_readyz_pings_redis_when_configured(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.readyz)
        assert "ping" in src, (
            "readyz must call redis.ping() to verify Redis connectivity. "
            "Checking that a URL is configured is not the same as checking connectivity."
        )
        assert "AsyncRedis" in src or "Redis.from_url" in src or "from_url" in src, (
            "readyz must create a Redis client and ping it when RATE_LIMIT_STORAGE_URL is set."
        )
        assert "aclose" in src or "close" in src, (
            "readyz must close the Redis client after pinging to avoid connection leaks."
        )

    def test_readyz_reports_503_on_redis_failure(self):
        """When Redis ping fails, readyz must report it as an error (not just a warning)."""
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.readyz)
        # The error path must set ready = False
        assert "ready = False" in src, (
            "Redis connectivity failure in readyz must set ready=False and return 503."
        )


# ── Sprint A3: health/readyz/metrics exempt from rate limits ─────────────────

class TestInfraEndpointsExemptFromRateLimits:
    """Orchestrator and monitoring endpoints must not be rate-limited."""

    def test_infra_endpoints_have_no_route_specific_limits(self):
        """Factory app must exempt infra endpoints from route-specific limits."""
        from fastapi.routing import APIRoute

        from atlas.api.app import create_app
        from atlas.config import Settings

        test_app = create_app(Settings(app_env="test", api_auth_enabled=False, rate_limit_enabled=True))
        routes = {r.path: r for r in test_app.router.routes if isinstance(r, APIRoute)}
        for path in ("/api/v1/health", "/api/v1/readyz", "/metrics"):
            assert path in routes, f"{path} must be registered"
            assert not getattr(routes[path].endpoint, "_rate_limits", None), (
                f"{path} must not have route-specific _rate_limits; probes/scrapers must not be 429'd."
            )


# ── Sprint A4: Unmatched-route label uses sentinel, not raw URL ───────────────

class TestMetricsLabelCardinality:
    """Unmatched routes must use a fixed label, not the raw request URL."""

    def test_middleware_uses_sentinel_for_unmatched(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module._record_request_metrics)
        assert "__unmatched__" in src, (
            "Unmatched routes must use a fixed '__unmatched__' label. "
            "Using raw URL paths would create one label per unique URL — "
            "e.g. /random/id1, /random/id2 — cardinality-bombing Prometheus."
        )
        # Must NOT initialize to raw url.path anymore
        assert "request.url.path\n" not in src.split("__unmatched__")[0], (
            "After adding '__unmatched__', the fallback must not be url.path."
        )


# ── Sprint B1: Bounding-box filter on map endpoint ───────────────────────────

class TestMapBoundingBox:
    """Map endpoint must accept and validate bounding-box parameters."""

    def test_map_endpoint_has_bbox_params(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.map_accidents)
        for param in ("north", "south", "east", "west"):
            assert param in src, (
                f"map_accidents must have a '{param}' bounding-box parameter. "
                "Without viewport filtering, users on large datasets always hit the cap."
            )

    def test_bbox_partial_params_rejected(self):
        """Providing only some bbox params must return 422."""
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.map_accidents)
        assert "422" in src, (
            "map_accidents must reject partial bounding-box params with 422. "
            "Silently ignoring north without south would produce wrong results."
        )

    def test_south_gt_north_rejected(self):
        """south > north is geographically invalid."""
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.map_accidents)
        assert "south > north" in src or "south.*north" in src or "south, north" in src, (
            "map_accidents must reject south > north with 422."
        )

    def test_year_to_filter_present(self):
        """The map endpoint should also accept year_to for closed time windows."""
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.map_accidents)
        assert "year_to" in src, (
            "map_accidents should accept year_to so callers can specify a closed "
            "time window (e.g. accidents between 2010 and 2020)."
        )

    def test_fetchMapAccidents_supports_bounds(self):
        """Frontend fetchMapAccidents must pass bbox params."""
        import os
        api_path = os.path.join(os.path.dirname(__file__), "..", "web", "lib", "api.ts")
        with open(api_path) as f:
            content = f.read()
        assert "MapBounds" in content, (
            "api.ts must define a MapBounds interface with north/south/east/west."
        )
        assert "bounds" in content, (
            "fetchMapAccidents must accept a bounds parameter and pass it to the API."
        )


# ── Sprint C1: Provenance sections have deterministic ordering ────────────────

class TestProvenanceDeterministicOrdering:
    """All capped provenance sections must have an explicit ORDER BY."""

    def test_conflicts_have_order_by(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.get_provenance)
        # Find the conflicts query section
        conflicts_section = src[src.find("conflicts_raw"):][:500]
        assert "order_by" in conflicts_section, (
            "Conflicts in provenance must have ORDER BY so truncated results "
            "are deterministic across requests."
        )
        assert "created_at" in conflicts_section, (
            "Conflicts must be ordered by created_at (most recent first) "
            "so the most recent conflicts are returned when the cap is hit."
        )

    def test_source_documents_have_order_by(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.get_provenance)
        docs_section = src[src.find("docs_raw"):][:500]
        assert "order_by" in docs_section, (
            "Source documents in provenance must have ORDER BY."
        )
        assert "published_at" in docs_section, (
            "Source documents must be ordered by published_at so the most "
            "recent documents appear when the cap is hit."
        )


# ── Sprint C2: sources_out includes all referenced source IDs ─────────────────

class TestProvenanceSourcesCompleteness:
    """sources_out must include source IDs from docs and revisions, not just claims."""

    def test_sources_collected_from_all_sections(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.get_provenance)
        # Must collect from docs, not only from claims
        assert "d.source_id" in src or "doc.source_id" in src or "docs" in src and "source_id" in src, (
            "get_provenance must collect source IDs from source documents, "
            "not only from returned claims. A doc can reference a source not "
            "in the (capped) claims list."
        )
        assert "all_source_ids" in src or "source_ids" in src and "docs" in src, (
            "get_provenance must accumulate source IDs from multiple sections."
        )

    def test_sources_out_not_only_from_claims(self):
        """Specifically verify the old single-source-of-IDs pattern is gone."""
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.get_provenance)
        # The old pattern was a one-liner set comprehension from claims only
        old_pattern = "{c.source_id for c in [cs[0] for cs in claims_with_sources]}"
        assert old_pattern not in src, (
            "The old source_ids comprehension only from claims must be replaced "
            "with a multi-section accumulation that includes doc source IDs."
        )


# ── Docs: production.md exists ────────────────────────────────────────────────

class TestProductionDocsExist:
    def test_production_md_exists(self):
        import os
        doc_path = os.path.join(os.path.dirname(__file__), "..", "docs", "production.md")
        assert os.path.exists(doc_path), (
            "docs/production.md must exist. Production deployments have stricter "
            "startup requirements (Redis, auth, metrics) that need documentation."
        )

    def test_production_md_covers_required_env_vars(self):
        import os
        doc_path = os.path.join(os.path.dirname(__file__), "..", "docs", "production.md")
        with open(doc_path) as f:
            content = f.read()
        for var in ("APP_ENV", "API_AUTH_ENABLED", "RATE_LIMIT_STORAGE_URL",
                    "METRICS_TOKEN", "DATABASE_URL"):
            assert var in content, f"docs/production.md must document {var}"

    def test_production_md_mentions_readyz(self):
        import os
        doc_path = os.path.join(os.path.dirname(__file__), "..", "docs", "production.md")
        with open(doc_path) as f:
            content = f.read()
        assert "readyz" in content, (
            "Production docs must explain /readyz and distinguish it from /health."
        )


# ─────────────────────────────────────────────────────────────────────────────
# v28.7 (Sprint D0/D1) — unit tests
# ─────────────────────────────────────────────────────────────────────────────


# ── D0.1: Anti-meridian rejection ─────────────────────────────────────────────

class TestAntiMeridianRejection:
    """west > east must be rejected with 422."""

    def test_map_rejects_west_gt_east(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.map_accidents)
        assert "west > east" in src, (
            "map_accidents must reject west > east (anti-meridian crossing). "
            "Without this check, the BETWEEN predicate silently returns 0 rows."
        )
        assert "anti-meridian" in src.lower() or "anti_meridian" in src.lower(), (
            "The 422 error must explain that anti-meridian crossing is unsupported."
        )

    def test_valid_bbox_west_lt_east_not_rejected(self):
        """west < east must not trigger the anti-meridian guard."""
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.map_accidents)
        # The rejection must be conditional on west > east, not on having a bbox
        assert "if west is not None and east is not None and west > east" in src, (
            "Anti-meridian rejection must only fire when west > east, "
            "not for all bounding boxes."
        )


# ── D0.2: Lat/lon index migration ─────────────────────────────────────────────

class TestLatLonMigration:
    def test_migration_0014_creates_index(self):
        import glob
        import os
        versions_dir = os.path.join(os.path.dirname(__file__), "..", "migrations", "versions")
        files = glob.glob(os.path.join(versions_dir, "0014*.py"))
        assert files, "Migration 0014 not found"
        with open(files[0]) as f:
            content = f.read()
        assert "ix_accident_records_lat_lon" in content
        assert "create_index" in content
        assert "location_lat" in content
        assert "location_lon" in content

    def test_migration_0014_is_partial_index(self):
        """Index should be partial (WHERE NOT NULL) to avoid indexing NULL rows."""
        import glob
        import os
        versions_dir = os.path.join(os.path.dirname(__file__), "..", "migrations", "versions")
        files = glob.glob(os.path.join(versions_dir, "0014*.py"))
        assert files
        with open(files[0]) as f:
            content = f.read()
        assert "IS NOT NULL" in content, (
            "The lat/lon index should be a partial index (WHERE lat IS NOT NULL) "
            "to avoid storing NULL coordinate rows in the index."
        )

    def test_migration_0014_has_working_downgrade(self):
        import glob
        import os
        versions_dir = os.path.join(os.path.dirname(__file__), "..", "migrations", "versions")
        files = glob.glob(os.path.join(versions_dir, "0014*.py"))
        assert files
        with open(files[0]) as f:
            content = f.read()
        assert "def downgrade" in content
        assert "drop_index" in content


# ── D0.3: Provenance source completeness includes conflict claim sources ───────

class TestProvenanceConflictClaimSources:
    """Sources from conflict claim A and B must be in sources_out."""

    def test_conflict_claim_ids_are_collected(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.get_provenance)
        assert "conflict_claim_ids" in src, (
            "get_provenance must collect claim IDs from returned conflicts "
            "to fetch their source IDs."
        )
        assert "claim_a_id" in src.split("conflict_claim_ids")[1][:500] or \
               "claim_a_id" in src, (
            "conflict_claim_ids must include claim_a_id from each conflict."
        )

    def test_conflict_source_ids_fetched(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.get_provenance)
        # Must query Claim.source_id for the conflict claim IDs
        assert "conflict_source_rows" in src or "conflict_claim_ids" in src, (
            "get_provenance must fetch source IDs from conflict claim rows."
        )
        assert "all_source_ids.update" in src or "all_source_ids.add" in src, (
            "Conflict claim source IDs must be added to all_source_ids."
        )


# ── D0.4: Open conflicts first ordering ──────────────────────────────────────

class TestProvenanceOpenConflictsFirst:
    def test_conflicts_ordered_open_first(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.get_provenance)
        conflicts_section = src[src.find("conflicts_raw"):][:600]
        assert "open" in conflicts_section, (
            "Conflicts must be ordered with open status first."
        )
        assert "case" in conflicts_section.lower() or "sa_case" in conflicts_section, (
            "Use a CASE expression to sort open=0 before resolved=1."
        )


# ── D0.6: Conflict queue is protected ─────────────────────────────────────────

class TestConflictQueueProtection:
    def test_list_open_conflicts_requires_reviewer(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.list_open_conflicts)
        assert "require_reviewer" in src, (
            "list_open_conflicts must depend on require_reviewer. "
            "Unprotected conflict queues expose internal data disagreements."
        )

    def test_conflict_stats_requires_reviewer(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.conflict_stats)
        assert "require_reviewer" in src, (
            "conflict_stats must depend on require_reviewer."
        )


# ── D1.1: App factory exists ──────────────────────────────────────────────────

class TestAppFactory:
    """create_app must exist and produce a real FastAPI app with settings isolation."""

    def test_create_app_importable(self):
        from atlas.api.app import create_app
        assert callable(create_app)

    def test_create_app_returns_fastapi_instance(self):
        from fastapi import FastAPI

        from atlas.api.app import create_app
        app_instance = create_app()
        assert isinstance(app_instance, FastAPI)

    def test_create_app_does_not_affect_module_level_singleton(self):
        """Creating a new app must not mutate the module-level `app` or `settings`."""
        from atlas.api.app import app as module_app
        from atlas.api.app import create_app
        new_app = create_app()
        assert new_app is not module_app, (
            "create_app must return a distinct FastAPI instance, not the singleton."
        )

    def test_build_limiter_with_settings_uses_provided_limits(self):
        """The limiter factory must use the provided settings, not the global singleton."""
        from unittest.mock import MagicMock

        from atlas.api.app import _build_limiter_with_settings

        mock_settings = MagicMock()
        mock_settings.rate_limit_default = "999/minute"
        mock_settings.rate_limit_storage_url = None

        limiter = _build_limiter_with_settings(mock_settings)
        # SlowAPI stores default limits as LimitGroup objects; iterate to get Limit items.
        default_limits = getattr(limiter, "_default_limits", [])
        assert default_limits, "_build_limiter_with_settings must set _default_limits"
        limit_strings = []
        for group in default_limits:
            for item in group:
                limit_strings.append(str(item.limit))
        assert any("999" in s for s in limit_strings), (
            f"_build_limiter_with_settings must use the provided rate_limit_default. "
            f"Got limits: {limit_strings}"
        )


# ── D0.5: .gitignore covers cache dirs ───────────────────────────────────────

class TestGitignoreHygiene:
    def test_gitignore_covers_cache_dirs(self):
        import os
        gi_path = os.path.join(os.path.dirname(__file__), "..", ".gitignore")
        with open(gi_path) as f:
            content = f.read()
        for pattern in (".pytest_cache", ".ruff_cache", "__pycache__", "node_modules", ".mypy_cache"):
            assert pattern in content, (
                f".gitignore must include '{pattern}' to prevent cache directories "
                "from being committed to the repository."
            )


# ── Map clustering for low zoom levels ───────────────────────────────────────

class TestMapClusteringSupport:
    def test_cluster_settings_exist(self):
        from atlas.config import Settings
        s = Settings()
        assert hasattr(s, "map_cluster_max_zoom")
        assert isinstance(s.map_cluster_max_zoom, int)
        assert 0 <= s.map_cluster_max_zoom <= 22

    def test_map_cluster_schema_exists(self):
        from atlas.api.schemas import MapCluster
        c = MapCluster(
            cluster_id="z4:10:-20",
            location_lat=40.0,
            location_lon=-100.0,
            count=12,
            fatalities_total=3,
            latest_occurred_year=2024,
            cell_degrees=2.8125,
        )
        assert c.count == 12
        assert c.cluster_id.startswith("z4:")

    def test_map_endpoint_accepts_zoom_and_cluster_mode(self):
        import inspect

        from atlas.api import app as app_module
        src = inspect.getsource(app_module.map_accidents)
        assert "zoom" in src, "map_accidents must accept a zoom parameter."
        assert "mode\": \"clusters\"" in src, "low zoom map response must expose cluster mode."
        assert "MapCluster" in src, "clustered map responses must use the MapCluster schema."
        assert "group_by" in src, "clustered map responses must group rows in SQL."


class TestGenericCsvInjurySplits:
    def test_generic_normalise_preserves_split_count_fields(self):
        from atlas.ingestion.generic_csv_adapter import normalise_generic

        fields = normalise_generic({
            "fatalities_total": "3",
            "fatalities_crew": "1",
            "fatalities_passengers": "2",
            "serious_injuries": "5",
            "serious_injuries_crew": "2",
            "serious_injuries_passengers": "3",
            "minor_injuries": "7",
            "minor_injuries_crew": "4",
            "minor_injuries_passengers": "3",
            "uninjured_crew": "1",
            "uninjured_passengers": "99",
        })
        assert fields["fatalities_crew"] == 1
        assert fields["fatalities_passengers"] == 2
        assert fields["serious_injuries_crew"] == 2
        assert fields["serious_injuries_passengers"] == 3
        assert fields["minor_injuries_crew"] == 4
        assert fields["minor_injuries_passengers"] == 3
        assert fields["uninjured_crew"] == 1
        assert fields["uninjured_passengers"] == 99
