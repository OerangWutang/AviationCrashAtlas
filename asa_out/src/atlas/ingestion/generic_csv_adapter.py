"""
Generic CSV ingestion adapter.

Any tabular source (ASN, ICAO, BAAA, a custom research dataset) can be
ingested by providing a source mapping that describes how that source's
column names map to the canonical field names the claim store understands.

Mapping format (JSON)
---------------------
{
  "source_id":       "src-asn-001",
  "record_id_field": "accident_id",    # column that uniquely identifies a record
  "field_map": {                        # source_column → canonical_field
    "Date":              "occurred_at",
    "Registration":      "aircraft_registration",
    "Operator":          "operator_name",
    "Fat.":              "fatalities_total",
    "Country":           "location_text",
    "Type":              "aircraft_make"
  },
  "value_transforms": {                 # optional per-field overrides
    "injury_severity": {"fatal": "FATAL", "serious": "SERIOUS"}
  },
  "delimiter":      ",",                # default ","
  "encoding":       "utf-8",            # default "utf-8"
  "skip_rows":      0                   # header rows to skip beyond the first
}

The adapter re-uses all normalizer functions from normalizer.py — no
per-source normalization code is needed unless the source has genuinely
unusual data formats.

Usage
-----
    atlas ingest generic-csv \\
        --filepath  asn_export.csv \\
        --mapping   src/atlas/ingestion/source_mappings/asn_mapping.json

    atlas ingest generic-csv \\
        --filepath  icao_report.csv \\
        --mapping   custom_mapping.json \\
        --dry-run
"""
from __future__ import annotations

import csv
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

import structlog

from atlas.models.orm import RawSnapshot

log = structlog.get_logger(__name__)

# ── Mapping helpers ────────────────────────────────────────────────────────────

class SourceMapping:
    """
    Validated source mapping loaded from a JSON file.

    Attributes
    ----------
    source_id       str           Registry ID (must exist in sources table)
    record_id_field str | None    Column whose value is the source's unique ID
    field_map       dict          source_col → canonical_field
    value_transforms dict         canonical_field → {raw_value: canonical_value}
    delimiter       str           CSV delimiter (default ",")
    encoding        str           File encoding (default "utf-8")
    skip_rows       int           Extra header rows to skip
    """

    def __init__(self, data: dict[str, Any]) -> None:
        self.source_id: str = data["source_id"]
        self.record_id_field: str | None = data.get("record_id_field")
        self.field_map: dict[str, str] = data.get("field_map", {})
        self.value_transforms: dict[str, dict[str, str]] = data.get("value_transforms", {})
        self.delimiter: str = data.get("delimiter", ",")
        self.encoding: str = data.get("encoding", "utf-8")
        self.skip_rows: int = data.get("skip_rows", 0)

        if not self.source_id:
            raise ValueError("source_id is required in mapping")
        if not self.field_map:
            raise ValueError("field_map must define at least one column mapping")

    @classmethod
    def from_file(cls, path: Path | str) -> SourceMapping:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Mapping file not found: {p}")
        with p.open(encoding="utf-8") as f:
            data = json.load(f)
        return cls(data)

    def apply_transforms(
        self, canonical_field: str, raw_value: str | None
    ) -> str | None:
        """Apply optional value-level remapping for a single field."""
        if raw_value is None:
            return None
        transforms = self.value_transforms.get(canonical_field, {})
        return transforms.get(raw_value, transforms.get(raw_value.lower(), raw_value))


# ── Row → canonical dict ───────────────────────────────────────────────────────

def _row_to_raw(row: dict[str, str], mapping: SourceMapping) -> dict[str, Any]:
    """
    Convert one CSV row to a raw dict using the source mapping.

    The raw dict uses the *source's* column names as keys (preserving
    the original snapshot) plus a ``__canonical__`` sub-dict holding
    the pre-mapped values.  The pipeline stores the full row in
    raw_snapshots and works from __canonical__ for normalisation.
    """
    raw: dict[str, Any] = dict(row)          # preserve original keys
    canonical_raw: dict[str, Any] = {}

    for src_col, canonical_field in mapping.field_map.items():
        val = row.get(src_col, "").strip() or None
        val = mapping.apply_transforms(canonical_field, val)
        if val is not None:
            canonical_raw[canonical_field] = val

    raw["__canonical__"] = canonical_raw
    raw["__source_id__"] = mapping.source_id
    if mapping.record_id_field:
        raw["__record_id__"] = row.get(mapping.record_id_field, "").strip() or None
    else:
        raw["__record_id__"] = None

    return raw


# ── Normalisation: canonical_raw → ClaimWriter input ──────────────────────────

def normalise_generic(canonical_raw: dict[str, Any]) -> dict[str, Any]:
    """
    Apply the same normaliser functions used by the NTSB pipeline to the
    pre-mapped canonical_raw dict. Returns Python-typed values only.

    ClaimWriter.write_fields() is the single encoding boundary for claim
    storage. This function must not call claim_value.encode(); otherwise
    generic CSV ingestion stores nested envelopes such as
    {"v": {"v": 3, "type": "int"}, "type": "dict"}.

    This is intentionally a subset of build_canonical_fields() — only the
    fields that are commonly available across sources are included here.
    Source-specific normalisers can extend this by sub-classing or by
    adding more entries to the mapping's field_map.
    """
    from atlas.ingestion.normalizer import (
        normalize_aircraft,
        normalize_coordinates,
        normalize_country_code,
        normalize_damage,
        normalize_date,
        normalize_int,
        normalize_investigation_status,
        normalize_operator,
        normalize_phase,
        normalize_severity,
        normalize_state_code,
    )
    out: dict[str, Any] = {}

    # ── Occurrence date ───────────────────────────────────────────────────────
    raw_date = canonical_raw.get("occurred_at") or canonical_raw.get("date")
    if raw_date:
        dt, precision = normalize_date(raw_date, None)
        if dt:
            out["occurred_at"] = dt
            out["occurred_at_precision"] = precision

    # ── Location ─────────────────────────────────────────────────────────────
    if loc := canonical_raw.get("location_text"):
        out["location_text"] = str(loc).strip()

    raw_lat = canonical_raw.get("latitude") or canonical_raw.get("location_lat")
    raw_lon = canonical_raw.get("longitude") or canonical_raw.get("location_lon")
    if raw_lat is not None and raw_lon is not None:
        lat, lon = normalize_coordinates(raw_lat, raw_lon)
        if lat is not None and lon is not None:
            out["location_coordinates"] = {"latitude": lat, "longitude": lon}

    raw_country = canonical_raw.get("country_code") or canonical_raw.get("country")
    if raw_country:
        code, text = normalize_country_code(str(raw_country))
        if code:
            out["country_code"] = code
        if text and "location_text" not in out:
            out["location_text"] = text

    raw_state = canonical_raw.get("state_code") or canonical_raw.get("state")
    if raw_state:
        state_code, _normalised = normalize_state_code(str(raw_state))
        if state_code:
            out["state_code"] = state_code

    # ── Aircraft ─────────────────────────────────────────────────────────────
    raw_make = canonical_raw.get("aircraft_make")
    raw_model = canonical_raw.get("aircraft_model")
    if raw_make or raw_model:
        make, model = normalize_aircraft(raw_make, raw_model)
        if make:
            out["aircraft_make"] = make
        if model:
            out["aircraft_model"] = model

    if reg := canonical_raw.get("aircraft_registration"):
        out["aircraft_registration"] = str(reg).strip().upper()

    raw_amateur = canonical_raw.get("aircraft_amateur_built")
    if raw_amateur is not None:
        val_str = str(raw_amateur).strip().lower()
        if val_str in ("yes", "true", "1", "y"):
            out["aircraft_amateur_built"] = True
        elif val_str in ("no", "false", "0", "n"):
            out["aircraft_amateur_built"] = False

    # ── Operator ─────────────────────────────────────────────────────────────
    if op := normalize_operator(canonical_raw.get("operator_name")):
        out["operator_name"] = op

    # ── Flight ───────────────────────────────────────────────────────────────
    if phase := normalize_phase(canonical_raw.get("phase_of_flight")):
        out["phase_of_flight"] = phase

    if wx := canonical_raw.get("weather_condition"):
        normalised_wx = str(wx).strip().upper()
        if normalised_wx in ("VMC", "IMC", "UNKNOWN"):
            out["weather_condition"] = normalised_wx

    if purpose := canonical_raw.get("purpose_of_flight"):
        out["purpose_of_flight"] = str(purpose).strip()

    # ── Outcome ───────────────────────────────────────────────────────────────
    out["injury_severity"] = normalize_severity(canonical_raw.get("injury_severity"))
    out["aircraft_damage"] = normalize_damage(canonical_raw.get("aircraft_damage"))

    for int_field in (
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
    ):
        if (raw_val := canonical_raw.get(int_field)) is not None:
            n = normalize_int(raw_val)
            if n is not None:
                out[int_field] = n

    # ── Investigation ─────────────────────────────────────────────────────────
    out["investigation_status"] = normalize_investigation_status(
        canonical_raw.get("investigation_status")
    )

    if pc := canonical_raw.get("probable_cause"):
        out["probable_cause"] = str(pc).strip()

    return {k: v for k, v in out.items() if v is not None}


# ── Snapshot builder ──────────────────────────────────────────────────────────

def build_generic_snapshot(
    raw_row: dict[str, Any],
    *,
    source_id: str,
    source_record_id: str | None,
    run_id: str,
) -> RawSnapshot:
    """Build an immutable RawSnapshot from a generic CSV row."""
    payload_str = json.dumps(raw_row, sort_keys=True, default=str)
    payload_hash = hashlib.sha256(payload_str.encode()).hexdigest()
    return RawSnapshot(
        id=str(uuid.uuid4()),
        source_id=source_id,
        source_record_id=source_record_id,
        payload=raw_row,
        payload_hash=payload_hash,
        source_url=None,
        ingestion_run_id=run_id,
    )


# ── CSV loader ────────────────────────────────────────────────────────────────

def load_csv_with_mapping(
    filepath: str | Path,
    mapping: SourceMapping,
) -> list[dict[str, Any]]:
    """
    Load a CSV file and apply the source mapping to every row.

    Returns a list of raw dicts suitable for IngestionPipeline._process_generic().
    Empty rows and rows where all mapped values are blank are skipped.
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"CSV file not found: {path}")

    records: list[dict[str, Any]] = []
    with path.open(encoding=mapping.encoding, newline="") as f:
        # Skip extra header rows beyond the first
        for _ in range(mapping.skip_rows):
            next(f, None)
        reader = csv.DictReader(f, delimiter=mapping.delimiter)
        for i, row in enumerate(reader):
            # Strip BOM from first key if present (common in Excel exports)
            clean_row: dict[str, str] = {}
            for k, v in row.items():
                clean_k = k.lstrip("\ufeff").strip() if k else k
                clean_row[clean_k] = (v or "").strip()

            raw = _row_to_raw(clean_row, mapping)

            # Skip rows with no usable canonical data
            if not raw.get("__canonical__"):
                log.debug("generic_csv.row_skipped_no_canonical", row_index=i)
                continue

            records.append(raw)

    log.info("generic_csv.loaded", filepath=str(path), records=len(records))
    return records


# ── Bundled source mappings ───────────────────────────────────────────────────

MAPPING_DIR = Path(__file__).parent / "source_mappings"


def list_bundled_mappings() -> list[str]:
    """Return names of all bundled mapping files (without .json extension)."""
    if not MAPPING_DIR.exists():
        return []
    return [p.stem for p in MAPPING_DIR.glob("*.json")]


def load_bundled_mapping(name: str) -> SourceMapping:
    """Load a bundled mapping by name (e.g. 'asn', 'icao_accidents')."""
    path = MAPPING_DIR / f"{name}.json"
    if not path.exists():
        available = list_bundled_mappings()
        raise FileNotFoundError(
            f"Bundled mapping {name!r} not found. "
            f"Available: {available or ['(none)']}"
        )
    return SourceMapping.from_file(path)
