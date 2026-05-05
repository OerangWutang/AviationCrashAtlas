"""
Claim value serialization.

Fixes the bug where raw Python datetime/date objects were written into
JSONB claim fields, causing serialization errors at insert time.

All claim values must be encoded via encode() before writing to the DB,
and decoded via decode() when reading back for use in application logic.

The envelope format is:
  {"v": <json_safe_value>, "type": "<type_tag>"}

Type tags: str, int, float, bool, datetime, date, list, dict, null
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

_CLAIM_VALUE_TYPE_TAGS = frozenset({
    "str", "int", "float", "bool", "datetime", "date", "list", "dict", "null",
})


def is_envelope(value: Any) -> bool:
    """Return True when ``value`` already looks like a claim-value envelope.

    This is used at ingestion boundaries to prevent accidental nested encoding.
    Normalisers should return Python-typed values; only ClaimWriter encodes.
    """
    return (
        isinstance(value, dict)
        and set(value.keys()) == {"v", "type"}
        and value.get("type") in _CLAIM_VALUE_TYPE_TAGS
    )


def contains_envelope(value: Any) -> bool:
    """Return True if ``value`` or any nested value is already an envelope."""
    if is_envelope(value):
        return True
    if isinstance(value, dict):
        return any(contains_envelope(v) for v in value.values())
    if isinstance(value, list):
        return any(contains_envelope(v) for v in value)
    return False

def encode(value: Any) -> dict[str, Any]:
    """
    Wrap a Python value in the canonical claim envelope.
    Returns a JSON-safe dict safe to insert into a JSONB column.
    """
    if value is None:
        return {"v": None, "type": "null"}
    if isinstance(value, bool):
        # bool must come before int since bool is a subclass of int
        return {"v": value, "type": "bool"}
    if isinstance(value, datetime):
        return {"v": value.isoformat(), "type": "datetime"}
    if isinstance(value, date):
        return {"v": value.isoformat(), "type": "date"}
    if isinstance(value, int):
        return {"v": value, "type": "int"}
    if isinstance(value, float):
        return {"v": value, "type": "float"}
    if isinstance(value, str):
        return {"v": value, "type": "str"}
    if isinstance(value, list):
        return {"v": [encode(item) for item in value], "type": "list"}
    if isinstance(value, dict):
        return {"v": {k: encode(v) for k, v in value.items()}, "type": "dict"}
    # Fallback: stringify unknown types rather than crashing
    return {"v": str(value), "type": "str"}


def decode(envelope: dict[str, Any]) -> Any:
    """
    Reconstruct the original Python value from a claim envelope.
    Raises ValueError on unrecognised type tags.
    """
    if not isinstance(envelope, dict) or "v" not in envelope:
        raise ValueError(f"Invalid claim envelope: {envelope!r}")

    v = envelope["v"]
    t = envelope.get("type", "str")

    if t == "null" or v is None:
        return None
    if t == "bool":
        return bool(v)
    if t == "datetime":
        return datetime.fromisoformat(v)
    if t == "date":
        return date.fromisoformat(v)
    if t == "int":
        return int(v)
    if t == "float":
        return float(v)
    if t == "str":
        return str(v)
    if t == "list":
        return [decode(item) for item in v]
    if t == "dict":
        return {k: decode(val) for k, val in v.items()}
    raise ValueError(f"Unknown claim value type tag: {t!r}")


def display(envelope: dict[str, Any]) -> str:
    """Human-readable string for a claim value — used in UI and logs."""
    try:
        val = decode(envelope)
    except (ValueError, KeyError):
        return repr(envelope)

    if val is None:
        return "—"
    if isinstance(val, bool):
        return "Yes" if val else "No"
    if isinstance(val, datetime):
        # If the datetime is timezone-aware, show the offset label.
        # If it is naive, it is a local accident time with unknown timezone —
        # do NOT append "UTC" because that would be a false claim.
        if val.tzinfo is not None:
            return val.strftime("%Y-%m-%d %H:%M %Z")
        return val.strftime("%Y-%m-%d %H:%M (local time, tz unknown)")
    if isinstance(val, date):
        return val.strftime("%Y-%m-%d")
    if isinstance(val, dict):
        # Coordinate pairs
        if "latitude" in val and "longitude" in val:
            return f"{val['latitude']:.4f}, {val['longitude']:.4f}"
        return str(val)
    return str(val)


# Fields where any numeric difference is a real conflict.
# A 1-fatality / injury discrepancy is always significant regardless of scale.
_EXACT_INT_FIELDS = frozenset({
    "fatalities_total",
    "fatalities_crew",
    "fatalities_passengers",
    "serious_injuries",
    "serious_injuries_crew",
    "serious_injuries_passengers",
    "minor_injuries",
    "minor_injuries_crew",
    "minor_injuries_passengers",
    "uninjured_crew",
    "uninjured_passengers",
    "aboard_total",
})


def values_conflict(a: dict[str, Any], b: dict[str, Any], field_name: str) -> bool:
    """
    Return True if two claim envelopes genuinely disagree.

    Rules:
    - null vs anything: no conflict (null means "unknown", not "zero")
    - Fatality/injury counts: exact integer match required — any difference
      is a conflict (a 1-fatality discrepancy is always significant)
    - Other numeric fields: conflict if difference > 5% of the larger value
    - String fields: case-insensitive comparison after stripping
    - Coordinate fields: conflict if distance > ~0.5° (~55 km)
    """
    try:
        val_a = decode(a)
        val_b = decode(b)
    except (ValueError, KeyError):
        return False

    if val_a is None or val_b is None:
        return False

    # Coordinate pairs
    if field_name == "location_coordinates":
        if isinstance(val_a, dict) and isinstance(val_b, dict):
            lat_diff = abs(float(val_a.get("latitude", 0)) - float(val_b.get("latitude", 0)))
            lon_diff = abs(float(val_a.get("longitude", 0)) - float(val_b.get("longitude", 0)))
            return lat_diff > 0.5 or lon_diff > 0.5
        return False

    # Fatality / injury counts — exact match required
    if field_name in _EXACT_INT_FIELDS:
        if isinstance(val_a, (int, float)) and isinstance(val_b, (int, float)):
            return int(val_a) != int(val_b)
        return val_a != val_b

    # Other numeric fields — percentage threshold
    if isinstance(val_a, (int, float)) and isinstance(val_b, (int, float)):
        if val_a == 0 and val_b == 0:
            return False
        denominator = max(abs(float(val_a)), abs(float(val_b)), 1)
        return abs(float(val_a) - float(val_b)) / denominator > 0.05

    # String fields
    if isinstance(val_a, str) and isinstance(val_b, str):
        return val_a.strip().upper() != val_b.strip().upper()

    return val_a != val_b
