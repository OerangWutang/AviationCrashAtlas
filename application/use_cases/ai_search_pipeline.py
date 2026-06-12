"""Atlas AI Search Pipeline — 8-stage orchestrator.

File location: src/atlas/application/use_cases/ai_search_pipeline.py

Runs:
  1. Intent classifier (DeepSeek)
  2. Filter extractor (DeepSeek, JSON mode)
  3. Deterministic SQL search (existing Atlas search)
  4. Evidence enrichment (claims, attributions, conflicts)
  5. Intent-specific prompt construction
  6. Grounded LLM answer (DeepSeek)
  7. Citation verification (deterministic)
  8. Telemetry (NlQueryLog, MeteringService)

The LLM never decides which accidents match. The LLM only:
- Extracts JSON filters from prose (Stage 2)
- Generates prose from filtered evidence (Stage 6)

Everything else is deterministic SQL/Python.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from atlas.application.services.ai_search.evidence_enricher import (
    EnrichedHit,
    enrich_hits,
)
from atlas.application.services.ai_search.filter_extractor import extract_filters
from atlas.application.services.ai_search.intent_classifier import classify_intent
from atlas.application.services.ai_search.prompt_templates import build_answer_prompt
from atlas.application.services.metering import MeteringService
from atlas.application.services.nl_query_parser import (
    hour_bucket_for,
    query_hash_for,
)
from atlas.application.unit_of_work import UnitOfWork
from atlas.domain.metering.entities import MetricKind
from atlas.domain.nl_search.entities import NlQueryLog
from atlas.domain.nl_search.extended_query import (
    ExtendedParsedQuery,
    SearchIntent,
    SortBy,
)
from atlas.domain.search.entities import SearchHit, SearchQuery
from atlas.domain.utils import utc_now
from atlas.infrastructure.llm.deepseek_client import (
    DeepSeekError,
    call_deepseek,
)

logger = logging.getLogger(__name__)

# ── Citation extraction regex ─────────────────────────────────────────────────

_CITATION_RE = re.compile(r"\[([a-z0-9][a-z0-9\-]{2,79})\]")


# ── Result type ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CitationVerification:
    valid_slugs: list[str]
    stripped_slugs: list[str]
    all_valid: bool


@dataclass(frozen=True)
class TokenUsage:
    """Cumulative LLM token usage across all stages of one query."""

    intent_call: int = 0
    extractor_call: int = 0
    answer_call: int = 0

    @property
    def total(self) -> int:
        return self.intent_call + self.extractor_call + self.answer_call


@dataclass
class AiSearchPipelineResult:
    """The full pipeline output returned to the API layer."""

    query: str
    intent: SearchIntent
    extended_query: ExtendedParsedQuery
    hits: list[EnrichedHit]
    raw_answer: str
    answer: str
    citations: CitationVerification
    hit_count: int
    log_id: UUID
    token_usage: TokenUsage
    llm_fully_available: bool
    warnings: list[str]


# ── Main pipeline ─────────────────────────────────────────────────────────────


class AiSearchPipeline:
    """The full 8-stage pipeline.

    One call: ``await AiSearchPipeline(uow).run(query)`` runs the
    whole thing and returns a single ``AiSearchPipelineResult``.

    The pipeline is robust to LLM failure at every stage — if
    DeepSeek is unavailable, the deterministic parser handles
    extraction and a structured fallback answer is produced.
    """

    def __init__(self, uow: UnitOfWork):
        self._uow = uow

    async def run(self, query: str, *, limit_override: int | None = None) -> AiSearchPipelineResult:
        warnings: list[str] = []
        token_usage = TokenUsage()

        # ── Stage 1 — Intent classification ──────────────────────────────────
        intent = await classify_intent(query)

        # ── Stage 2 — Filter extraction ──────────────────────────────────────
        hfacs_categories = await self._uow.hfacs_categories.list_all()
        extended = await extract_filters(query, intent, hfacs_categories)

        if extended.fallback_to_deterministic:
            warnings.append(
                "AI filter extraction unavailable — using deterministic parser. "
                "Day-of-week and cause-keyword filters may be missing."
            )

        if limit_override:
            extended = extended.model_copy(update={"limit": min(limit_override, 50)})

        # ── Stage 3 — Deterministic SQL search ───────────────────────────────
        hits = await self._search(extended)

        # ── Stage 3b — Post-filter (day-of-week, cause keywords) ─────────────
        hits = _post_filter(hits, extended)

        # ── Stage 3c — Deterministic sort ────────────────────────────────────
        hits = _sort_hits(hits, extended.sort_by)

        # Apply final limit AFTER post-filtering and sorting
        hits = hits[: extended.limit]

        # ── Stage 4 — Evidence enrichment ────────────────────────────────────
        enriched = await enrich_hits(self._uow, hits)

        # ── Stage 5 — Prompt construction ────────────────────────────────────
        prompt = build_answer_prompt(query, extended, enriched)

        # ── Stage 6 — Grounded answer ────────────────────────────────────────
        raw_answer, llm_ok = await self._generate_answer(prompt)
        if not llm_ok:
            warnings.append("AI answer generation unavailable — showing structured fallback.")
            raw_answer = _fallback_answer(query, extended, enriched)

        # ── Stage 7 — Citation verification ──────────────────────────────────
        valid_slugs = {h.slug for h in enriched}
        verification = _verify_citations(raw_answer, valid_slugs)
        clean_answer = _strip_hallucinations(raw_answer, verification.stripped_slugs)

        if verification.stripped_slugs:
            warnings.append(
                f"{len(verification.stripped_slugs)} hallucinated citation(s) stripped: "
                + ", ".join(verification.stripped_slugs)
            )

        # ── Stage 8 — Telemetry ──────────────────────────────────────────────
        log_id = await self._log_query(query, extended, len(enriched))

        return AiSearchPipelineResult(
            query=query,
            intent=intent,
            extended_query=extended,
            hits=enriched,
            raw_answer=raw_answer,
            answer=clean_answer,
            citations=verification,
            hit_count=len(enriched),
            log_id=log_id,
            token_usage=token_usage,
            llm_fully_available=not extended.fallback_to_deterministic and llm_ok,
            warnings=warnings,
        )

    # ── Stage 3 helpers ──────────────────────────────────────────────────────

    async def _search(self, extended: ExtendedParsedQuery) -> list[SearchHit]:
        """Dispatch to the existing search repository.

        Over-fetches by 3x so post-filtering on day-of-week and
        cause keywords has enough material to filter from. The
        final limit is enforced after sort.
        """
        fatalities_min = extended.fatalities_min
        fatalities_max = extended.fatalities_max
        if extended.fatal_only and fatalities_min is None:
            fatalities_min = 1
        if extended.non_fatal_only and fatalities_max is None:
            fatalities_max = 0

        # Use cause_keywords + free_text_remainder as the FTS query
        fts_terms: list[str] = []
        if extended.cause_keywords:
            fts_terms.extend(extended.cause_keywords)
        if extended.free_text_remainder.strip():
            fts_terms.append(extended.free_text_remainder.strip())
        fts_q = " ".join(fts_terms) or None

        # Over-fetch so post-filters have material; cap at 60.
        over_fetch_limit = min(extended.limit * 3, 60)

        sq = SearchQuery(
            q=fts_q,
            operator=extended.operator,
            aircraft_type=extended.aircraft_type,
            country=extended.country,
            event_date_from=extended.event_date_from,
            event_date_to=extended.event_date_to,
            fatalities_min=fatalities_min,
            fatalities_max=fatalities_max,
            limit=over_fetch_limit,
        )
        result = await self._uow.search.search(sq)
        items = result.items

        # Intersect with HFACS categories if any
        if extended.hfacs_category_codes:
            items = await self._intersect_hfacs(items, extended.hfacs_category_codes)

        return items

    async def _intersect_hfacs(
        self,
        items: list[SearchHit],
        codes: list[str],
    ) -> list[SearchHit]:
        cats = await self._uow.hfacs_categories.list_all()
        wanted = {c.id for c in cats if c.code in codes}
        kept: list[SearchHit] = []
        for hit in items:
            page = await self._uow.public_event_pages.get_by_id(hit.page_id)
            if page is None:
                continue
            attrs = await self._uow.event_hfacs_attributions.list_for_event(page.event_id)
            if any(a.category_id in wanted for a in attrs):
                kept.append(hit)
        return kept

    # ── Stage 6 helper ───────────────────────────────────────────────────────

    async def _generate_answer(self, prompt: str) -> tuple[str, bool]:
        """Call DeepSeek for the answer. Returns (text, success)."""
        try:
            response = await call_deepseek(
                prompt=prompt,
                json_mode=False,
                temperature=0.1,
                max_tokens=1200,
            )
            return response.content, True
        except DeepSeekError as exc:
            logger.warning("Answer generation failed: %s", exc)
            return "", False

    # ── Stage 8 helper ───────────────────────────────────────────────────────

    async def _log_query(
        self,
        query: str,
        extended: ExtendedParsedQuery,
        result_count: int,
    ) -> UUID:
        log_entry = NlQueryLog(
            raw_query=query,
            query_hash=query_hash_for(query),
            parsed_filters=extended.model_dump(mode="json"),
            result_count=result_count,
            parser_confidence=extended.extractor_confidence,
            hour_bucket=hour_bucket_for(utc_now()),
        )
        await self._uow.nl_query_log.add(log_entry)
        await MeteringService(self._uow).record(
            metric_kind=MetricKind.NL_QUERY_EXECUTED,
            tenant_id=None,
            user_id=None,
            resource_id=log_entry.id,
        )
        await self._uow.commit()
        return log_entry.id


# ── Stage 3b: post-filter on day-of-week and cause keywords ──────────────────


def _post_filter(hits: list[SearchHit], extended: ExtendedParsedQuery) -> list[SearchHit]:
    """Apply filters that aren't expressible as SQL facets."""
    kept = hits

    # Day-of-week filter (no DB index — cheap Python filter)
    if extended.day_of_week_in:
        allowed = {d.value for d in extended.day_of_week_in}
        kept = [
            h for h in kept
            if h.event_date is not None
            and h.event_date.strftime("%A").lower() in allowed
        ]

    # Cause-keyword filter is best-effort via FTS upstream; nothing to add here
    # (the SQL stage already used cause_keywords in the FTS query).

    return kept


# ── Stage 3c: deterministic sort ──────────────────────────────────────────────


def _sort_hits(hits: list[SearchHit], sort_by: SortBy) -> list[SearchHit]:
    """Sort the result set by the chosen criterion."""
    if sort_by == SortBy.DATE_DESC:
        return sorted(
            hits,
            key=lambda h: (h.event_date or date.min),
            reverse=True,
        )
    if sort_by == SortBy.DATE_ASC:
        return sorted(hits, key=lambda h: (h.event_date or date.max))
    if sort_by == SortBy.FATALITIES_DESC:
        return sorted(
            hits,
            key=lambda h: (h.fatalities_total or 0),
            reverse=True,
        )
    if sort_by == SortBy.FATALITIES_ASC:
        return sorted(hits, key=lambda h: (h.fatalities_total or 0))
    # RELEVANCE — preserve SQL rank order
    return hits


# ── Stage 7: citation verification ───────────────────────────────────────────


def _verify_citations(answer: str, valid_slugs: set[str]) -> CitationVerification:
    cited = _CITATION_RE.findall(answer)
    valid: list[str] = []
    stripped: list[str] = []
    for slug in cited:
        if slug in valid_slugs:
            if slug not in valid:
                valid.append(slug)
        else:
            if slug not in stripped:
                stripped.append(slug)
            logger.warning("AI search: stripping hallucinated citation %r", slug)
    return CitationVerification(
        valid_slugs=valid,
        stripped_slugs=stripped,
        all_valid=len(stripped) == 0,
    )


def _strip_hallucinations(answer: str, stripped_slugs: list[str]) -> str:
    out = answer
    for slug in stripped_slugs:
        out = out.replace(f"[{slug}]", "[citation removed]")
    return out


# ── Fallback answer (no LLM) ──────────────────────────────────────────────────


def _fallback_answer(
    query: str,
    extended: ExtendedParsedQuery,
    hits: list[EnrichedHit],
) -> str:
    """Structured fallback when DeepSeek is unavailable.

    Mechanically derived from the retrieved hits — no model, no
    hallucination risk, still useful.
    """
    if not hits:
        filters = _filter_summary(extended)
        return (
            f"No records matching your query were found in the Atlas database. "
            f"{filters} "
            "The corpus may not yet contain accidents matching these criteria, "
            "or the evidence may not have been ingested."
        )

    lines = [f"Found {len(hits)} record(s) matching your query:\n"]
    for h in hits:
        meta_parts: list[str] = []
        if h.event_date:
            meta_parts.append(h.event_date)
        if h.operator:
            meta_parts.append(h.operator)
        if h.aircraft_type:
            meta_parts.append(h.aircraft_type)
        if h.fatalities_total is not None:
            meta_parts.append(f"{h.fatalities_total} fatalities")
        meta = " · ".join(meta_parts)
        lines.append(f"- **{h.title}** [{h.slug}]")
        if meta:
            lines.append(f"  {meta}")
        if h.short_summary:
            lines.append(f"  {h.short_summary}")

    lines.append(f"\nSource: Atlas evidence database — {len(hits)} records matched.")
    return "\n".join(lines)


def _filter_summary(extended: ExtendedParsedQuery) -> str:
    parts: list[str] = []
    if extended.event_date_from and extended.event_date_to:
        parts.append(f"date range {extended.event_date_from} to {extended.event_date_to}")
    if extended.day_of_week_in:
        parts.append("day of week: " + ", ".join(d.value for d in extended.day_of_week_in))
    if extended.cause_keywords:
        parts.append("cause: " + ", ".join(extended.cause_keywords))
    if extended.aircraft_type:
        parts.append(f"aircraft: {extended.aircraft_type}")
    if not parts:
        return "(no specific filters applied)"
    return "Filters: " + "; ".join(parts) + "."


__all__ = [
    "AiSearchPipeline",
    "AiSearchPipelineResult",
    "CitationVerification",
    "TokenUsage",
]
