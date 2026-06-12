"""Stage 2 — Structured filter extractor.

File location: src/atlas/application/services/ai_search/filter_extractor.py

One DeepSeek call to convert a free-text query into an
ExtendedParsedQuery. JSON mode + strict schema.

On any failure (DeepSeek down, invalid JSON, schema mismatch),
falls back to the existing Phase 7 deterministic parser. Both
together cover the field — the LLM handles natural-language
sophistication (day-of-week, cause phrases, sort orders); the
deterministic parser handles the basics (dates, aircraft, operator)
even when the LLM is unavailable.
"""

from __future__ import annotations

import logging
from datetime import date

from atlas.application.services.nl_query_parser import parse_nl_query
from atlas.domain.causality.entities import HfacsCategory
from atlas.domain.nl_search.extended_query import (
    DayOfWeek,
    ExtendedParsedQuery,
    SearchIntent,
    SortBy,
)
from atlas.infrastructure.llm.deepseek_client import (
    DeepSeekError,
    call_deepseek,
    parse_json_or_none,
)

logger = logging.getLogger(__name__)


# The schema description is kept tight so the LLM doesn't drift.
# Every field is optional; null/missing values are the default.
_EXTRACTOR_PROMPT_TEMPLATE = """\
You are an aviation accident search query parser. Convert the user's \
free-text query into structured JSON filters.

OUTPUT SCHEMA (every field optional; omit or null if not mentioned):
{{
  "operator":              string|null,    // airline name, e.g. "Delta Air Lines"
  "aircraft_type":         string|null,    // e.g. "Boeing 747", "Airbus A320"
  "country":               string|null,    // ISO country name or 2-letter code
  "event_date_from":       "YYYY-MM-DD"|null,
  "event_date_to":         "YYYY-MM-DD"|null,
  "fatalities_min":        integer|null,   // inclusive lower bound
  "fatalities_max":        integer|null,   // inclusive upper bound
  "fatal_only":            boolean,        // true if "fatal" mentioned
  "non_fatal_only":        boolean,        // true if "non-fatal" mentioned
  "day_of_week_in":        ["monday"|"tuesday"|...|"sunday"],  // empty if not mentioned
  "cause_keywords":        [string],       // e.g. ["engine failure", "bird strike"]
  "shelo_factor_classes":  ["SOFTWARE"|"HARDWARE"|"ENVIRONMENT"|"LIVEWARE"],
  "hfacs_category_codes":  [string],       // HFACS codes if user mentions named categories
  "sort_by":               "date_desc"|"date_asc"|"fatalities_desc"|"fatalities_asc"|"relevance",
  "limit":                 integer         // 1-50, default 20
}}

EXTRACTION RULES:
1. Date ranges: "between 1998 and 2022" → from=1998-01-01, to=2022-12-31.
   "before 2020" → to=2019-12-31. "after 2018" → from=2019-01-01.
   "in 2010" → from=2010-01-01, to=2010-12-31.
   "last decade" → from = ten years ago, to = today.
2. Day of week: only include weekdays explicitly mentioned. "weekend" → ["saturday","sunday"].
3. Cause keywords: extract specific failure modes the user names.
   "engine failure" → ["engine failure"]
   "bird strike" → ["bird strike"]
   "icing" → ["icing"] AND shelo_factor_classes=["ENVIRONMENT"]
   "pilot error" → ["pilot error"] AND shelo_factor_classes=["LIVEWARE"]
4. SHELO classes — infer from cause keywords:
   engine/structural/airframe → HARDWARE
   weather/icing/turbulence/windshear → ENVIRONMENT
   pilot/crew/fatigue/human → LIVEWARE
   software/FMS/FADEC → SOFTWARE
5. Sort: "deadliest"/"worst" → "fatalities_desc". "oldest" → "date_asc".
   "most recent"/"latest" → "date_desc". Default: "date_desc".
6. fatal_only: true if user says "fatal accidents" (not "non-fatal").
7. Limit: extract "top 10", "first 5"; otherwise 20.

Today's date is %(today)s.

Return JSON ONLY. No prose, no code fences, no commentary.

QUERY: %(query)s

JSON:"""


async def extract_filters(
    query: str,
    intent: SearchIntent,
    hfacs_categories: list[HfacsCategory],
) -> ExtendedParsedQuery:
    """Extract structured filters from a free-text query.

    On any LLM failure, falls back to the deterministic Phase 7
    parser and marks the result as a fallback so the caller can
    log it for observability.
    """
    prompt = _EXTRACTOR_PROMPT_TEMPLATE % {
        "today": date.today().isoformat(),
        "query": query.strip(),
    }

    try:
        response = await call_deepseek(
            prompt=prompt,
            json_mode=True,
            temperature=0.0,
            max_tokens=400,
        )
        parsed = parse_json_or_none(response.content)
        if parsed is None:
            logger.warning(
                "Filter extractor: invalid JSON from DeepSeek — falling back to deterministic parser. raw=%r",
                response.content[:200],
            )
            return _deterministic_fallback(query, intent, hfacs_categories)
        return _json_to_extended_query(parsed, query, intent)
    except DeepSeekError as exc:
        logger.warning(
            "Filter extractor: DeepSeek call failed — falling back. error=%s", exc
        )
        return _deterministic_fallback(query, intent, hfacs_categories)


# ── JSON → ExtendedParsedQuery ────────────────────────────────────────────────


def _safe_date(value: object) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _safe_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, str)):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return None


def _safe_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if isinstance(v, (str, int)) and str(v).strip()]


def _safe_days(value: object) -> list[DayOfWeek]:
    out: list[DayOfWeek] = []
    for raw in _safe_str_list(value):
        try:
            out.append(DayOfWeek(raw.lower()))
        except ValueError:
            continue
    return out


def _safe_sort(value: object) -> SortBy:
    try:
        return SortBy(str(value).strip().lower())
    except (ValueError, AttributeError):
        return SortBy.DATE_DESC


def _safe_bool(value: object) -> bool:
    return isinstance(value, bool) and value


def _json_to_extended_query(
    data: dict[str, object],
    raw_query: str,
    intent: SearchIntent,
) -> ExtendedParsedQuery:
    """Map the LLM's JSON to a validated ExtendedParsedQuery."""
    limit = _safe_int(data.get("limit")) or 20
    limit = max(1, min(limit, 50))

    operator = data.get("operator")
    aircraft = data.get("aircraft_type")
    country = data.get("country")

    return ExtendedParsedQuery(
        intent=intent,
        operator=str(operator).strip() if isinstance(operator, str) and operator.strip() else None,
        aircraft_type=str(aircraft).strip() if isinstance(aircraft, str) and aircraft.strip() else None,
        country=str(country).strip() if isinstance(country, str) and country.strip() else None,
        event_date_from=_safe_date(data.get("event_date_from")),
        event_date_to=_safe_date(data.get("event_date_to")),
        fatalities_min=_safe_int(data.get("fatalities_min")),
        fatalities_max=_safe_int(data.get("fatalities_max")),
        fatal_only=_safe_bool(data.get("fatal_only")),
        non_fatal_only=_safe_bool(data.get("non_fatal_only")),
        day_of_week_in=_safe_days(data.get("day_of_week_in")),
        cause_keywords=_safe_str_list(data.get("cause_keywords")),
        shelo_factor_classes=_safe_str_list(data.get("shelo_factor_classes")),
        hfacs_category_codes=_safe_str_list(data.get("hfacs_category_codes")),
        sort_by=_safe_sort(data.get("sort_by")),
        limit=limit,
        free_text_remainder=raw_query,
        extractor_confidence=0.9,
        fallback_to_deterministic=False,
    )


# ── Deterministic fallback ────────────────────────────────────────────────────


def _deterministic_fallback(
    query: str,
    intent: SearchIntent,
    hfacs_categories: list[HfacsCategory],
) -> ExtendedParsedQuery:
    """When the LLM is unavailable, fall back to Phase 7's parser.

    The fallback covers fewer features (no day-of-week, no cause
    keywords) but keeps the system useful and honest about its
    degraded mode via `fallback_to_deterministic=True`.
    """
    parsed = parse_nl_query(query, hfacs_categories=hfacs_categories)
    return ExtendedParsedQuery(
        intent=intent,
        operator=parsed.operator,
        aircraft_type=parsed.aircraft_type,
        country=parsed.country,
        event_date_from=parsed.event_date_from,
        event_date_to=parsed.event_date_to,
        fatalities_min=parsed.fatalities_min,
        fatalities_max=parsed.fatalities_max,
        fatal_only=parsed.fatal_only,
        non_fatal_only=parsed.non_fatal_only,
        hfacs_category_codes=parsed.hfacs_category_codes,
        shelo_factor_classes=parsed.shelo_factor_classes,
        free_text_remainder=parsed.free_text_remainder,
        extractor_confidence=parsed.confidence,
        fallback_to_deterministic=True,
    )


__all__ = ["extract_filters"]
