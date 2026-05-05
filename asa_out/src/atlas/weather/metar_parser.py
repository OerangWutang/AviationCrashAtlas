"""
metar_parser.py — Minimal, stdlib-only METAR field extractor.

Parses the subset of METAR fields needed for weather context display.
Designed for correctness over completeness: unknown fields are left as None
rather than guessed.

Extension points
----------------
- Replace parse_metar() with a call to python-metar or metar-taf-parser
  when richer parsing is required.
- add_cloud_layers() and add_remarks() placeholders mark where cloud-detail
  and RMK-section parsing can be added.

Unit system: all output is in canonical units —
  temperature/dew_point: Celsius
  wind_speed / gust: knots
  visibility: metres
  pressure: hPa
  ceiling: feet AGL

Sample METARs exercised in tests:
  KJFK 121651Z 18012KT 10SM FEW025 SCT250 26/18 A2992 RMK AO2
  EHAM 051025Z 22015G25KT 9999 FEW020 SCT035 12/07 Q1013 NOSIG
  KLAX 151753Z 25008KT 6SM HZ FEW012 22/16 A3001 RMK AO2
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Conversion constants
# ---------------------------------------------------------------------------

_SM_TO_M   = 1609.344   # statute miles → metres
_INHG_TO_HPA = 33.8639  # inHg → hPa


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class ParsedMetar:
    """Parsed METAR fields in canonical units.  None = field absent or unparseable."""
    station: str | None = None
    observation_time_raw: str | None = None   # raw DDHHmmZ token

    temperature_c: float | None = None
    dew_point_c: float | None = None

    wind_direction_degrees: int | None = None
    wind_speed_kt: float | None = None
    wind_gust_kt: float | None = None
    wind_variable: bool = False

    visibility_m: float | None = None
    visibility_raw: str | None = None

    ceiling_ft: int | None = None              # lowest BKN/OVC layer in feet
    cloud_layers: list[dict[str, Any]] = field(default_factory=list)

    altimeter_hpa: float | None = None
    altimeter_raw: str | None = None

    precipitation_type: str | None = None
    thunderstorm_present: bool = False

    icing_risk: str = "unknown"
    turbulence_risk: str = "unknown"
    flight_rules: str = "unknown"

    remarks: str | None = None
    raw: str = ""


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_RE_STATION  = re.compile(r"^([A-Z]{4})\b")
_RE_TIME     = re.compile(r"\b(\d{6}Z)\b")
_RE_WIND_VRB = re.compile(r"\bVRB(\d{2,3})KT\b")
_RE_WIND     = re.compile(r"\b(\d{3})(\d{2,3})(?:G(\d{2,3}))?KT\b")
_RE_VIS_SM   = re.compile(r"\b(\d+(?:\s\d/\d)?|\d/\d)SM\b")
_RE_VIS_M    = re.compile(r"\b(\d{4})\b(?!\s*FT)")   # 4-digit metres (ICAO)
_RE_TEMP     = re.compile(r"\b(M?\d{1,2})/(M?\d{1,2})\b")
_RE_ALTQ     = re.compile(r"\bQ(\d{4})\b")            # QNH in hPa (ICAO)
_RE_ALTA     = re.compile(r"\bA(\d{4})\b")            # altimeter in inHg × 100
_RE_CLOUD    = re.compile(r"\b(FEW|SCT|BKN|OVC)(\d{3})\b")
_RE_TS       = re.compile(r"\bTS\b")
_RE_RMK      = re.compile(r"\bRMK\s+(.+)$")

_PRECIP_MAP = {
    "RA": "rain", "SN": "snow", "FZRA": "freezing_rain",
    "DZ": "drizzle", "GR": "hail", "SG": "snow_grains",
    "IC": "ice_crystals", "PL": "ice_pellets",
}
_PRECIP_RE = re.compile(
    r"\b(?:\+|-|VC)?(?:SH|FZ|BL|DR|MI|PR|BC|TS|VC)?("
    + "|".join(_PRECIP_MAP.keys())
    + r")\b"
)


def _parse_temp_token(token: str) -> float:
    """Convert METAR temp token (M02 → -2, 26 → 26) to float."""
    if token.startswith("M"):
        return -float(token[1:])
    return float(token)


def _compute_flight_rules(
    visibility_m: float | None,
    ceiling_ft: int | None,
) -> str:
    """
    Standard US FAA flight rule categories.
    LIFR: ceil < 500 ft OR vis < 1 sm (1609 m)
    IFR:  ceil 500–999 ft OR vis 1–2 sm
    MVFR: ceil 1000–2999 ft OR vis 3–4 sm
    VFR:  ceil ≥ 3000 ft AND vis ≥ 5 sm
    """
    if visibility_m is None and ceiling_ft is None:
        return "unknown"

    ceil = ceiling_ft if ceiling_ft is not None else 99999
    vis  = visibility_m if visibility_m is not None else 99999

    if ceil < 500 or vis < 1609:
        return "lifr"
    if ceil < 1000 or vis < 3219:
        return "ifr"
    if ceil < 3000 or vis < 8047:
        return "mvfr"
    return "vfr"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_metar(raw: str) -> ParsedMetar:
    """
    Parse a raw METAR string into a ParsedMetar dataclass.

    Unrecognised fields are silently ignored and left as None — never
    guessed.  The raw string is always preserved.
    """
    result = ParsedMetar(raw=raw.strip())
    text = raw.strip()

    # Station identifier (first 4-letter token)
    m = _RE_STATION.match(text)
    if m:
        result.station = m.group(1)

    # Observation time (DDHHmmZ)
    m = _RE_TIME.search(text)
    if m:
        result.observation_time_raw = m.group(1)

    # Wind — variable
    m = _RE_WIND_VRB.search(text)
    if m:
        result.wind_variable = True
        result.wind_speed_kt = float(m.group(1))
    else:
        # Directional wind
        m = _RE_WIND.search(text)
        if m:
            result.wind_direction_degrees = int(m.group(1))
            result.wind_speed_kt = float(m.group(2))
            if m.group(3):
                result.wind_gust_kt = float(m.group(3))

    # Visibility — statute miles (US)
    m = _RE_VIS_SM.search(text)
    if m:
        raw_vis = m.group(1).strip()
        result.visibility_raw = raw_vis + "SM"
        # Handle fractional: "1 3/4" or "3/4"
        parts = raw_vis.split()
        if "/" in parts[-1]:
            num, den = parts[-1].split("/")
            frac = int(num) / int(den)
            whole = float(parts[0]) if len(parts) > 1 else 0.0
            sm = whole + frac
        else:
            sm = float(raw_vis)
        result.visibility_m = round(sm * _SM_TO_M, 1)
    else:
        # ICAO 4-digit metres
        m = _RE_VIS_M.search(text)
        if m:
            raw_m = int(m.group(1))
            if raw_m == 9999:
                result.visibility_m = 10000.0  # 10 km+
            else:
                result.visibility_m = float(raw_m)
            result.visibility_raw = m.group(1)

    # Cloud layers
    for m in _RE_CLOUD.finditer(text):
        cover, height_token = m.group(1), m.group(2)
        height_ft = int(height_token) * 100
        result.cloud_layers.append({"cover": cover, "height_ft": height_ft})
        # Ceiling = lowest BKN or OVC
        if cover in ("BKN", "OVC"):
            if result.ceiling_ft is None or height_ft < result.ceiling_ft:
                result.ceiling_ft = height_ft

    # Temperature / dew point
    m = _RE_TEMP.search(text)
    if m:
        result.temperature_c = _parse_temp_token(m.group(1))
        result.dew_point_c   = _parse_temp_token(m.group(2))

    # Altimeter — QNH (hPa, ICAO)
    m = _RE_ALTQ.search(text)
    if m:
        result.altimeter_hpa = float(m.group(1))
        result.altimeter_raw = f"Q{m.group(1)}"
    else:
        # Altimeter — A (inHg × 100, US)
        m = _RE_ALTA.search(text)
        if m:
            inhg = int(m.group(1)) / 100.0
            result.altimeter_hpa = round(inhg * _INHG_TO_HPA, 2)
            result.altimeter_raw = f"A{m.group(1)}"

    # Thunderstorm
    if _RE_TS.search(text):
        result.thunderstorm_present = True

    # Precipitation type (first match wins)
    m = _PRECIP_RE.search(text)
    if m:
        result.precipitation_type = _PRECIP_MAP.get(m.group(1))

    # Remarks
    m = _RE_RMK.search(text)
    if m:
        result.remarks = m.group(1).strip()

    # Derived fields
    result.flight_rules = _compute_flight_rules(result.visibility_m, result.ceiling_ft)

    return result
