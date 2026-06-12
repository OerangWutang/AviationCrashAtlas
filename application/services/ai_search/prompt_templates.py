"""Stage 5 — Intent-specific prompt templates.

File location: src/atlas/application/services/ai_search/prompt_templates.py

Each intent (list, summarize, compare, rank, analyze) gets a
distinct prompt shape. All prompts share:
- Grounding rules (only cite slugs in the evidence block)
- Citation format ([slug])
- Disclaimer about evidence-support, not legal advice
- The same evidence block

They differ in the answer shape they request.
"""

from __future__ import annotations

from atlas.application.services.ai_search.evidence_enricher import EnrichedHit
from atlas.domain.nl_search.extended_query import (
    ExtendedParsedQuery,
    SearchIntent,
)

# ── Shared grounding header ───────────────────────────────────────────────────


_GROUNDING_RULES = """\
You are an aviation safety analyst assistant for Atlas, a legal-grade \
evidence management platform. Answer the user's question using ONLY \
the evidence below.

STRICT RULES — violating any rule makes your answer inadmissible:
1. Only reference accidents listed in the EVIDENCE section.
2. Every factual claim must be followed by its slug in square \
brackets, e.g. [colgan-air-3407].
3. Never invent accident details, dates, locations, or causes.
4. Never state a cause as definitive unless the evidence says \
"probable cause" or shows an HFACS attribution.
5. If a field is marked DISPUTED, say so explicitly and do not \
treat it as a settled fact.
6. If the evidence does not answer the question, say so.
7. Do not refer to yourself as an AI or mention the model name.
8. End with: "Source: Atlas evidence database — {hit_count} records matched."
"""


# ── Intent-specific instructions ──────────────────────────────────────────────


_LIST_INSTRUCTIONS = """\
ANSWER SHAPE — list:
- Brief 1-2 sentence introduction stating how many accidents matched.
- Numbered list, one entry per accident.
- Each entry: date, operator, aircraft, fatalities, location, brief \
description, [slug].
- Sort by date descending unless another order is implied.
- If more than 10 accidents matched, list the top 10 and note the total.
"""

_SUMMARIZE_INSTRUCTIONS = """\
ANSWER SHAPE — summarize:
- One paragraph synthesising patterns across the matched accidents.
- Cite the most representative 3-5 accidents inline with [slug] markers.
- Include key statistics: total fatalities across the set, date range, \
common aircraft types, common operators.
- End with one sentence identifying the dominant causal pattern if any.
"""

_COMPARE_INSTRUCTIONS = """\
ANSWER SHAPE — compare:
- A side-by-side comparison of the most relevant accidents.
- Use a markdown table with columns: Accident, Date, Aircraft, \
Operator, Fatalities, Primary cause.
- Below the table, 2-3 sentences highlighting the most notable \
similarities and differences.
- Cite every row with [slug].
"""

_RANK_INSTRUCTIONS = """\
ANSWER SHAPE — rank:
- Ordered list from #1 (highest by the implied criterion) downward.
- Each entry: rank number, accident name, the metric value, [slug].
- Default ranking: most fatalities first.
- Include 1-2 sentences after the list explaining what the ranking \
reveals about the corpus.
"""

_ANALYZE_INSTRUCTIONS = """\
ANSWER SHAPE — analyze:
- Lead with the dominant causal pattern across the matched accidents.
- Cite HFACS attributions and SHELO factors from the evidence directly.
- Cite at least 3 specific accidents that illustrate the pattern, with [slug].
- Do NOT invent causal relationships. Only describe patterns that \
multiple accidents in the evidence actually demonstrate.
- End with one sentence on what the evidence does NOT establish.
"""


_INTENT_INSTRUCTIONS = {
    SearchIntent.LIST: _LIST_INSTRUCTIONS,
    SearchIntent.SUMMARIZE: _SUMMARIZE_INSTRUCTIONS,
    SearchIntent.COMPARE: _COMPARE_INSTRUCTIONS,
    SearchIntent.RANK: _RANK_INSTRUCTIONS,
    SearchIntent.ANALYZE: _ANALYZE_INSTRUCTIONS,
}


# ── Evidence block builder ────────────────────────────────────────────────────


def _format_evidence_block(hits: list[EnrichedHit]) -> str:
    """Format the enriched hits into a structured evidence block.

    Compact format — every record fits in ~150 tokens so we can fit
    20 accidents in ~3000 tokens of evidence.
    """
    if not hits:
        return "EVIDENCE:\n(No matching records found in the database.)\n"

    lines = ["EVIDENCE:"]
    for i, hit in enumerate(hits, 1):
        lines.append(f"\n--- Record {i} ---")
        lines.append(f"Slug: {hit.slug}")
        lines.append(f"Title: {hit.title}")
        if hit.event_date:
            day = f" ({hit.day_of_week.capitalize()})" if hit.day_of_week else ""
            lines.append(f"Date: {hit.event_date}{day}")
        if hit.operator:
            lines.append(f"Operator: {hit.operator}")
        if hit.aircraft_type:
            lines.append(f"Aircraft: {hit.aircraft_type}")
        if hit.country:
            lines.append(f"Country: {hit.country}")
        if hit.fatalities_total is not None:
            lines.append(f"Fatalities: {hit.fatalities_total}")
        lines.append(f"Evidence quality: {hit.confidence_band}")
        if hit.short_summary:
            lines.append(f"Summary: {hit.short_summary}")

        # Top causal attributions
        if hit.attributions:
            lines.append("Causal factors (HFACS):")
            for a in hit.attributions:
                conf_pct = int(a.confidence * 100)
                rationale = f" — {a.rationale}" if a.rationale else ""
                lines.append(f"  - {a.code}: {a.name} ({conf_pct}% confidence){rationale}")

        # Top supporting claims
        if hit.claims:
            lines.append("Key supporting facts:")
            for c in hit.claims:
                lines.append(
                    f"  - {c.field_name}: {c.field_value} "
                    f"[source: {c.source_name}, tier {c.source_reliability_tier}]"
                )

        # Active conflicts
        if hit.open_conflicts:
            disputed_fields = ", ".join(c.field_name for c in hit.open_conflicts)
            lines.append(f"⚠️ DISPUTED FIELDS (do not assert as fact): {disputed_fields}")

    return "\n".join(lines)


# ── Filter recap ──────────────────────────────────────────────────────────────


def _format_filter_recap(query: ExtendedParsedQuery) -> str:
    """Tell the LLM exactly which filters were applied.

    This helps when the evidence list looks unexpectedly empty —
    the LLM can explain "no Boeing 747 accidents on a Friday in
    2020 were found" instead of just "no results".
    """
    parts: list[str] = []
    if query.event_date_from and query.event_date_to:
        parts.append(f"date range {query.event_date_from} to {query.event_date_to}")
    elif query.event_date_from:
        parts.append(f"after {query.event_date_from}")
    elif query.event_date_to:
        parts.append(f"before {query.event_date_to}")

    if query.day_of_week_in:
        parts.append("day of week in: " + ", ".join(d.value for d in query.day_of_week_in))
    if query.aircraft_type:
        parts.append(f"aircraft = {query.aircraft_type}")
    if query.operator:
        parts.append(f"operator = {query.operator}")
    if query.country:
        parts.append(f"country = {query.country}")
    if query.fatal_only:
        parts.append("fatal accidents only")
    if query.fatalities_min is not None:
        parts.append(f"fatalities >= {query.fatalities_min}")
    if query.fatalities_max is not None:
        parts.append(f"fatalities <= {query.fatalities_max}")
    if query.cause_keywords:
        parts.append("cause keywords: " + ", ".join(query.cause_keywords))
    if query.shelo_factor_classes:
        parts.append("SHELO factors: " + ", ".join(query.shelo_factor_classes))
    if query.hfacs_category_codes:
        parts.append("HFACS categories: " + ", ".join(query.hfacs_category_codes))

    if not parts:
        return "Filters applied: (none — full corpus search)"
    return "Filters applied: " + "; ".join(parts) + "."


# ── Public builder ────────────────────────────────────────────────────────────


def build_answer_prompt(
    query: str,
    extended_query: ExtendedParsedQuery,
    hits: list[EnrichedHit],
) -> str:
    """Build the full prompt for the Stage 6 LLM answer call."""
    intent_block = _INTENT_INSTRUCTIONS.get(
        extended_query.intent, _LIST_INSTRUCTIONS
    )
    grounding = _GROUNDING_RULES.format(hit_count=len(hits))
    evidence = _format_evidence_block(hits)
    filter_recap = _format_filter_recap(extended_query)

    return (
        grounding
        + "\n"
        + intent_block
        + "\n"
        + filter_recap
        + "\n\n"
        + evidence
        + f"\n\nQUESTION: {query}\n\nANSWER:"
    )


__all__ = ["build_answer_prompt"]
