"""Extended parsed query for AI search.

File location: src/atlas/domain/nl_search/extended_query.py

Extends ParsedQuery (Phase 7) with fields the AI pipeline needs:
- intent           — what the user wants the answer shaped like
- day_of_week_in   — Mon..Sun filter (post-SQL filter, no index needed)
- cause_keywords   — phrases matched against narrative_markdown
- sort_by          — deterministic ordering choice
- limit            — explicit cap (overrides default)

This is purely additive — does not modify ParsedQuery itself, so
the existing parser, ExecuteNlSearch, and persistence layers keep
working unchanged.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import Field

from atlas.domain.entities import DomainModel


class SearchIntent(StrEnum):
    """What the user wants the answer to look like."""

    LIST = "list"            # tabular or bulleted list of matching accidents
    SUMMARIZE = "summarize"  # narrative synthesis across the result set
    COMPARE = "compare"      # side-by-side comparison of N accidents
    RANK = "rank"            # ordered ranking by some criterion
    ANALYZE = "analyze"      # causal pattern analysis citing HFACS/SHELO


class SortBy(StrEnum):
    """Deterministic sort options applied after retrieval.

    The LLM never sorts results — it only chooses which sort key
    the user wanted. The DB or Python applies the sort.
    """

    DATE_DESC = "date_desc"          # default — most recent first
    DATE_ASC = "date_asc"            # oldest first
    FATALITIES_DESC = "fatalities_desc"  # deadliest first
    FATALITIES_ASC = "fatalities_asc"
    RELEVANCE = "relevance"          # SQL FTS rank score


class DayOfWeek(StrEnum):
    MONDAY = "monday"
    TUESDAY = "tuesday"
    WEDNESDAY = "wednesday"
    THURSDAY = "thursday"
    FRIDAY = "friday"
    SATURDAY = "saturday"
    SUNDAY = "sunday"


class ExtendedParsedQuery(DomainModel):
    """The AI search pipeline's structured filter shape.

    Wraps everything the Phase 7 ParsedQuery had, plus:
    - intent              — shapes the answer prompt
    - day_of_week_in      — set of weekday names (e.g. {"friday"})
    - cause_keywords      — phrases like "engine failure", "bird strike"
    - sort_by             — deterministic ordering
    - limit               — explicit result cap

    The LLM produces this object (as JSON) in Stage 2 of the pipeline.
    Every field is optional — best-effort extraction.
    """

    # ── Intent (Stage 1 result, sometimes overridden by Stage 2) ─────────
    intent: SearchIntent = SearchIntent.LIST

    # ── Facet filters (existing Phase 7 ParsedQuery superset) ────────────
    operator: str | None = None
    aircraft_type: str | None = None
    country: str | None = None
    event_date_from: date | None = None
    event_date_to: date | None = None
    fatalities_min: int | None = None
    fatalities_max: int | None = None
    fatal_only: bool = False
    non_fatal_only: bool = False

    # ── HFACS / SHELO (existing) ─────────────────────────────────────────
    hfacs_category_codes: list[str] = Field(default_factory=list)
    shelo_factor_classes: list[str] = Field(default_factory=list)

    # ── NEW: day-of-week filter (post-SQL Python filter) ─────────────────
    day_of_week_in: list[DayOfWeek] = Field(default_factory=list)

    # ── NEW: cause keywords (matched against narrative_markdown) ─────────
    cause_keywords: list[str] = Field(default_factory=list)

    # ── NEW: deterministic sort + limit ──────────────────────────────────
    sort_by: SortBy = SortBy.DATE_DESC
    limit: int = Field(default=20, ge=1, le=50)

    # ── Bookkeeping ──────────────────────────────────────────────────────
    free_text_remainder: str = ""
    extractor_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    fallback_to_deterministic: bool = False


__all__ = [
    "DayOfWeek",
    "ExtendedParsedQuery",
    "SearchIntent",
    "SortBy",
]
