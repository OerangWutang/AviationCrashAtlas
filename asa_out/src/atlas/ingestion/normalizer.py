"""
NTSB field normalizer — pure functions, no side effects, fully testable.

build_canonical_fields() takes a raw NTSB record dict and returns a dict
mapping canonical field names to Python-typed values.

All datetime/date objects are properly typed here. The writer then calls
claim_value.encode() before inserting into JSONB — so this module never
needs to think about JSON serialization.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

_SEVERITY_MAP = {
    "FATAL": "FATAL", "SERIOUS": "SERIOUS", "MINOR": "MINOR",
    "NONE": "NONE", "UNK": "UNKNOWN", "": "UNKNOWN",
}

_DAMAGE_MAP = {
    "DEST": "DESTROYED", "SUBS": "SUBSTANTIAL",
    "MINR": "MINOR", "NONE": "NONE", "UNK": "UNKNOWN", "": "UNKNOWN",
}

_INV_STATUS_MAP = {
    "PRELIMINARY": "preliminary", "FACTUAL": "factual",
    "PROBABLE CAUSE": "probable_cause", "FINAL": "final", "CLOSED": "closed",
}

_PHASE_MAP = {
    "TAKEOFF": "TAKEOFF", "TAKE-OFF": "TAKEOFF",
    "INITIAL CLIMB": "CLIMB", "CLIMB": "CLIMB",
    "CRUISE": "CRUISE", "DESCENT": "DESCENT", "EMERGENCY DESCENT": "DESCENT",
    "APPROACH": "APPROACH", "FINAL APPROACH": "APPROACH", "GO-AROUND": "APPROACH",
    "LANDING": "LANDING", "LANDING ROLL": "LANDING",
    "MANEUVERING": "MANEUVERING", "HOVERING": "MANEUVERING",
    "STANDING": "STANDING", "TAXI": "TAXI",
}


def normalize_severity(raw: str | None) -> str:
    if not raw:
        return "UNKNOWN"
    return _SEVERITY_MAP.get(raw.upper().strip(), "UNKNOWN")


def normalize_damage(raw: str | None) -> str:
    if not raw:
        return "UNKNOWN"
    return _DAMAGE_MAP.get(raw.upper().strip(), "UNKNOWN")


def normalize_investigation_status(raw: str | None) -> str:
    if not raw:
        return "preliminary"
    return _INV_STATUS_MAP.get(raw.upper().strip(), "preliminary")


def normalize_phase(raw: str | None) -> str | None:
    if not raw:
        return None
    return _PHASE_MAP.get(raw.upper().strip(), raw.upper().strip())


def normalize_date(
    raw_date: str | None,
    raw_time: str | None = None,
) -> tuple[datetime | None, str]:
    """
    Parse NTSB date + time into a datetime and precision label.

    IMPORTANT: NTSB source times are local (accident-site timezone) unless
    explicitly stated otherwise.  We do NOT attach UTC (timezone.utc) because
    we do not know the offset.  The datetime is returned as timezone-naive and
    callers should treat it as "local, timezone unknown" until a tz resolution
    step is added.  Storing a false UTC timestamp is worse than storing a
    correct naive local one.

    Returns Python datetime — caller must encode() before JSONB insert.
    """
    if not raw_date:
        return None, "year"
    try:
        dt = datetime.strptime(raw_date.strip(), "%Y-%m-%d")
    except ValueError:
        try:
            dt = datetime.strptime(raw_date.strip(), "%m/%d/%Y")
        except ValueError:
            return None, "year"

    if raw_time and re.match(r"^\d{4}$", raw_time.strip()):
        try:
            hour, minute = int(raw_time[:2]), int(raw_time[2:])
            # Return naive datetime: local time, timezone unknown
            dt = dt.replace(hour=hour, minute=minute)
            return dt, "exact"
        except ValueError:
            pass

    # Day-precision only — no time component, keep naive
    return dt, "day"


def normalize_coordinates(raw_lat: Any, raw_lon: Any) -> tuple[float | None, float | None]:
    try:
        lat, lon = float(raw_lat), float(raw_lon)
    except (TypeError, ValueError):
        return None, None
    if lat == 0.0 and lon == 0.0:
        return None, None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None, None
    return lat, lon


def normalize_int(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(float(str(raw)))
    except (ValueError, TypeError):
        return None




def _first_normalized_int(raw: dict[str, Any], *keys: str) -> int | None:
    """Return the first present integer value among several source-field aliases."""
    for key in keys:
        if key in raw:
            value = normalize_int(raw.get(key))
            if value is not None:
                return value
    return None

def normalize_operator(raw: str | None) -> str | None:
    if not raw:
        return None
    name = raw.strip().upper()
    for suffix in (" INC", " LLC", " LTD", " CO", " CORP", " DBA"):
        name = name.removesuffix(suffix)
    return name.strip() or None


def normalize_aircraft(
    raw_make: str | None, raw_model: str | None
) -> tuple[str | None, str | None]:
    make = raw_make.strip().title() if raw_make else None
    model = raw_model.strip().upper() if raw_model else None
    return make, model


# NTSB uses full country names, abbreviations, and some ISO codes.
# Map the most common values to ISO 3166-1 alpha-3. Unmapped values
# are kept as-is (uppercased, truncated to 3 chars) as a last resort,
# but the common cases are handled explicitly to avoid "UNI" for USA.
_COUNTRY_MAP: dict[str, str] = {
    # United States variants
    "UNITED STATES": "USA",
    "UNITED STATES OF AMERICA": "USA",
    "US": "USA",
    "USA": "USA",
    "U.S.": "USA",
    "U.S.A.": "USA",
    # Other frequent NTSB countries
    "CANADA": "CAN",
    "MEXICO": "MEX",
    "BAHAMAS": "BHS",
    "JAMAICA": "JAM",
    "BRAZIL": "BRA",
    "COLOMBIA": "COL",
    "VENEZUELA": "VEN",
    "PERU": "PER",
    "CHILE": "CHL",
    "ARGENTINA": "ARG",
    "UNITED KINGDOM": "GBR",
    "UK": "GBR",
    "GERMANY": "DEU",
    "FRANCE": "FRA",
    "ITALY": "ITA",
    "SPAIN": "ESP",
    "NETHERLANDS": "NLD",
    "AUSTRALIA": "AUS",
    "NEW ZEALAND": "NZL",
    "JAPAN": "JPN",
    "CHINA": "CHN",
    "INDIA": "IND",
    "RUSSIA": "RUS",
}


# NTSB uses full US state names. Map them to standard 2-letter postal codes
# so state_code always contains a real code, not a truncated full name.
# Non-US states are left as-is if they are already short (≤10 chars),
# or stored only in state_name_raw if longer.
_US_STATE_MAP: dict[str, str] = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
    "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE",
    "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID",
    "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS",
    "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD",
    "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN", "MISSISSIPPI": "MS",
    "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV",
    "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM",
    "NEW YORK": "NY", "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND",
    "OHIO": "OH", "OKLAHOMA": "OK", "OREGON": "OR", "PENNSYLVANIA": "PA",
    "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD",
    "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT", "VERMONT": "VT",
    "VIRGINIA": "VA", "WASHINGTON": "WA", "WEST VIRGINIA": "WV",
    "WISCONSIN": "WI", "WYOMING": "WY",
    # Territories and DC
    "DISTRICT OF COLUMBIA": "DC", "PUERTO RICO": "PR", "GUAM": "GU",
    "VIRGIN ISLANDS": "VI", "AMERICAN SAMOA": "AS",
    "NORTHERN MARIANA ISLANDS": "MP",
    # Common 2-letter codes passed through as-is
}


def normalize_state_code(raw: str) -> tuple[str | None, str]:
    """
    Return (state_code_or_None, raw_state_name).

    state_code is the standard 2-letter postal abbreviation when the input
    is a recognized US state or territory name, or already a 2-letter code.
    Returns None for the code when the input is not a recognized US state
    (e.g., a Canadian province abbreviation like "ON" is passed through only
    if it is already 2 letters; longer foreign region names return None).
    """
    raw_stripped = raw.strip()
    key = raw_stripped.upper()
    # Already a 2-letter code — pass through
    if len(key) == 2 and key.isalpha():
        return key, raw_stripped
    # Full name lookup
    if key in _US_STATE_MAP:
        return _US_STATE_MAP[key], raw_stripped
    # Not recognized — don't store a fake abbreviation
    return None, raw_stripped


def normalize_country_code(raw: str) -> tuple[str | None, str | None]:
    """
    Return (iso_alpha3_or_none, raw_name_or_none) for the given raw country string.

    country_code is VARCHAR(3) in the schema — we must never return a value
    longer than 3 characters.  When the raw value is not in our lookup table
    and is not already a 3-letter alpha code, we return (None, raw) so the
    caller can store the raw name in a separate country_name_raw field rather
    than silently truncating or violating the column constraint.
    """
    key = raw.strip().upper()
    if key in _COUNTRY_MAP:
        return _COUNTRY_MAP[key], raw.strip()
    # Already a valid 3-letter ISO code
    if len(key) == 3 and key.isalpha():
        return key, raw.strip()
    # Unknown long-form name: do NOT truncate into country_code (String(3) would
    # reject anything longer than 3 chars).  Return None so the caller stores the
    # raw value in country_name_raw instead.
    return None, raw.strip()


def build_canonical_fields(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Transform a raw NTSB record into a canonical field dict.

    Returns Python-typed values. The caller (ClaimWriter) is responsible
    for encoding each value via claim_value.encode() before DB insertion.

    Missing / unparseable fields are omitted entirely (not set to None).
    """
    out: dict[str, Any] = {}

    dt, precision = normalize_date(raw.get("EventDate"), raw.get("EventTime"))
    if dt:
        out["occurred_at"] = dt          # datetime — must be encoded before JSONB
        out["occurred_at_precision"] = precision

    lat, lon = normalize_coordinates(raw.get("LatDecimal"), raw.get("LongDecimal"))
    if lat is not None:
        out["location_coordinates"] = {"latitude": lat, "longitude": lon}

    location_parts = [
        p.strip() for p in [raw.get("City"), raw.get("State"), raw.get("Country")] if p
    ]
    if location_parts:
        out["location_text"] = ", ".join(location_parts)

    state_raw = (raw.get("State") or "").strip()
    if state_raw:
        code, raw_name = normalize_state_code(state_raw)
        if code is not None:
            out["state_code"] = code  # e.g. "OR", "NC" — a real postal code
        out["state_name_raw"] = raw_name  # always preserve the original value

    country_raw = (raw.get("Country") or "").strip()
    if country_raw:
        iso3, country_name = normalize_country_code(country_raw)
        if iso3 is not None:
            out["country_code"] = iso3
        if country_name:
            out["country_name_raw"] = country_name

    make, model = normalize_aircraft(raw.get("Make"), raw.get("Model"))
    if make:
        out["aircraft_make"] = make
    if model:
        out["aircraft_model"] = model

    reg = (raw.get("Registration") or "").strip().upper()
    if reg:
        out["aircraft_registration"] = reg

    raw_ab = raw.get("AmateurBuilt")
    if raw_ab is not None and str(raw_ab).strip():
        out["aircraft_amateur_built"] = str(raw_ab).upper() in ("YES", "TRUE", "1")

    # engine_type and num_engines are intentionally omitted: they are not
    # projected in AccidentRecord and writing claims for them creates phantom
    # data that sits in the claims table but never surfaces in the read model.

    operator = normalize_operator(raw.get("OperatorName"))
    if operator:
        out["operator_name"] = operator

    phase = normalize_phase(raw.get("PhaseOfFlight"))
    if phase:
        out["phase_of_flight"] = phase

    purpose = (raw.get("PurposeOfFlight") or "").strip()
    if purpose:
        out["purpose_of_flight"] = purpose

    wx = (raw.get("WeatherCondition") or "").strip().upper()
    if wx:
        out["weather_condition"] = wx

    # Only emit these when the source actually provides a value.
    # Missing != UNKNOWN: if the source field is absent, we have no claim
    # to make. Emitting UNKNOWN when the field is missing inflates apparent
    # coverage and can mislead confidence scoring.
    raw_severity = raw.get("HighestInjury")
    if raw_severity not in (None, ""):
        out["injury_severity"] = normalize_severity(raw_severity)

    raw_damage = raw.get("AircraftDamage")
    if raw_damage not in (None, ""):
        out["aircraft_damage"] = normalize_damage(raw_damage)

    fatal = _first_normalized_int(raw, "TotalFatalInjuries", "FatalInjuryCount")
    if fatal is not None:
        out["fatalities_total"] = fatal

    split_aliases = {
        "fatalities_crew": (
            "FatalCrewInjuries", "CrewFatalInjuries", "CrewFatalInjuryCount",
            "FatalInjuryCrewCount", "FatalitiesCrew", "CrewFatalities",
        ),
        "fatalities_passengers": (
            "FatalPassengerInjuries", "PassengerFatalInjuries",
            "PassengerFatalInjuryCount", "FatalInjuryPassengerCount",
            "FatalitiesPassengers", "PassengerFatalities",
        ),
        "serious_injuries_crew": (
            "SeriousCrewInjuries", "CrewSeriousInjuries",
            "CrewSeriousInjuryCount", "SeriousInjuryCrewCount",
        ),
        "serious_injuries_passengers": (
            "SeriousPassengerInjuries", "PassengerSeriousInjuries",
            "PassengerSeriousInjuryCount", "SeriousInjuryPassengerCount",
        ),
        "minor_injuries_crew": (
            "MinorCrewInjuries", "CrewMinorInjuries",
            "CrewMinorInjuryCount", "MinorInjuryCrewCount",
        ),
        "minor_injuries_passengers": (
            "MinorPassengerInjuries", "PassengerMinorInjuries",
            "PassengerMinorInjuryCount", "MinorInjuryPassengerCount",
        ),
        "uninjured_crew": ("UninjuredCrew", "CrewUninjured", "CrewUninjuredCount"),
        "uninjured_passengers": (
            "UninjuredPassengers", "PassengerUninjured", "PassengerUninjuredCount",
        ),
    }
    for field_name, aliases in split_aliases.items():
        value = _first_normalized_int(raw, *aliases)
        if value is not None:
            out[field_name] = value

    serious = _first_normalized_int(raw, "TotalSeriousInjuries", "SeriousInjuryCount")
    if serious is not None:
        out["serious_injuries"] = serious

    minor = _first_normalized_int(raw, "TotalMinorInjuries", "MinorInjuryCount")
    if minor is not None:
        out["minor_injuries"] = minor

    pax = normalize_int(raw.get("AboardPassengerCount"))
    crew = normalize_int(raw.get("AboardCrewCount"))
    # Crew-only accidents are valid; if either count is present we can sum.
    # We only treat missing as zero when the *other* side is known — if both
    # are missing we emit nothing (unknown total is different from zero total).
    if pax is not None or crew is not None:
        out["aboard_total"] = (pax or 0) + (crew or 0)

    raw_inv = raw.get("InvestigationType")
    if raw_inv not in (None, ""):
        out["investigation_status"] = normalize_investigation_status(raw_inv)

    pc = (raw.get("ProbableCause") or "").strip()
    if pc:
        out["probable_cause"] = pc

    rn = (raw.get("ReportNumber") or "").strip()
    if rn:
        out["ntsb_report_number"] = rn

    return out
