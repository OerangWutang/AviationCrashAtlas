"""AI search router — exposes the 8-stage pipeline.

File location: src/atlas/presentation/api/routers/ai_search.py

Replaces the earlier minimal ai_search router. Single endpoint:
    POST /api/v1/search/ai

The endpoint is reader-role gated. Public means "published Atlas
record" — not anonymous internet access. The BFF proxies to here
from an authenticated browser session.

Test:
    curl -s -X POST \\
      -H "X-API-Key: $ATLAS_KEY" \\
      -H "Content-Type: application/json" \\
      -d '{"query": "engine failures on a friday between 1998 and 2022"}' \\
      http://127.0.0.1:8000/api/v1/search/ai | python3 -m json.tool
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from atlas.application.dto import CurrentUser
from atlas.application.unit_of_work import UnitOfWork
from atlas.application.use_cases.ai_search_pipeline import (
    AiSearchPipeline,
    AiSearchPipelineResult,
)
from atlas.domain.enums import Role
from atlas.presentation.api.dependencies import get_uow, require_role

router = APIRouter(prefix="/search/ai", tags=["ai-search"])

_READERS = (Role.ADMIN, Role.REVIEWER, Role.ANALYST)


# ── Request ───────────────────────────────────────────────────────────────────


class AiSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        min_length=1,
        max_length=500,
        description="Free-text question about aviation accidents.",
    )
    limit: int | None = Field(
        default=None,
        ge=1,
        le=50,
        description="Optional override for the LLM-extracted limit.",
    )


# ── Response sub-models ───────────────────────────────────────────────────────


class CitationVerificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    valid_slugs: list[str]
    stripped_slugs: list[str]
    all_valid: bool


class EnrichedClaimResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field_name: str
    field_value: Any
    source_name: str
    source_reliability_tier: int
    claim_type: str
    is_winning: bool


class EnrichedAttributionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    name: str
    confidence: float
    rationale: str | None


class EnrichedConflictResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field_name: str
    status: str
    candidate_count: int


class EnrichedHitResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str
    title: str
    short_summary: str | None
    operator: str | None
    aircraft_type: str | None
    country: str | None
    event_date: str | None
    day_of_week: str | None
    fatalities_total: int | None
    confidence_band: str
    claims: list[EnrichedClaimResponse]
    attributions: list[EnrichedAttributionResponse]
    open_conflicts: list[EnrichedConflictResponse]


class ExtendedQueryEcho(BaseModel):
    """Echo of the filters the LLM extracted from the query.

    Exposed so the UI can show "we interpreted your query as: …"
    and the user can spot extraction errors before trusting the answer.
    """

    model_config = ConfigDict(extra="forbid")
    intent: str
    operator: str | None
    aircraft_type: str | None
    country: str | None
    event_date_from: str | None
    event_date_to: str | None
    fatalities_min: int | None
    fatalities_max: int | None
    fatal_only: bool
    non_fatal_only: bool
    day_of_week_in: list[str]
    cause_keywords: list[str]
    shelo_factor_classes: list[str]
    hfacs_category_codes: list[str]
    sort_by: str
    limit: int
    extractor_confidence: float
    fallback_to_deterministic: bool


# ── Top-level response ────────────────────────────────────────────────────────


class AiSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    intent: str = Field(description="One of: list, summarize, compare, rank, analyze.")
    extracted_filters: ExtendedQueryEcho
    answer: str = Field(
        description=(
            "Grounded natural-language answer with inline [slug] citations. "
            "Every citation is verified against the retrieved hit set."
        )
    )
    hit_count: int
    citations: CitationVerificationResponse
    hits: list[EnrichedHitResponse] = Field(
        description="Records that fed the answer. Inspect to verify."
    )
    warnings: list[str] = Field(
        default_factory=list,
        description=(
            "Non-fatal issues: LLM degraded mode, stripped citations, etc. "
            "Empty when everything worked normally."
        ),
    )
    llm_fully_available: bool
    disclaimer: str = Field(
        default=(
            "Atlas organises and reconciles source evidence. "
            "This answer is derived from ingested evidence only. "
            "It does not establish fault, liability, or legal causation. "
            "Not legal advice."
        )
    )


# ── Result → response mapping ─────────────────────────────────────────────────


def _to_response(result: AiSearchPipelineResult) -> AiSearchResponse:
    eq = result.extended_query
    return AiSearchResponse(
        query=result.query,
        intent=result.intent.value,
        extracted_filters=ExtendedQueryEcho(
            intent=eq.intent.value,
            operator=eq.operator,
            aircraft_type=eq.aircraft_type,
            country=eq.country,
            event_date_from=eq.event_date_from.isoformat() if eq.event_date_from else None,
            event_date_to=eq.event_date_to.isoformat() if eq.event_date_to else None,
            fatalities_min=eq.fatalities_min,
            fatalities_max=eq.fatalities_max,
            fatal_only=eq.fatal_only,
            non_fatal_only=eq.non_fatal_only,
            day_of_week_in=[d.value for d in eq.day_of_week_in],
            cause_keywords=eq.cause_keywords,
            shelo_factor_classes=eq.shelo_factor_classes,
            hfacs_category_codes=eq.hfacs_category_codes,
            sort_by=eq.sort_by.value,
            limit=eq.limit,
            extractor_confidence=eq.extractor_confidence,
            fallback_to_deterministic=eq.fallback_to_deterministic,
        ),
        answer=result.answer,
        hit_count=result.hit_count,
        citations=CitationVerificationResponse(
            valid_slugs=result.citations.valid_slugs,
            stripped_slugs=result.citations.stripped_slugs,
            all_valid=result.citations.all_valid,
        ),
        hits=[
            EnrichedHitResponse(
                slug=h.slug,
                title=h.title,
                short_summary=h.short_summary,
                operator=h.operator,
                aircraft_type=h.aircraft_type,
                country=h.country,
                event_date=h.event_date,
                day_of_week=h.day_of_week,
                fatalities_total=h.fatalities_total,
                confidence_band=h.confidence_band,
                claims=[
                    EnrichedClaimResponse(
                        field_name=c.field_name,
                        field_value=c.field_value,
                        source_name=c.source_name,
                        source_reliability_tier=c.source_reliability_tier,
                        claim_type=c.claim_type,
                        is_winning=c.is_winning,
                    )
                    for c in h.claims
                ],
                attributions=[
                    EnrichedAttributionResponse(
                        code=a.code,
                        name=a.name,
                        confidence=a.confidence,
                        rationale=a.rationale,
                    )
                    for a in h.attributions
                ],
                open_conflicts=[
                    EnrichedConflictResponse(
                        field_name=c.field_name,
                        status=c.status,
                        candidate_count=c.candidate_count,
                    )
                    for c in h.open_conflicts
                ],
            )
            for h in result.hits
        ],
        warnings=result.warnings,
        llm_fully_available=result.llm_fully_available,
    )


# ── Endpoint ──────────────────────────────────────────────────────────────────


@router.post("", response_model=AiSearchResponse)
async def ai_search(
    request: AiSearchRequest,
    uow: UnitOfWork = Depends(get_uow, scope="function"),
    _user: CurrentUser = Depends(require_role(*_READERS)),
) -> AiSearchResponse:
    """Run the full AI search pipeline against a free-text query.

    Pipeline (see AI_SEARCH_ARCHITECTURE.md):
        1. Intent classifier (DeepSeek)
        2. Filter extractor (DeepSeek, JSON mode)
        3. Deterministic SQL search (existing Atlas search)
        4. Evidence enrichment (claims, attributions, conflicts)
        5. Intent-specific prompt
        6. Grounded LLM answer (DeepSeek)
        7. Citation verification (deterministic)
        8. Telemetry

    The LLM never decides which records match or how to rank them.
    It only converts prose ↔ JSON ↔ prose with the database in the loop.
    Hallucinated citations are stripped before the response leaves the server.
    """
    pipeline = AiSearchPipeline(uow)
    result = await pipeline.run(request.query, limit_override=request.limit)
    return _to_response(result)
