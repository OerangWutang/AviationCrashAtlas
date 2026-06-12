"""Stage 1 — Intent classifier.

File location: src/atlas/application/services/ai_search/intent_classifier.py

One DeepSeek call to classify the user's intent into one of five
shapes. The intent decides which prompt template Stage 6 uses.

Output is constrained JSON so it's deterministic and parseable.
On any failure, the default intent is LIST — the safest answer
shape since it just enumerates the matched accidents.
"""

from __future__ import annotations

import logging

from atlas.domain.nl_search.extended_query import SearchIntent
from atlas.infrastructure.llm.deepseek_client import (
    DeepSeekError,
    call_deepseek,
    parse_json_or_none,
)

logger = logging.getLogger(__name__)


_INTENT_PROMPT = """\
You are classifying an aviation accident search query into ONE of \
exactly five intent categories. Return strict JSON only.

INTENT CATEGORIES:
- list       — user wants an enumeration of matching accidents
- summarize  — user wants narrative synthesis across the result set
- compare    — user wants side-by-side comparison of specific accidents
- rank       — user wants accidents ordered by some criterion
- analyze    — user wants causal pattern analysis or root-cause discussion

CLASSIFICATION RULES:
- "list all accidents…" → list
- "show me…" / "find…" → list
- "summarise/summarize…" / "what happened in…" → summarize
- "compare X and Y" / "differences between" → compare
- "deadliest" / "worst" / "most fatal" / "rank by" → rank
- "why did…" / "what caused…" / "common factors" / "patterns" → analyze

Return JSON in this exact shape, no commentary:
{"intent": "<one of: list, summarize, compare, rank, analyze>"}

QUERY: %s

JSON:"""


async def classify_intent(query: str) -> SearchIntent:
    """Classify the user's query intent. Returns LIST on any failure."""
    try:
        response = await call_deepseek(
            prompt=_INTENT_PROMPT % query.strip(),
            json_mode=True,
            temperature=0.0,
            max_tokens=50,
        )
    except DeepSeekError as exc:
        logger.warning("Intent classifier: DeepSeek call failed — defaulting to LIST. error=%s", exc)
        return SearchIntent.LIST

    parsed = parse_json_or_none(response.content)
    if not parsed:
        logger.warning("Intent classifier: invalid JSON, defaulting to LIST. raw=%r", response.content[:200])
        return SearchIntent.LIST

    raw_intent = str(parsed.get("intent", "")).strip().lower()
    try:
        return SearchIntent(raw_intent)
    except ValueError:
        logger.warning("Intent classifier: unknown intent %r, defaulting to LIST", raw_intent)
        return SearchIntent.LIST


__all__ = ["classify_intent"]
