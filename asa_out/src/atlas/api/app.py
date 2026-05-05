"""
FastAPI application.

Fixes from review:
- API now returns location_lat / location_lon as plain floats so the map
  can use real coordinates (not hardcoded mock ones)
- confidence_breakdown is returned in detail and provenance responses
- Date-sorted accident lists support cursor/keyset pagination; non-date sorts keep offset pagination
- /api/v1/accidents/{id}/provenance returns winning claims correctly
  (is_winning is now set by ProjectionService before API reads it)
"""
from __future__ import annotations

import asyncio
import base64
import csv
import hashlib
import io
import json
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Literal

import structlog
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from pydantic import BaseModel  # noqa: F401  (re-exported for legacy importers)
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from sqlalchemy import and_, case, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from atlas.api.auth import OperatorContext, require_admin, require_reviewer
from atlas.api.schemas import (
    AccidentDetail,
    AccidentProvenance,
    AccidentSummary,
    AnalyticsSummary,
    ApiKeyCreateIn,
    ApiKeyCreateOut,
    ApiKeyOut,
    ArchiveManifestOut,
    AuditLogItemOut,  # noqa: F401  (re-exported for schemas.__all__ contract)
    ClaimOut,
    ClaimTypeValue,  # noqa: F401  (re-exported)
    ConfidenceOut,
    ConflictOut,
    ConflictQueueItem,
    ConflictResolveIn,
    CursorPaginatedAccidents,  # noqa: F401  (re-exported)
    DataQualityIssueOut,
    DataQualityResolveIn,
    DuplicateCandidateOut,
    DuplicateDecisionIn,
    EventRevisionOut,
    IngestionRunOut,
    MapAccident,
    MapCluster,
    PaginatedAccidents,
    ProjectionExplanationOut,
    ProvenanceTruncationOut,
    ResolutionType,  # noqa: F401  (re-exported)
    SourceDocumentOut,
    SourceDocumentReviewIn,
    SourceOut,
    SourceStatusOut,
)
from atlas.claims.projection import ProjectionService
from atlas.claims.resolution import (
    ConflictAlreadyResolvedError,
    ConflictNotFoundError,
    ConflictResolutionService,
    ConflictValidationError,
    ProjectionRebuildError,
)
from atlas.confidence.engine import confidence_label
from atlas.config import get_settings
from atlas.db.engine import get_db, get_read_db
from atlas.models import claim_value as cv
from atlas.models.orm import (
    AccidentEvent,
    AccidentRecord,
    ApiKey,
    ArchiveManifest,
    Claim,
    ClaimConflict,
    ClaimHistory,
    ClaimSourceDocument,
    ClaimType,
    DataQualityIssue,
    DuplicateCandidateReview,
    DuplicateMergeOperation,
    EventRevision,
    IngestionRun,
    Source,
    SourceDocument,
)
from atlas.timeline.router import router as timeline_router
from atlas.weather.router import router as weather_router
from atlas.system_failures.router import router as system_failures_router
from atlas.analytics.router import router as analytics_router
from atlas.flight_path.router import router as flight_path_router

log = structlog.get_logger(__name__)
settings = get_settings()


# ── Analytics TTL cache ────────────────────────────────────────────────────────
# The analytics summary runs 5 aggregation queries over the full dataset on
# every request.  Data changes only at ingestion time, so caching for a short
# TTL (default 60s, configurable) avoids redundant full-table work.
# asyncio.Lock prevents a thundering herd when the cache expires under load.

class _AnalyticsCache:
    def __init__(self) -> None:
        self._value: AnalyticsSummary | None = None
        self._expires_at: datetime = datetime.min.replace(tzinfo=UTC)
        self._lock: asyncio.Lock = asyncio.Lock()

    def is_fresh(self, s: Any) -> bool:
        return (
            self._value is not None
            and s.analytics_cache_ttl_s > 0
            and datetime.now(tz=UTC) < self._expires_at
        )

    def store(self, value: AnalyticsSummary, s: Any) -> None:
        self._value = value
        if s.analytics_cache_ttl_s > 0:
            from datetime import timedelta
            self._expires_at = datetime.now(tz=UTC) + timedelta(
                seconds=s.analytics_cache_ttl_s
            )

    @property
    def value(self) -> AnalyticsSummary | None:
        return self._value

    def invalidate(self) -> None:
        self._value = None
        self._expires_at = datetime.min.replace(tzinfo=UTC)


_analytics_cache = _AnalyticsCache()


# ── Prometheus metrics ─────────────────────────────────────────────────────────

_map_truncation_total = Counter(
    "atlas_map_truncation_total",
    "Number of times the map endpoint returned a truncated response",
)
_conflict_resolutions_total = Counter(
    "atlas_conflict_resolutions_total",
    "Number of conflict resolutions",
    ["resolution_type"],
)
_projection_rebuilds_total = Counter(
    "atlas_projection_rebuilds_total",
    "Number of projection rebuilds triggered via API",
    ["outcome"],
)
_provenance_truncation_total = Counter(
    "atlas_provenance_truncation_total",
    "Number of times provenance sub-sections were capped",
    ["section"],
)
_http_requests_total = Counter(
    "atlas_http_requests_total",
    "Total HTTP requests by method, path template, and status code",
    ["method", "path_template", "status_code"],
)
_http_request_duration = Histogram(
    "atlas_http_request_duration_seconds",
    "HTTP request latency by method and path template",
    ["method", "path_template"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
_http_requests_in_flight = Gauge(
    "atlas_http_requests_in_flight",
    "Current number of HTTP requests being processed (in-flight gauge)",
)

_ingestion_last_success_timestamp = Gauge(
    "atlas_ingestion_last_success_timestamp_seconds",
    "Unix timestamp of the most recent successful ingestion by source",
    ["source"],
)
_ingestion_runs_db_total = Gauge(
    "atlas_ingestion_runs_total",
    "Number of ingestion runs recorded in the database by source and status",
    ["source", "status"],
)
_conflicts_open_total = Gauge(
    "atlas_conflicts_open_total",
    "Current number of open claim conflicts",
)
_conflicts_oldest_open_age_seconds = Gauge(
    "atlas_conflicts_oldest_open_age_seconds",
    "Age in seconds of the oldest open conflict",
)
_duplicate_candidates_pending_total = Gauge(
    "atlas_duplicate_candidates_pending_total",
    "Current number of pending duplicate candidates",
)
_data_quality_issues_open_total = Gauge(
    "atlas_data_quality_issues_open_total",
    "Current number of open data-quality issues by issue code",
    ["issue_code"],
)

_archive_manifests_db_total = Gauge(
    "atlas_archive_manifests_total",
    "Number of archive manifests recorded in the database by status",
    ["status"],
)
_archive_last_success_timestamp = Gauge(
    "atlas_archive_last_success_timestamp_seconds",
    "Unix timestamp of the most recent completed archive run",
)
_source_documents_unverified_total = Gauge(
    "atlas_source_documents_unverified_total",
    "Current number of source documents that are not URL-verified",
)

# ── Rate limiter ───────────────────────────────────────────────────────────────

def _build_limiter_with_settings(s: Any) -> Limiter:
    """
    Build a SlowAPI limiter from a Settings-like object.
    Extracted so create_app() and _build_limiter() share the same logic.
    """
    kwargs: dict[str, Any] = {
        "key_func": get_remote_address,
        "default_limits": [s.rate_limit_default],
    }
    if s.rate_limit_storage_url:
        kwargs["storage_uri"] = s.rate_limit_storage_url
    return Limiter(**kwargs)


def _build_limiter() -> Limiter:
    """Build the module-level limiter using the global settings singleton."""
    return _build_limiter_with_settings(settings)

_limiter = _build_limiter()

# Module-level constant derived from settings so the FastAPI Query() declaration
# can advertise the limit in OpenAPI and reject oversize input at parse time,
# rather than only validating it manually inside the endpoint body. Settings
# values are evaluated at import time; runtime overrides re-validate via the
# explicit length check inside list_accidents (which uses _app_settings(request)).
_SEARCH_Q_MAX_LENGTH = settings.search_q_max_length

# All business routes are registered on this router. create_app() includes the
# router into a fresh FastAPI instance, so tests and deployments can build a
# real route-bearing app from an explicit Settings object without copying routes
# from the module-level singleton.
api_router = APIRouter()


def _escape_like(value: str) -> str:
    """
    Escape LIKE metacharacters in a user-supplied search string.

    SQLAlchemy correctly parameterizes LIKE patterns to prevent SQL injection,
    but it does NOT escape the pattern metacharacters % and _ that PostgreSQL
    interprets inside the pattern.  Without escaping:
      - q="%" matches every row
      - q="_" matches every single-character field value
      - q="C-130_ABC" treats _ as a single-character wildcard

    Callers must pass escape="\\" to .like() so Postgres honours the escape.
    """
    return (
        value
        .replace("\\", "\\\\")   # must be first — escape the escape char
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def _b64url_decode_json(token: str) -> dict[str, Any]:
    """Decode an opaque URL-safe cursor token."""
    try:
        padded = token + ("=" * (-len(token) % 4))
        value = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
    except Exception as exc:  # noqa: BLE001 - normalize every decode failure for API clients
        raise HTTPException(status_code=400, detail="Invalid pagination cursor") from exc
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail="Invalid pagination cursor")
    return value


def _decode_date_cursor(cursor: str, *, expected_sort: str) -> tuple[datetime | None, str]:
    """
    Decode and validate a date-sort cursor.

    v28 originally accepted a bare {"at", "id"} token.  Keep accepting that
    shape for compatibility, while new tokens also include sort/version so a
    cursor from one ordering cannot accidentally be reused with another.
    """
    payload = _b64url_decode_json(cursor)
    cursor_sort = payload.get("sort")
    if cursor_sort is not None and cursor_sort != expected_sort:
        raise HTTPException(status_code=400, detail="Cursor was created for a different sort")

    cur_id = payload.get("id")
    if not isinstance(cur_id, str) or not cur_id:
        raise HTTPException(status_code=400, detail="Invalid pagination cursor")

    raw_at = payload.get("at")
    if raw_at in (None, ""):
        return None, cur_id
    if not isinstance(raw_at, str):
        raise HTTPException(status_code=400, detail="Invalid pagination cursor")
    try:
        return datetime.fromisoformat(raw_at), cur_id
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid pagination cursor") from exc


def _encode_date_cursor(record: AccidentRecord, *, sort: str) -> str:
    """Build the next-page cursor from the last returned row."""
    payload = json.dumps(
        {
            "v": 1,
            "sort": sort,
            "at": record.occurred_at.isoformat() if record.occurred_at else None,
            "id": record.id,
        },
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(payload.encode()).rstrip(b"=").decode()


def _apply_date_cursor(stmt: Any, *, sort: str, cur_at: datetime | None, cur_id: str) -> Any:
    """
    Apply a keyset predicate matching the public date sorts exactly.

    date_desc order: occurred_at DESC NULLS LAST, id ASC
    date_asc order:  occurred_at ASC NULLS FIRST, id ASC

    The NULL branches are explicit because SQL comparisons such as < and > do
    not match NULL values, yet NULL rows are part of both sort orders.
    """
    if sort == "date_desc":
        if cur_at is None:
            return stmt.where(
                and_(AccidentRecord.occurred_at.is_(None), AccidentRecord.id > cur_id)
            )
        return stmt.where(
            or_(
                AccidentRecord.occurred_at < cur_at,
                AccidentRecord.occurred_at.is_(None),
                and_(AccidentRecord.occurred_at == cur_at, AccidentRecord.id > cur_id),
            )
        )

    # date_asc: NULL rows come first, then non-NULL dates ascending.
    if cur_at is None:
        return stmt.where(
            or_(
                and_(AccidentRecord.occurred_at.is_(None), AccidentRecord.id > cur_id),
                AccidentRecord.occurred_at.is_not(None),
            )
        )
    return stmt.where(
        or_(
            AccidentRecord.occurred_at > cur_at,
            and_(AccidentRecord.occurred_at == cur_at, AccidentRecord.id > cur_id),
        )
    )


def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> Response:  # type: ignore[return]
    """Return a plain 429 with a Retry-After hint instead of the default 500."""
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=429,
        content={
            "detail": f"Rate limit exceeded: {exc.detail}. Slow down and retry.",
        },
        headers={"Retry-After": "60"},
    )


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    # Production safety guards must read the module-level `settings` global, not
    # `app.state.settings`. `app.state.settings` is set at create_app() time and
    # is a stable reference that does not see later module-level patches; it
    # exists so per-request handlers can read whichever Settings object their
    # specific app instance was built with. Startup guards, by contrast, are
    # global concerns — there is one production deployment per process — so we
    # read the module global here to match how operators actually deploy and to
    # let tests that patch `atlas.api.app.settings` exercise the guards.
    s = settings
    log.info("atlas.api.start", version=s.api_version, env=s.app_env)

    # Production safety: in production mode, refuse to start without auth.
    if s.app_env == "production" and not s.api_auth_enabled:
        raise RuntimeError(
            "FATAL: APP_ENV=production but API_AUTH_ENABLED=false. "
            "Set API_AUTH_ENABLED=true and seed API keys before deploying. "
            "This check exists because a warning in the logs is not a sufficient "
            "control — write endpoints are unauthenticated otherwise."
        )

    # Production safety: in-memory rate limiting is per-process.
    if (
        s.app_env == "production"
        and s.rate_limit_enabled
        and not s.rate_limit_storage_url
    ):
        raise RuntimeError(
            "FATAL: APP_ENV=production with RATE_LIMIT_ENABLED=true but "
            "RATE_LIMIT_STORAGE_URL is not set. In-memory rate limiting is "
            "per-process and does not work correctly with multiple workers. "
            "Set RATE_LIMIT_STORAGE_URL=redis://... before deploying."
        )

    # Production safety: unprotected /metrics on a public interface is a
    # configuration error, not just a warning.  Operators can opt out by
    # setting METRICS_PUBLIC_OK=true if /metrics is behind a network policy.
    if (
        s.app_env == "production"
        and not s.metrics_token
        and not s.metrics_public_ok
    ):
        raise RuntimeError(
            "FATAL: APP_ENV=production but /metrics is unprotected. "
            "Either set METRICS_TOKEN=<secret> to require bearer-token auth, "
            "or set METRICS_PUBLIC_OK=true to acknowledge that /metrics is "
            "protected by a network policy and intentionally public."
        )

    if not s.api_auth_enabled:
        log.warning(
            "atlas.api.auth_disabled",
            message=(
                "API_AUTH_ENABLED=false — reviewer write endpoints such as "
                "conflict resolution are UNAUTHENTICATED, while admin override "
                "endpoints are disabled.  Acceptable for local development and CI; "
                "never deploy this to a network-reachable host without setting "
                "API_AUTH_ENABLED=true and seeding API keys via `atlas keys create`."
            ),
        )

    yield
    log.info("atlas.api.stop")


def _app_settings(request: Request) -> Any:
    """Return the Settings object attached to the current FastAPI app."""
    return getattr(request.app.state, "settings", settings)


def _install_route_rate_limits(app: FastAPI, limiter: Limiter, s: Any) -> None:
    """Attach per-route rate limits/exemptions for this app instance.

    Routes live on a module-level APIRouter, but the limiter and configured
    limits belong to the app instance. Installing the limit metadata here keeps
    create_app(Settings(...)) from relying on the module singleton's limiter or
    on route-copy side effects.
    """
    from fastapi.routing import APIRoute

    exempt_paths = {"/api/v1/health", "/api/v1/readyz", "/metrics"}
    route_limits = {
        "/api/v1/accidents/map": s.rate_limit_map,
        "/api/v1/analytics/summary": s.rate_limit_analytics,
        "/api/v1/accidents/{event_id}/provenance": s.rate_limit_provenance,
        "/api/v1/admin/events/{event_id}/force-resolve-field": s.rate_limit_mutations,
        "/api/v1/conflicts/{conflict_id}/resolve": s.rate_limit_mutations,
        "/api/v1/duplicates/{candidate_id}/confirm": s.rate_limit_mutations,
        "/api/v1/duplicates/{candidate_id}/reject": s.rate_limit_mutations,
        "/api/v1/data-quality/issues/{issue_id}/resolve": s.rate_limit_mutations,
    }

    if not s.rate_limit_enabled:
        return

    for route in app.router.routes:
        if not isinstance(route, APIRoute):
            continue

        endpoint = route.endpoint
        if route.path in exempt_paths:
            limiter.exempt(endpoint)
            # Clear any stale rate-limit marker if this endpoint was previously
            # rate-limited under a different Settings configuration. Keeps
            # introspection (and TestInfraEndpointsExemptFromRateLimits) honest.
            if hasattr(endpoint, "_rate_limits"):
                try:
                    delattr(endpoint, "_rate_limits")
                except Exception:
                    pass
            continue

        if route.path in route_limits:
            limit_str = route_limits[route.path]
            # Avoid accumulating stale route-specific limits when tests create
            # multiple app instances with different Settings objects. SlowAPI
            # itself tracks limits in Limiter._route_limits keyed by function
            # name; the marker attribute below is what introspection callers
            # (and tests) use to confirm a limit was installed for this route.
            if hasattr(endpoint, "_rate_limits"):
                try:
                    delattr(endpoint, "_rate_limits")
                except Exception:
                    pass
            limiter.limit(limit_str)(endpoint)
            # Marker attribute: the SlowAPI decorator stores its bookkeeping on
            # the Limiter, not the function, so we attach an explicit marker
            # here. Truthy tuple so `hasattr` is True and `getattr(..., None)`
            # is truthy for the positive test, and other introspection callers
            # can read which limit applies.
            try:
                endpoint._rate_limits = (limit_str,)  # type: ignore[attr-defined]
            except Exception:
                pass


def create_app(app_settings: Any = None) -> FastAPI:
    """Create a fully routed, settings-isolated FastAPI application.

    All API routes are registered on ``api_router`` below and included here.
    The returned app has its own settings object, limiter, middleware, exception
    handlers, lifespan, and dependency overrides; it no longer copies routes
    from the module-level singleton.
    """
    from atlas.config import get_settings as _get_settings

    s = app_settings or _get_settings()
    limiter = _build_limiter_with_settings(s)

    created = FastAPI(
        title=s.api_title,
        version=s.api_version,
        description="Aviation Safety Atlas — claim-based aviation accident data with full provenance.",
        lifespan=lifespan,
    )

    created.state.settings = s
    created.state.limiter = limiter

    created.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins_list,
        allow_methods=["GET", "POST"],
        # Explicit header allowlist — wildcard is too broad for reviewer/admin APIs.
        allow_headers=["Content-Type", "X-API-Key", "Accept", "Authorization"],
    )

    created.include_router(api_router)
    created.include_router(timeline_router)
    created.include_router(weather_router)
    created.include_router(system_failures_router)
    created.include_router(analytics_router)
    created.include_router(flight_path_router)
    _install_route_rate_limits(created, limiter, s)

    if s.rate_limit_enabled:
        created.add_middleware(SlowAPIMiddleware)
        created.add_exception_handler(RateLimitExceeded, _rate_limit_handler)  # type: ignore[arg-type]

    created.middleware("http")(_record_request_metrics)
    return created


# ── Request metrics middleware ─────────────────────────────────────────────────
# Records per-request latency and status code using the matched route template
# (e.g. "/api/v1/accidents/{event_id}") rather than the raw URL so high-
# cardinality event IDs don't explode the label space.

async def _record_request_metrics(request: Request, call_next: Any) -> Any:
    # Use the matched route path template, fall back to a fixed sentinel.
    # NEVER use the raw URL as a label — one unique path per accident_id would
    # cardinality-bomb Prometheus and make the store unusable.
    path_template = "__unmatched__"
    for route in request.app.routes:
        match, _ = route.matches({"type": "http", "path": request.url.path, "method": request.method})
        if match.value >= 1 and hasattr(route, "path"):
            path_template = route.path
            break

    _http_requests_in_flight.inc()
    t0 = time.perf_counter()
    status_code = "500"  # default — overwritten on success or known HTTP error

    try:
        response = await call_next(request)
        status_code = str(response.status_code)
        return response
    except Exception:
        # Unhandled exception — still emit metrics so the error is visible
        # in dashboards.  Re-raise so FastAPI's exception handlers take over.
        raise
    finally:
        elapsed = time.perf_counter() - t0
        _http_requests_in_flight.dec()
        _http_request_duration.labels(
            method=request.method,
            path_template=path_template,
        ).observe(elapsed)
        _http_requests_total.labels(
            method=request.method,
            path_template=path_template,
            status_code=status_code,
        ).inc()


# ── Response schemas (defined in atlas.api.schemas) ───────────────────────────
# Re-exported below for backwards compatibility.

# ── Helpers ────────────────────────────────────────────────────────────────────

def _conf_out(score: float | None, breakdown: dict[str, Any] | None = None) -> ConfidenceOut:
    s = float(score or 0.0)
    label, css = confidence_label(s)
    return ConfidenceOut(score=round(s, 3), label=label, css_class=css, breakdown=breakdown)


def _to_summary(record: AccidentRecord, event: AccidentEvent) -> AccidentSummary:
    return AccidentSummary(
        id=event.id, canonical_id=event.canonical_id,
        occurred_at=record.occurred_at, occurred_date=record.occurred_date,
        occurred_year=record.occurred_year,
        occurred_at_precision=record.occurred_at_precision,
        location_text=record.location_text, country_code=record.country_code,
        location_lat=float(record.location_lat) if record.location_lat is not None else None,
        location_lon=float(record.location_lon) if record.location_lon is not None else None,
        aircraft_make=record.aircraft_make, aircraft_model=record.aircraft_model,
        operator_name=record.operator_name, phase_of_flight=record.phase_of_flight,
        injury_severity=record.injury_severity, fatalities_total=record.fatalities_total,
        fatalities_crew=record.fatalities_crew,
        fatalities_passengers=record.fatalities_passengers,
        serious_injuries_crew=record.serious_injuries_crew,
        serious_injuries_passengers=record.serious_injuries_passengers,
        minor_injuries_crew=record.minor_injuries_crew,
        minor_injuries_passengers=record.minor_injuries_passengers,
        uninjured_crew=record.uninjured_crew,
        uninjured_passengers=record.uninjured_passengers,
        aboard_total=record.aboard_total, aircraft_damage=record.aircraft_damage,
        investigation_status=record.investigation_status,
        confidence=_conf_out(record.confidence_score),
        # Summary responses intentionally omit confidence_breakdown to keep list payloads
        # small. The full breakdown is included in AccidentDetail (single-record endpoint).
        has_conflicts=record.has_conflicts,
        winning_source_count=len(record.source_ids or []),
        claim_source_count=len(record.claim_source_ids or []),
        primary_source_id=record.primary_source_id,
    )


# ── Routes ─────────────────────────────────────────────────────────────────────

@api_router.get("/api/v1/health")
async def health(request: Request, db: AsyncSession = Depends(get_read_db)) -> dict[str, str]:
    s = _app_settings(request)
    await db.execute(text("SELECT 1"))
    return {"status": "ok", "version": s.api_version}


async def _refresh_database_metrics(db: AsyncSession) -> None:
    """Refresh scrape-time gauges that reflect current database state."""
    try:
        open_count = (await db.execute(
            select(func.count()).select_from(ClaimConflict).where(ClaimConflict.status == "open")
        )).scalar_one()
        _conflicts_open_total.set(int(open_count or 0))

        oldest = (await db.execute(
            select(func.min(ClaimConflict.created_at)).where(ClaimConflict.status == "open")
        )).scalar_one_or_none()
        if oldest is not None:
            if oldest.tzinfo is None:
                oldest = oldest.replace(tzinfo=UTC)
            _conflicts_oldest_open_age_seconds.set(
                max(0.0, (datetime.now(tz=UTC) - oldest).total_seconds())
            )
        else:
            _conflicts_oldest_open_age_seconds.set(0)

        pending_dupes = (await db.execute(
            select(func.count()).select_from(DuplicateCandidateReview)
            .where(DuplicateCandidateReview.status == "pending")
        )).scalar_one()
        _duplicate_candidates_pending_total.set(int(pending_dupes or 0))

        # Clear/update known data-quality labels by setting observed codes.
        dq_rows = (await db.execute(
            select(DataQualityIssue.issue_code, func.count())
            .where(DataQualityIssue.status == "open")
            .group_by(DataQualityIssue.issue_code)
        )).all()
        # Ensure the common split mismatch label exists even at zero.
        seen_codes = {"split_total_mismatch"}
        for code, count in dq_rows:
            seen_codes.add(code)
            _data_quality_issues_open_total.labels(issue_code=code).set(int(count or 0))
        for code in seen_codes - {row[0] for row in dq_rows}:
            _data_quality_issues_open_total.labels(issue_code=code).set(0)

        run_rows = (await db.execute(
            select(IngestionRun.source_name, IngestionRun.status, func.count())
            .group_by(IngestionRun.source_name, IngestionRun.status)
        )).all()
        for source_name, status, count in run_rows:
            _ingestion_runs_db_total.labels(source=source_name, status=status).set(int(count or 0))

        success_rows = (await db.execute(
            select(IngestionRun.source_name, func.max(IngestionRun.completed_at))
            .where(IngestionRun.status == "completed")
            .group_by(IngestionRun.source_name)
        )).all()
        for source_name, completed_at in success_rows:
            if completed_at is None:
                continue
            if completed_at.tzinfo is None:
                completed_at = completed_at.replace(tzinfo=UTC)
            _ingestion_last_success_timestamp.labels(source=source_name).set(completed_at.timestamp())

        archive_rows = (await db.execute(
            select(ArchiveManifest.status, func.count()).group_by(ArchiveManifest.status)
        )).all()
        for status, count in archive_rows:
            _archive_manifests_db_total.labels(status=status).set(int(count or 0))
        last_archive = (await db.execute(
            select(func.max(ArchiveManifest.completed_at)).where(ArchiveManifest.status == "completed")
        )).scalar_one_or_none()
        if last_archive is not None:
            if last_archive.tzinfo is None:
                last_archive = last_archive.replace(tzinfo=UTC)
            _archive_last_success_timestamp.set(last_archive.timestamp())
        unverified_docs = (await db.execute(
            select(func.count()).select_from(SourceDocument).where(SourceDocument.url_verified.is_(False))
        )).scalar_one()
        _source_documents_unverified_total.set(int(unverified_docs or 0))
    except Exception:
        log.exception("metrics.db_refresh_failed")


@api_router.get("/metrics", include_in_schema=False)
async def metrics(request: Request, db: AsyncSession = Depends(get_read_db)) -> Response:
    """
    Prometheus metrics endpoint.  Not versioned — scrapers pin to this path.
    Excluded from OpenAPI docs to avoid confusion with the business API.

    **Production note**: this endpoint exposes service internals.  In production
    you should either bind it to a non-public interface, place it behind a
    network policy, or set METRICS_TOKEN to require a bearer token.  Exposing
    it publicly without any protection leaks timing and throughput data.
    """
    # Optional token protection — set METRICS_TOKEN env var to require a bearer
    # token.  When unset, the endpoint is open (acceptable behind a firewall;
    # not acceptable on a public interface).
    s = _app_settings(request)
    metrics_token = s.metrics_token
    if metrics_token:
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer ") or auth_header[7:] != metrics_token:
            return Response(
                content='{"detail": "metrics endpoint requires Authorization: Bearer <METRICS_TOKEN>"}',
                status_code=401,
                media_type="application/json",
            )
    await _refresh_database_metrics(db)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@api_router.get("/api/v1/readyz")
async def readyz(request: Request, db: AsyncSession = Depends(get_read_db)) -> dict[str, Any]:
    """
    Readiness check.  Returns 200 only when the service can safely handle
    traffic: database reachable, migrations at head, required sources seeded.

    Unlike /health (liveness, just SELECT 1), this endpoint verifies the
    application is actually ready to serve meaningful responses.

    Migration check: reads the current version from `alembic_version` and
    compares it against the script head derived from `alembic.ini`.  A database
    at migration 0008 with a codebase at 0013 is correctly reported as not ready.
    """
    s = _app_settings(request)
    checks: dict[str, str] = {}
    ready = True

    # 1. Database reachable
    try:
        await db.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = f"error: {exc}"
        ready = False

    # 2. Migrations at current script head
    # We use Alembic's own ScriptDirectory to discover the head revision so the
    # check is always current with the codebase, not a hardcoded string.
    # The alembic.ini path is resolved relative to this file so readyz is
    # independent of the process working directory — important in Docker where
    # CWD depends on WORKDIR and may differ from the project root.
    try:
        import pathlib

        from alembic.config import Config as AlembicConfig
        from alembic.script import ScriptDirectory

        # This file lives at src/atlas/api/app.py → project root is 3 levels up.
        _project_root = pathlib.Path(__file__).resolve().parents[3]
        _alembic_ini = _project_root / "alembic.ini"

        cfg = AlembicConfig(str(_alembic_ini))
        script_dir = ScriptDirectory.from_config(cfg)
        script_head = script_dir.get_current_head()

        row = (await db.execute(text("SELECT version_num FROM alembic_version"))).one_or_none()
        db_head = row[0] if row else None

        if db_head is None:
            checks["migrations"] = "error: alembic_version has no rows — run `atlas db migrate`"
            ready = False
        elif db_head != script_head:
            checks["migrations"] = (
                f"error: DB is at {db_head!r}, script head is {script_head!r} — "
                "run `atlas db migrate`"
            )
            ready = False
        else:
            checks["migrations"] = f"ok (at head={db_head})"
    except Exception as exc:
        checks["migrations"] = f"error: {exc}"
        ready = False

    # 3. Required source registry row — NTSB must exist for ingestion to work
    try:
        ntsb = (await db.execute(
            select(Source).where(Source.short_name == "NTSB")
        )).scalar_one_or_none()
        if ntsb:
            checks["ntsb_source"] = "ok"
        else:
            checks["ntsb_source"] = "error: NTSB source not seeded — run `atlas db seed`"
            ready = False
    except Exception as exc:
        checks["ntsb_source"] = f"error: {exc}"
        ready = False

    # 4. Rate-limit storage — ping Redis when configured, warn when in-memory
    if s.rate_limit_enabled and s.rate_limit_storage_url:
        try:
            from redis.asyncio import Redis as AsyncRedis
            redis_client = AsyncRedis.from_url(
                s.rate_limit_storage_url,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            await redis_client.ping()
            await redis_client.aclose()
            checks["rate_limit_storage"] = f"ok (redis: {s.rate_limit_storage_url})"
        except Exception as exc:
            checks["rate_limit_storage"] = (
                f"error: Redis ping failed ({s.rate_limit_storage_url!r}): {exc}"
            )
            ready = False
    elif s.rate_limit_enabled:
        checks["rate_limit_storage"] = (
            "warning: in-memory rate limiting (no RATE_LIMIT_STORAGE_URL). "
            "Per-process buckets multiply the effective limit by worker count. "
            "Set RATE_LIMIT_STORAGE_URL=redis://... for production."
        )
    else:
        checks["rate_limit_storage"] = "disabled (RATE_LIMIT_ENABLED=false)"

    status_code = 200 if ready else 503
    result: dict[str, Any] = {
        "ready": ready,
        "checks": checks,
        "version": s.api_version,
    }
    if not ready:
        raise HTTPException(status_code=status_code, detail=result)
    return result


@api_router.get("/api/v1/sources", response_model=list[SourceOut])
async def list_sources(db: AsyncSession = Depends(get_read_db)):
    r = await db.execute(select(Source).order_by(Source.tier))
    return r.scalars().all()




@api_router.get("/api/v1/ops/source-status", response_model=list[SourceStatusOut])
async def source_status(
    db: AsyncSession = Depends(get_read_db),
    operator: OperatorContext = Depends(require_reviewer),
) -> list[SourceStatusOut]:
    """Operator source-freshness view: configured sources plus latest ingestion state."""
    rows = (await db.execute(select(Source).order_by(Source.tier, Source.short_name))).scalars().all()
    out: list[SourceStatusOut] = []
    now = datetime.now(tz=UTC)
    for src in rows:
        latest = (await db.execute(
            select(IngestionRun)
            .where((IngestionRun.source_id == src.id) | (IngestionRun.source_name.ilike(f"%{src.short_name}%")))
            .order_by(IngestionRun.started_at.desc(), IngestionRun.id.asc())
            .limit(1)
        )).scalar_one_or_none()
        freshness = None
        if latest and latest.completed_at:
            completed = latest.completed_at if latest.completed_at.tzinfo else latest.completed_at.replace(tzinfo=UTC)
            freshness = max(0.0, (now - completed).total_seconds())
        out.append(SourceStatusOut(
            id=src.id, short_name=src.short_name, display_name=src.display_name,
            tier=src.tier, license_type=src.license_type, ingestion_enabled=src.ingestion_enabled,
            last_ingested_at=src.last_ingested_at,
            latest_run_status=latest.status if latest else None,
            latest_run_completed_at=latest.completed_at if latest else None,
            latest_run_errors=latest.ingestion_errors if latest else None,
            freshness_age_seconds=freshness,
        ))
    return out


@api_router.get("/api/v1/ops/ingestion-runs", response_model=list[IngestionRunOut])
async def list_ingestion_runs(
    source_name: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_read_db),
    operator: OperatorContext = Depends(require_reviewer),
) -> list[IngestionRunOut]:
    stmt = select(IngestionRun)
    if source_name:
        stmt = stmt.where(IngestionRun.source_name.ilike(f"%{_escape_like(source_name)}%"))
    if status:
        stmt = stmt.where(IngestionRun.status == status)
    rows = (await db.execute(
        stmt.order_by(IngestionRun.started_at.desc(), IngestionRun.id.asc()).limit(limit)
    )).scalars().all()
    return [IngestionRunOut.model_validate(r, from_attributes=True) for r in rows]


@api_router.get("/api/v1/accidents", response_model=PaginatedAccidents)
async def list_accidents(
    request: Request,
    q: str | None = Query(
        None,
        max_length=_SEARCH_Q_MAX_LENGTH,
        description="Free-text search (location, aircraft, operator, cause)",
    ),
    severity: str | None = None,
    phase: str | None = None,
    country: str | None = None,
    registration: str | None = Query(None, description="Aircraft registration exact/substring filter"),
    aircraft_type: str | None = Query(None, description="Aircraft make/model substring filter"),
    operator: str | None = Query(None, description="Operator substring filter"),
    source_id: str | None = Query(None, description="Only records with claims from this source"),
    disputed_only: bool = Query(False, description="Only records currently showing open conflicts"),
    final_report_only: bool = Query(False, description="Only records with at least one verified/final source document"),
    year_from: int | None = Query(None, ge=1919, le=2100),
    year_to: int | None = Query(None, ge=1919, le=2100),
    # Preferred name. min_confidence is kept as a legacy alias (same filter).
    min_source_completeness: float | None = Query(None, ge=0.0, le=1.0),
    min_confidence: float | None = Query(None, ge=0.0, le=1.0, include_in_schema=False),
    # fatality_status=some  → fatalities_total > 0
    # fatality_status=none  → fatalities_total == 0  (confirmed zero, not unknown)
    # fatality_status=unknown → fatalities_total IS NULL
    fatality_status: str | None = Query(None, pattern="^(some|none|unknown)$"),
    sort: Literal["date_desc", "date_asc", "source_completeness_desc", "confidence_desc", "fatalities_desc"] = "date_desc",
    # Offset pagination — accurately named (not keyset)
    page: int = Query(0, ge=0),
    page_size: int = Query(20, ge=1, le=100),
    # Cursor (keyset) pagination — stable under concurrent writes.
    # When cursor is provided, page/has_next are still returned for
    # backwards compatibility but the cursor is the stable navigation mechanism.
    cursor: str | None = Query(None, description="Opaque pagination cursor from previous response"),
    db: AsyncSession = Depends(get_read_db),
):
    s = _app_settings(request)
    if q is not None and len(q) > s.search_q_max_length:
        raise HTTPException(422, f"q must be at most {s.search_q_max_length} characters")

    stmt = (
        select(AccidentRecord, AccidentEvent)
        .join(AccidentEvent, AccidentRecord.id == AccidentEvent.id)
        .where(AccidentEvent.record_status == "active")
    )

    if q:
        t = f"%{_escape_like(q.lower())}%"
        stmt = stmt.where(
            func.lower(AccidentRecord.location_text).like(t, escape="\\")
            | func.lower(AccidentRecord.aircraft_make).like(t, escape="\\")
            | func.lower(AccidentRecord.aircraft_model).like(t, escape="\\")
            | func.lower(AccidentRecord.operator_name).like(t, escape="\\")
            | func.lower(AccidentRecord.probable_cause).like(t, escape="\\")
        )
    if severity: stmt = stmt.where(AccidentRecord.injury_severity == severity.upper())
    if phase:    stmt = stmt.where(func.upper(AccidentRecord.phase_of_flight) == phase.upper())
    if country:  stmt = stmt.where(AccidentRecord.country_code == country.upper())
    if registration:
        reg_t = f"%{_escape_like(registration.upper())}%"
        stmt = stmt.where(func.upper(AccidentRecord.aircraft_registration).like(reg_t, escape="\\"))
    if aircraft_type:
        ac_t = f"%{_escape_like(aircraft_type.lower())}%"
        stmt = stmt.where(
            func.lower(AccidentRecord.aircraft_make).like(ac_t, escape="\\")
            | func.lower(AccidentRecord.aircraft_model).like(ac_t, escape="\\")
        )
    if operator:
        op_t = f"%{_escape_like(operator.lower())}%"
        stmt = stmt.where(func.lower(AccidentRecord.operator_name).like(op_t, escape="\\"))
    if source_id:
        stmt = stmt.where(
            AccidentRecord.claim_source_ids.any(source_id)
            | AccidentRecord.source_ids.any(source_id)
            | (AccidentRecord.primary_source_id == source_id)
        )
    if disputed_only:
        stmt = stmt.where(AccidentRecord.has_conflicts.is_(True))
    if final_report_only:
        stmt = stmt.where(AccidentRecord.document_status == "verified")
    if year_from: stmt = stmt.where(AccidentRecord.occurred_year >= year_from)
    if year_to:   stmt = stmt.where(AccidentRecord.occurred_year <= year_to)
    # Accept either preferred or legacy param name
    effective_min = min_source_completeness if min_source_completeness is not None else min_confidence
    if effective_min is not None:
        stmt = stmt.where(AccidentRecord.confidence_score >= effective_min)
    if fatality_status == "some":
        stmt = stmt.where(AccidentRecord.fatalities_total > 0)
    elif fatality_status == "none":
        # Explicitly confirmed zero — not the same as unknown
        stmt = stmt.where(AccidentRecord.fatalities_total == 0)
    elif fatality_status == "unknown":
        stmt = stmt.where(AccidentRecord.fatalities_total.is_(None))

    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()

    order_map = {
        "date_desc": [AccidentRecord.occurred_at.desc().nullslast(), AccidentRecord.id.asc()],
        "date_asc": [AccidentRecord.occurred_at.asc().nullsfirst(), AccidentRecord.id.asc()],
        "source_completeness_desc": [AccidentRecord.confidence_score.desc().nullslast(), AccidentRecord.id.asc()],
        "confidence_desc": [AccidentRecord.confidence_score.desc().nullslast(), AccidentRecord.id.asc()],  # legacy alias
        "fatalities_desc": [AccidentRecord.fatalities_total.desc().nullslast(), AccidentRecord.id.asc()],
    }

    using_cursor = cursor is not None
    if using_cursor:
        if sort not in ("date_desc", "date_asc"):
            raise HTTPException(status_code=400, detail="Cursor pagination is only supported for date sorts")
        cur_at, cur_id = _decode_date_cursor(cursor, expected_sort=sort)
        stmt = _apply_date_cursor(stmt, sort=sort, cur_at=cur_at, cur_id=cur_id)

    stmt = stmt.order_by(*order_map[sort])
    if not using_cursor:
        stmt = stmt.offset(page * page_size)
    stmt = stmt.limit(page_size + 1)

    pairs = (await db.execute(stmt)).all()
    has_more = len(pairs) > page_size
    page_pairs = pairs[:page_size]

    # Build next cursor from the last item in this page.  Cursors are emitted
    # only for date sorts because those have an exact keyset predicate above.
    next_cursor: str | None = None
    if has_more and page_pairs and sort in ("date_desc", "date_asc"):
        last_rec, _ = page_pairs[-1]
        next_cursor = _encode_date_cursor(last_rec, sort=sort)

    return PaginatedAccidents(
        items=[_to_summary(rec, evt) for rec, evt in page_pairs],
        total=total, page=page, page_size=page_size,
        has_next=has_more,
        next_cursor=next_cursor,
    )


def _map_cluster_cell_degrees(zoom: int) -> float:
    """Return a conservative grid-cell size in degrees for low-zoom clustering.

    This is intentionally simple and database-portable: low zooms use large
    cells, then the grid halves with each zoom step. At zoom 6 the cell is
    ~0.7°; above settings.map_cluster_max_zoom the endpoint returns individual
    points instead of clusters.
    """
    return max(0.1, 45.0 / (2 ** max(0, zoom)))


# ── Dedicated map endpoint ─────────────────────────────────────────────────────
# Returns geocoded accidents capped at settings.max_map_results.  The old
# version returned the entire geocoded dataset in one shot — a memory and
# browser-tab killer on large datasets.  The response now includes `truncated`
# so the frontend can surface a warning when the cap is reached.


@api_router.get("/api/v1/accidents/map")
async def map_accidents(
    request: Request,
    severity: str | None = None,
    year_from: int | None = Query(None, ge=1919, le=2100),
    year_to: int | None = Query(None, ge=1919, le=2100),
    zoom: int | None = Query(None, ge=0, le=22, description="Current Leaflet/Web Mercator zoom level"),
    # Bounding-box viewport filters — return only accidents within the box.
    # Use the current map viewport bounds for efficient large-dataset browsing.
    north: float | None = Query(None, ge=-90, le=90, description="Northern latitude bound"),
    south: float | None = Query(None, ge=-90, le=90, description="Southern latitude bound"),
    east: float | None = Query(None, ge=-180, le=180, description="Eastern longitude bound"),
    west: float | None = Query(None, ge=-180, le=180, description="Western longitude bound"),
    db: AsyncSession = Depends(get_read_db),
) -> dict:
    """
    Return geocoded accidents for the map view.

    High zoom levels return individual accident points capped at
    `settings.max_map_results`. Low zoom levels (`zoom <=
    settings.map_cluster_max_zoom`) return grid clusters instead, also capped,
    so global/continent views remain small enough for the backend and browser.

    Supports optional bounding-box filtering via `north`, `south`, `east`,
    `west` to limit results to the current map viewport — the preferred way to
    avoid hitting the cap on large datasets.

    Anti-meridian crossing (west > east) is not currently supported — split the
    request into two calls at ±180° if needed.
    """
    # Validate bounding-box consistency before hitting the DB.
    bbox_params = [north, south, east, west]
    if any(p is not None for p in bbox_params) and any(p is None for p in bbox_params):
        raise HTTPException(
            422,
            "Bounding box requires all four parameters: north, south, east, west.",
        )
    if north is not None and south is not None and south > north:
        raise HTTPException(422, f"south ({south}) must be ≤ north ({north}).")
    if west is not None and east is not None and west > east:
        raise HTTPException(
            422,
            "Bounding boxes crossing the anti-meridian (west > east) are not "
            "supported. Split the request into two boxes at ±180° instead. "
            "Example: [west=-180, east=-160] and [west=160, east=180].",
        )

    s = _app_settings(request)
    limit = s.max_map_results

    filters = [
        AccidentEvent.record_status == "active",
        AccidentRecord.location_lat.isnot(None),
        AccidentRecord.location_lon.isnot(None),
    ]
    if severity:
        filters.append(AccidentRecord.injury_severity == severity.upper())
    if year_from:
        filters.append(AccidentRecord.occurred_year >= year_from)
    if year_to:
        filters.append(AccidentRecord.occurred_year <= year_to)
    if north is not None:
        filters.extend([
            AccidentRecord.location_lat.between(south, north),
            AccidentRecord.location_lon.between(west, east),
        ])

    should_cluster = zoom is not None and zoom <= s.map_cluster_max_zoom
    if should_cluster:
        cell_degrees = _map_cluster_cell_degrees(zoom)
        lat_bucket = func.floor(AccidentRecord.location_lat / cell_degrees)
        lon_bucket = func.floor(AccidentRecord.location_lon / cell_degrees)
        count_expr = func.count().label("count")
        fatalities_expr = func.coalesce(func.sum(AccidentRecord.fatalities_total), 0).label("fatalities_total")

        cluster_stmt = (
            select(
                lat_bucket.label("lat_bucket"),
                lon_bucket.label("lon_bucket"),
                func.avg(AccidentRecord.location_lat).label("location_lat"),
                func.avg(AccidentRecord.location_lon).label("location_lon"),
                count_expr,
                fatalities_expr,
                func.max(AccidentRecord.occurred_year).label("latest_occurred_year"),
            )
            .select_from(AccidentRecord)
            .join(AccidentEvent, AccidentRecord.id == AccidentEvent.id)
            .where(*filters)
            .group_by(lat_bucket, lon_bucket)
            .order_by(count_expr.desc(), fatalities_expr.desc(), lat_bucket.asc(), lon_bucket.asc())
            .limit(limit + 1)
        )
        rows = (await db.execute(cluster_stmt)).all()
        truncated = len(rows) > limit
        rows = rows[:limit]
        clusters = []
        for row in rows:
            m = row._mapping
            clusters.append(
                MapCluster(
                    cluster_id=f"z{zoom}:{int(m['lat_bucket'])}:{int(m['lon_bucket'])}",
                    location_lat=float(m['location_lat']),
                    location_lon=float(m['location_lon']),
                    count=int(m['count']),
                    fatalities_total=int(m['fatalities_total'] or 0),
                    latest_occurred_year=int(m['latest_occurred_year']) if m['latest_occurred_year'] is not None else None,
                    cell_degrees=cell_degrees,
                )
            )
        if truncated:
            log.warning(
                "map.clusters_truncated",
                returned=len(clusters),
                limit=limit,
                zoom=zoom,
                severity=severity,
                year_from=year_from,
                year_to=year_to,
                bbox=f"{north},{south},{east},{west}" if north is not None else None,
            )
            _map_truncation_total.inc()
        return {
            "mode": "clusters",
            "items": [],
            "clusters": [c.model_dump() for c in clusters],
            "count": len(clusters),
            "truncated": truncated,
            "limit": limit,
            "zoom": zoom,
            "cluster_cell_degrees": cell_degrees,
        }

    stmt = (
        select(AccidentRecord, AccidentEvent)
        .join(AccidentEvent, AccidentRecord.id == AccidentEvent.id)
        .where(*filters)
        .order_by(
            AccidentRecord.occurred_at.desc().nullslast(),
            AccidentRecord.fatalities_total.desc().nullslast(),
            AccidentRecord.id.asc(),
        )
        .limit(limit + 1)
    )
    pairs = (await db.execute(stmt)).all()
    truncated = len(pairs) > limit
    pairs = pairs[:limit]

    items = [
        MapAccident(
            id=r.id,
            canonical_id=evt.canonical_id,
            location_lat=r.location_lat,
            location_lon=r.location_lon,
            location_text=r.location_text,
            injury_severity=r.injury_severity,
            fatalities_total=r.fatalities_total,
            aircraft_make=r.aircraft_make,
            aircraft_model=r.aircraft_model,
            occurred_date=r.occurred_date,
            occurred_year=r.occurred_year,
            phase_of_flight=r.phase_of_flight,
            source_completeness_score=r.confidence_score,
        )
        for r, evt in pairs
    ]
    if truncated:
        log.warning(
            "map.truncated",
            returned=len(items),
            limit=limit,
            zoom=zoom,
            severity=severity,
            year_from=year_from,
            year_to=year_to,
            bbox=f"{north},{south},{east},{west}" if north is not None else None,
        )
        _map_truncation_total.inc()
    return {
        "mode": "points",
        "items": [i.model_dump() for i in items],
        "clusters": [],
        "count": len(items),
        "truncated": truncated,
        "limit": limit,
        "zoom": zoom,
        "cluster_cell_degrees": None,
    }


# ── Dedicated analytics endpoint ───────────────────────────────────────────────
# Computes aggregate statistics over the full dataset via SQL.
# The frontend analytics page must use this endpoint so that charts reflect
# the whole dataset and not just the first page of paginated search results.


@api_router.get("/api/v1/analytics/summary", response_model=AnalyticsSummary)
async def analytics_summary(request: Request, db: AsyncSession = Depends(get_read_db)):
    """
    Full-dataset aggregate statistics for the analytics dashboard.
    All aggregations are computed in the database over the complete active
    record set — not sampled from a paginated result page.

    Results are cached for `settings.analytics_cache_ttl_s` seconds
    (default 60s) because the data changes only at ingestion time and each
    request runs five aggregation queries.  Set `ANALYTICS_CACHE_TTL_S=0`
    to disable caching in tests.
    """
    # Fast path — serve cached result without acquiring lock
    s = _app_settings(request)
    if _analytics_cache.is_fresh(s):
        return _analytics_cache.value

    async with _analytics_cache._lock:
        # Re-check after acquiring lock — another coroutine may have refreshed
        if _analytics_cache.is_fresh(s):
            return _analytics_cache.value

        result = await _compute_analytics_summary(db)
        _analytics_cache.store(result, s)
        return result


async def _compute_analytics_summary(db: AsyncSession) -> AnalyticsSummary:
    """Run all analytics aggregation queries and return the summary object."""
    active = (
        select(AccidentRecord)
        .join(AccidentEvent, AccidentRecord.id == AccidentEvent.id)
        .where(AccidentEvent.record_status == "active")
        .subquery()
    )

    totals = (await db.execute(
        select(
            func.count().label("total"),
            func.coalesce(func.sum(active.c.fatalities_total), 0).label("fatalities"),
            func.count().filter(active.c.injury_severity == "FATAL").label("fatal_count"),
            func.avg(active.c.confidence_score).label("avg_conf"),
        )
    )).one()

    # by_severity
    rows = (await db.execute(
        select(active.c.injury_severity, func.count())
        .group_by(active.c.injury_severity)
    )).all()
    by_severity = {(r[0] or "UNKNOWN"): r[1] for r in rows}

    # by_phase
    rows = (await db.execute(
        select(active.c.phase_of_flight, func.count())
        .where(active.c.phase_of_flight.isnot(None))
        .group_by(active.c.phase_of_flight)
    )).all()
    by_phase = {r[0]: r[1] for r in rows}

    # by_year
    rows = (await db.execute(
        select(active.c.occurred_year, func.count())
        .where(active.c.occurred_year.isnot(None))
        .group_by(active.c.occurred_year)
        .order_by(active.c.occurred_year)
    )).all()
    by_year = {r[0]: r[1] for r in rows}

    # source_completeness_bins — computed in SQL so we never materialise every
    # confidence_score row into Python memory.  Keys match the label vocabulary
    # in confidence/engine.py and the frontend confLabels array.
    # Thresholds: ≥0.90 well_sourced | ≥0.70 mostly_sourced | ≥0.50 partially_sourced | <0.50 weakly_sourced
    bins_row = (await db.execute(
        select(
            func.count().filter(active.c.confidence_score >= 0.90).label("well_sourced"),
            func.count().filter(
                active.c.confidence_score >= 0.70,
                active.c.confidence_score < 0.90,
            ).label("mostly_sourced"),
            func.count().filter(
                active.c.confidence_score >= 0.50,
                active.c.confidence_score < 0.70,
            ).label("partially_sourced"),
            func.count().filter(
                (active.c.confidence_score < 0.50) | active.c.confidence_score.is_(None)
            ).label("weakly_sourced"),
        )
    )).one()
    bins: dict[str, int] = {
        "well_sourced":      bins_row.well_sourced,
        "mostly_sourced":    bins_row.mostly_sourced,
        "partially_sourced": bins_row.partially_sourced,
        "weakly_sourced":    bins_row.weakly_sourced,
    }

    return AnalyticsSummary(
        total_accidents=totals.total,
        total_fatalities=int(totals.fatalities),
        fatal_count=totals.fatal_count,
        avg_confidence=round(float(totals.avg_conf or 0.0), 3),
        by_severity=by_severity,
        by_phase=by_phase,
        by_year=by_year,
        confidence_bins=bins,
    )
@api_router.get(
    "/api/v1/conflicts",
    response_model=list[ConflictQueueItem],
    summary="Global conflict review queue (reviewer only)",
    description=(
        "Returns all open claim conflicts across all events, enriched with "
        "event context and both claim values for side-by-side review.\n\n"
        "**Authentication**: requires a reviewer or admin API key in the "
        "`X-API-Key` header when `API_AUTH_ENABLED=true`.  When auth is "
        "disabled (local dev / CI), this endpoint is openly accessible.\n\n"
        "Ordered by conflict creation date (oldest first) so a reviewer "
        "working through the queue sees the longest-standing disputes first."
    ),
)
async def list_open_conflicts(
    field_name: str | None = Query(None, description="Filter by field name"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_read_db),
    operator: OperatorContext = Depends(require_reviewer),
) -> list[ConflictQueueItem]:
    """Global reviewer queue — all open conflicts with event + claim context."""
    stmt = (
        select(ClaimConflict, AccidentEvent, AccidentRecord)
        .join(AccidentEvent, ClaimConflict.event_id == AccidentEvent.id)
        .outerjoin(AccidentRecord, AccidentRecord.id == AccidentEvent.id)
        .where(
            ClaimConflict.status == "open",
            AccidentEvent.record_status == "active",
        )
    )
    if field_name:
        stmt = stmt.where(ClaimConflict.field_name == field_name)
    stmt = stmt.order_by(ClaimConflict.created_at.asc()).limit(limit)

    rows = (await db.execute(stmt)).all()

    # Collect all claim IDs we need
    claim_ids: set[str] = set()
    for conflict, _, _ in rows:
        claim_ids.update({conflict.claim_a_id, conflict.claim_b_id})

    claims_by_id: dict[str, Any] = {}
    if claim_ids:
        claim_rows = (await db.execute(
            select(Claim, Source)
            .join(Source, Claim.source_id == Source.id)
            .where(Claim.id.in_(claim_ids))
        )).all()
        for claim, source in claim_rows:
            claims_by_id[claim.id] = (claim, source)

    # Closure captures only claims_by_id (loop-invariant), so define once
    # outside the loop. Avoids re-creating the function object per iteration
    # and makes the loop body purely about constructing items.
    def _display(claim_id: str) -> tuple[str | None, str | None]:
        entry = claims_by_id.get(claim_id)
        if not entry:
            return None, None
        claim, source = entry
        try:
            display = cv.display(claim.field_value)
        except Exception:
            display = None
        return display, source.short_name

    items = []
    for conflict, event, record in rows:
        a_val, a_src = _display(conflict.claim_a_id)
        b_val, b_src = _display(conflict.claim_b_id)

        items.append(ConflictQueueItem(
            conflict_id=conflict.id,
            event_id=event.id,
            canonical_id=event.canonical_id,
            field_name=conflict.field_name,
            claim_a_id=conflict.claim_a_id,
            claim_b_id=conflict.claim_b_id,
            claim_a_value=a_val,
            claim_b_value=b_val,
            claim_a_source=a_src,
            claim_b_source=b_src,
            created_at=conflict.created_at,
            occurred_date=record.occurred_date if record else None,
            location_text=record.location_text if record else None,
            injury_severity=record.injury_severity if record else None,
        ))
    return items


@api_router.get(
    "/api/v1/conflicts/stats",
    summary="Conflict review queue statistics (reviewer only)",
)
async def conflict_stats(
    db: AsyncSession = Depends(get_read_db),
    operator: OperatorContext = Depends(require_reviewer),
) -> dict[str, Any]:
    """
    Summary statistics for the conflict reviewer dashboard.

    Returns counts by status and the most-disputed fields.
    """
    # Counts by status
    status_rows = (await db.execute(
        select(ClaimConflict.status, func.count().label("n"))
        .group_by(ClaimConflict.status)
    )).all()

    # Most-disputed fields (top 10 by open conflict count). The COUNT(*)
    # column is reused for both SELECT and ORDER BY by re-evaluating the
    # function expression — SQLAlchemy emits the same SQL as the previous
    # text("n DESC") form but stays inside the typed expression API.
    field_rows = (await db.execute(
        select(ClaimConflict.field_name, func.count().label("n"))
        .where(ClaimConflict.status == "open")
        .group_by(ClaimConflict.field_name)
        .order_by(func.count().desc())
        .limit(10)
    )).all()

    return {
        "by_status": {row.status: row.n for row in status_rows},
        "top_disputed_fields": [
            {"field": row.field_name, "open_count": row.n} for row in field_rows
        ],
    }


@api_router.get(
    "/api/v1/duplicates",
    response_model=list[DuplicateCandidateOut],
    summary="Duplicate candidate review queue (reviewer only)",
)
async def list_duplicate_candidates(
    status: str = Query("pending", description="Filter by candidate status"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_read_db),
    operator: OperatorContext = Depends(require_reviewer),
) -> list[DuplicateCandidateOut]:
    rows = (await db.execute(
        select(DuplicateCandidateReview)
        .where(DuplicateCandidateReview.status == status)
        .order_by(DuplicateCandidateReview.created_at.asc(), DuplicateCandidateReview.id.asc())
        .limit(limit)
    )).scalars().all()
    return [DuplicateCandidateOut.model_validate(r, from_attributes=True) for r in rows]


@api_router.post(
    "/api/v1/duplicates/{candidate_id}/confirm",
    response_model=DuplicateCandidateOut,
    summary="Confirm a duplicate candidate and merge the source event into the candidate event",
)
async def confirm_duplicate_candidate(
    request: Request,
    candidate_id: str,
    body: DuplicateDecisionIn,
    db: AsyncSession = Depends(get_db),
    operator: OperatorContext = Depends(require_reviewer),
) -> DuplicateCandidateOut:
    cand = await db.get(DuplicateCandidateReview, candidate_id)
    if cand is None:
        raise HTTPException(status_code=404, detail="Duplicate candidate not found")
    if cand.status != "pending":
        raise HTTPException(status_code=409, detail=f"Duplicate candidate is already {cand.status}")
    if not cand.source_event_id:
        raise HTTPException(status_code=422, detail="Candidate has no source_event_id to merge")

    source_event = await db.get(AccidentEvent, cand.source_event_id)
    target_event = await db.get(AccidentEvent, cand.candidate_event_id)
    if source_event is None or target_event is None:
        raise HTTPException(status_code=404, detail="Source or target event no longer exists")

    # Move source-side provenance onto the target event, then mark the source
    # event merged.  Capture moved row IDs first so this reviewer action has a
    # concrete rollback strategy instead of being a one-way mutation.
    moved_claim_ids = list((await db.execute(select(Claim.id).where(Claim.event_id == source_event.id))).scalars().all())
    moved_document_ids = list((await db.execute(select(SourceDocument.id).where(SourceDocument.event_id == source_event.id))).scalars().all())
    moved_revision_ids = list((await db.execute(select(EventRevision.id).where(EventRevision.event_id == source_event.id))).scalars().all())
    moved_conflict_ids = list((await db.execute(select(ClaimConflict.id).where(ClaimConflict.event_id == source_event.id))).scalars().all())
    moved_issue_ids = list((await db.execute(select(DataQualityIssue.id).where(DataQualityIssue.event_id == source_event.id))).scalars().all())

    db.add(DuplicateMergeOperation(
        id=str(uuid.uuid4()),
        duplicate_candidate_id=cand.id,
        source_event_id=source_event.id,
        target_event_id=target_event.id,
        moved_claim_ids=moved_claim_ids,
        moved_document_ids=moved_document_ids,
        moved_revision_ids=moved_revision_ids,
        moved_conflict_ids=moved_conflict_ids,
        moved_issue_ids=moved_issue_ids,
        created_by=operator.id,
    ))

    await db.execute(update(Claim).where(Claim.id.in_(moved_claim_ids)).values(event_id=target_event.id))
    await db.execute(update(SourceDocument).where(SourceDocument.id.in_(moved_document_ids)).values(event_id=target_event.id))
    await db.execute(update(EventRevision).where(EventRevision.id.in_(moved_revision_ids)).values(event_id=target_event.id))
    await db.execute(update(ClaimConflict).where(ClaimConflict.id.in_(moved_conflict_ids)).values(event_id=target_event.id))
    await db.execute(update(DataQualityIssue).where(DataQualityIssue.id.in_(moved_issue_ids)).values(event_id=target_event.id))

    source_event.record_status = "merged"
    source_event.merged_into_id = target_event.id
    source_event.updated_at = datetime.now(tz=UTC)
    cand.status = "confirmed"
    cand.decision_note = body.note
    cand.reviewed_by = operator.id
    cand.reviewed_at = datetime.now(tz=UTC)

    await ProjectionService(db).rebuild_event(target_event.id)
    await db.commit()
    await db.refresh(cand)
    return DuplicateCandidateOut.model_validate(cand, from_attributes=True)


@api_router.post(
    "/api/v1/duplicates/{candidate_id}/undo",
    response_model=DuplicateCandidateOut,
    summary="Undo a previously confirmed duplicate merge when the merge was wrong",
)
async def undo_duplicate_merge(
    candidate_id: str,
    body: DuplicateDecisionIn,
    db: AsyncSession = Depends(get_db),
    operator: OperatorContext = Depends(require_reviewer),
) -> DuplicateCandidateOut:
    cand = await db.get(DuplicateCandidateReview, candidate_id)
    if cand is None:
        raise HTTPException(status_code=404, detail="Duplicate candidate not found")
    if cand.status != "confirmed":
        raise HTTPException(status_code=409, detail=f"Duplicate candidate is {cand.status}, not confirmed")
    op = (await db.execute(
        select(DuplicateMergeOperation)
        .where(DuplicateMergeOperation.duplicate_candidate_id == candidate_id)
        .where(DuplicateMergeOperation.undone_at.is_(None))
        .order_by(DuplicateMergeOperation.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    if op is None:
        raise HTTPException(status_code=409, detail="No reversible merge operation found for this candidate")

    await db.execute(update(Claim).where(Claim.id.in_(op.moved_claim_ids or [])).values(event_id=op.source_event_id))
    await db.execute(update(SourceDocument).where(SourceDocument.id.in_(op.moved_document_ids or [])).values(event_id=op.source_event_id))
    await db.execute(update(EventRevision).where(EventRevision.id.in_(op.moved_revision_ids or [])).values(event_id=op.source_event_id))
    await db.execute(update(ClaimConflict).where(ClaimConflict.id.in_(op.moved_conflict_ids or [])).values(event_id=op.source_event_id))
    await db.execute(update(DataQualityIssue).where(DataQualityIssue.id.in_(op.moved_issue_ids or [])).values(event_id=op.source_event_id))

    source_event = await db.get(AccidentEvent, op.source_event_id)
    if source_event is not None:
        source_event.record_status = "active"
        source_event.merged_into_id = None
        source_event.updated_at = datetime.now(tz=UTC)

    op.undone_by = operator.id
    op.undone_at = datetime.now(tz=UTC)
    op.undo_note = body.note
    cand.status = "undone"
    cand.decision_note = body.note
    cand.reviewed_by = operator.id
    cand.reviewed_at = datetime.now(tz=UTC)

    await ProjectionService(db).rebuild_event(op.source_event_id)
    await ProjectionService(db).rebuild_event(op.target_event_id)
    await db.commit()
    await db.refresh(cand)
    return DuplicateCandidateOut.model_validate(cand, from_attributes=True)


@api_router.post(
    "/api/v1/duplicates/{candidate_id}/reject",
    response_model=DuplicateCandidateOut,
    summary="Reject a duplicate candidate so it is not repeatedly suggested",
)
async def reject_duplicate_candidate(
    request: Request,
    candidate_id: str,
    body: DuplicateDecisionIn,
    db: AsyncSession = Depends(get_db),
    operator: OperatorContext = Depends(require_reviewer),
) -> DuplicateCandidateOut:
    cand = await db.get(DuplicateCandidateReview, candidate_id)
    if cand is None:
        raise HTTPException(status_code=404, detail="Duplicate candidate not found")
    if cand.status != "pending":
        raise HTTPException(status_code=409, detail=f"Duplicate candidate is already {cand.status}")
    cand.status = "rejected"
    cand.decision_note = body.note
    cand.reviewed_by = operator.id
    cand.reviewed_at = datetime.now(tz=UTC)
    await db.commit()
    await db.refresh(cand)
    return DuplicateCandidateOut.model_validate(cand, from_attributes=True)


@api_router.get(
    "/api/v1/data-quality/issues",
    response_model=list[DataQualityIssueOut],
    summary="Data-quality issue queue (reviewer only)",
)
async def list_data_quality_issues(
    status: str = Query("open"),
    issue_code: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_read_db),
    operator: OperatorContext = Depends(require_reviewer),
) -> list[DataQualityIssueOut]:
    stmt = select(DataQualityIssue).where(DataQualityIssue.status == status)
    if issue_code:
        stmt = stmt.where(DataQualityIssue.issue_code == issue_code)
    rows = (await db.execute(
        stmt.order_by(DataQualityIssue.created_at.asc(), DataQualityIssue.id.asc()).limit(limit)
    )).scalars().all()
    return [DataQualityIssueOut.model_validate(r, from_attributes=True) for r in rows]


@api_router.post(
    "/api/v1/data-quality/issues/{issue_id}/resolve",
    response_model=DataQualityIssueOut,
    summary="Resolve a data-quality issue (reviewer only)",
)
async def resolve_data_quality_issue(
    request: Request,
    issue_id: str,
    body: DataQualityResolveIn,
    db: AsyncSession = Depends(get_db),
    operator: OperatorContext = Depends(require_reviewer),
) -> DataQualityIssueOut:
    issue = await db.get(DataQualityIssue, issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail="Data-quality issue not found")
    if issue.status != "open":
        raise HTTPException(status_code=409, detail=f"Issue is already {issue.status}")
    issue.status = "resolved"
    issue.resolved_by = operator.id
    issue.resolved_at = datetime.now(tz=UTC)
    issue.resolution_note = body.note
    await db.commit()
    await db.refresh(issue)
    return DataQualityIssueOut.model_validate(issue, from_attributes=True)


@api_router.get(
    "/api/v1/admin/audit-log",
    summary="Unified admin audit log (admin only)",
)
async def admin_audit_log(
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_read_db),
    operator: OperatorContext = Depends(require_admin),
) -> list[dict[str, Any]]:
    """Return recent reviewer/admin actions from multiple audit tables."""
    items: list[dict[str, Any]] = []

    claim_rows = (await db.execute(
        select(ClaimHistory, Claim)
        .join(Claim, ClaimHistory.claim_id == Claim.id)
        .order_by(ClaimHistory.changed_at.desc())
        .limit(limit)
    )).all()
    for h, claim in claim_rows:
        items.append({
            "kind": "claim_history",
            "id": h.id,
            "occurred_at": h.changed_at,
            "actor": h.changed_by,
            "event_id": claim.event_id,
            "claim_id": h.claim_id,
            "action": h.change_reason,
            "description": f"Claim {h.claim_id} changed from {h.old_claim_type} to {h.new_claim_type}",
        })

    conflict_rows = (await db.execute(
        select(ClaimConflict)
        .where(ClaimConflict.resolved_at.isnot(None))
        .order_by(ClaimConflict.resolved_at.desc())
        .limit(limit)
    )).scalars().all()
    for c in conflict_rows:
        items.append({
            "kind": "conflict_resolution",
            "id": c.id,
            "occurred_at": c.resolved_at,
            "actor": c.resolved_by,
            "event_id": c.event_id,
            "claim_id": c.accepted_claim_id,
            "action": c.resolution_type,
            "description": c.resolution,
        })

    dupe_rows = (await db.execute(
        select(DuplicateCandidateReview)
        .where(DuplicateCandidateReview.reviewed_at.isnot(None))
        .order_by(DuplicateCandidateReview.reviewed_at.desc())
        .limit(limit)
    )).scalars().all()
    for d in dupe_rows:
        items.append({
            "kind": "duplicate_candidate",
            "id": d.id,
            "occurred_at": d.reviewed_at,
            "actor": d.reviewed_by,
            "event_id": d.source_event_id,
            "candidate_event_id": d.candidate_event_id,
            "action": d.status,
            "description": d.decision_note,
        })

    dq_rows = (await db.execute(
        select(DataQualityIssue)
        .where(DataQualityIssue.resolved_at.isnot(None))
        .order_by(DataQualityIssue.resolved_at.desc())
        .limit(limit)
    )).scalars().all()
    for issue in dq_rows:
        items.append({
            "kind": "data_quality_issue",
            "id": issue.id,
            "occurred_at": issue.resolved_at,
            "actor": issue.resolved_by,
            "event_id": issue.event_id,
            "action": issue.status,
            "description": issue.resolution_note,
        })

    archive_rows = (await db.execute(
        select(ArchiveManifest)
        .order_by(ArchiveManifest.created_at.desc())
        .limit(limit)
    )).scalars().all()
    for m in archive_rows:
        items.append({
            "kind": "archive_manifest",
            "id": m.id,
            "occurred_at": m.created_at,
            "actor": m.created_by,
            "event_id": None,
            "action": m.status,
            "description": m.output_uri,
        })

    items.sort(key=lambda x: x.get("occurred_at") or datetime.min.replace(tzinfo=UTC), reverse=True)
    return items[:limit]


@api_router.get(
    "/api/v1/admin/archive/manifests",
    response_model=list[ArchiveManifestOut],
    summary="List archive manifests (admin only)",
)
async def list_archive_manifests(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_read_db),
    operator: OperatorContext = Depends(require_admin),
) -> list[ArchiveManifestOut]:
    rows = (await db.execute(
        select(ArchiveManifest)
        .order_by(ArchiveManifest.created_at.desc(), ArchiveManifest.id.asc())
        .limit(limit)
    )).scalars().all()
    return [ArchiveManifestOut.model_validate(r, from_attributes=True) for r in rows]




def _json_response(obj: Any, *, filename: str) -> Response:
    body = json.dumps(obj, default=str, indent=2, sort_keys=True)
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _csv_response(rows: list[dict[str, Any]], *, filename: str) -> Response:
    buf = io.StringIO()
    fieldnames = sorted({key for row in rows for key in row.keys()}) if rows else ["empty"]
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: ("" if v is None else v) for k, v in row.items()})
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@api_router.get(
    "/api/v1/admin/api-keys",
    response_model=list[ApiKeyOut],
    summary="List API keys without raw secrets (admin only)",
)
async def list_api_keys(
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_read_db),
    operator: OperatorContext = Depends(require_admin),
) -> list[ApiKeyOut]:
    rows = (await db.execute(
        select(ApiKey).order_by(ApiKey.created_at.desc(), ApiKey.id.asc()).limit(limit)
    )).scalars().all()
    return [ApiKeyOut.model_validate(r, from_attributes=True) for r in rows]


@api_router.post(
    "/api/v1/admin/api-keys",
    response_model=ApiKeyCreateOut,
    summary="Create reviewer/admin API key and return raw key once (admin only)",
)
async def create_api_key(
    body: ApiKeyCreateIn,
    db: AsyncSession = Depends(get_db),
    operator: OperatorContext = Depends(require_admin),
) -> ApiKeyCreateOut:
    raw_key = f"atlas_{secrets.token_urlsafe(32)}"
    key = ApiKey(
        id=str(uuid.uuid4()),
        key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
        operator_id=body.operator_id,
        role=body.role,
        is_active=True,
        expires_at=body.expires_at,
        description=body.description,
    )
    db.add(key)
    await db.commit()
    await db.refresh(key)
    return ApiKeyCreateOut(raw_key=raw_key, **ApiKeyOut.model_validate(key, from_attributes=True).model_dump())


@api_router.post(
    "/api/v1/admin/api-keys/{key_id}/revoke",
    response_model=ApiKeyOut,
    summary="Revoke an API key (admin only)",
)
async def revoke_api_key(
    key_id: str,
    db: AsyncSession = Depends(get_db),
    operator: OperatorContext = Depends(require_admin),
) -> ApiKeyOut:
    key = await db.get(ApiKey, key_id)
    if key is None:
        raise HTTPException(status_code=404, detail="API key not found")
    key.is_active = False
    await db.commit()
    await db.refresh(key)
    return ApiKeyOut.model_validate(key, from_attributes=True)


@api_router.get(
    "/api/v1/admin/source-documents",
    response_model=list[SourceDocumentOut],
    summary="List source documents needing operator review (admin only)",
)
async def list_source_documents_admin(
    event_id: str | None = Query(None),
    document_type: str | None = Query(None),
    verified: bool | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_read_db),
    operator: OperatorContext = Depends(require_admin),
) -> list[SourceDocumentOut]:
    stmt = select(SourceDocument)
    if event_id:
        stmt = stmt.where(SourceDocument.event_id == event_id)
    if document_type:
        stmt = stmt.where(SourceDocument.document_type == document_type)
    if verified is not None:
        stmt = stmt.where(SourceDocument.url_verified.is_(verified))
    rows = (await db.execute(
        stmt.order_by(SourceDocument.last_checked_at.asc().nullsfirst(), SourceDocument.id.asc()).limit(limit)
    )).scalars().all()
    return [SourceDocumentOut.model_validate(r, from_attributes=True) for r in rows]


@api_router.post(
    "/api/v1/admin/source-documents/{document_id}/review",
    response_model=SourceDocumentOut,
    summary="Review/update source-document final-report metadata (admin only)",
)
async def review_source_document(
    document_id: str,
    body: SourceDocumentReviewIn,
    db: AsyncSession = Depends(get_db),
    operator: OperatorContext = Depends(require_admin),
) -> SourceDocumentOut:
    doc = await db.get(SourceDocument, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Source document not found")
    if body.document_type is not None:
        doc.document_type = body.document_type
    if body.url_verified is not None:
        doc.url_verified = body.url_verified
    if body.is_available is not None:
        doc.is_available = body.is_available
    doc.last_checked_at = datetime.now(tz=UTC)

    # When an operator verifies a final report, explicitly link that document
    # to existing claims from the same event/source. This makes the final-report
    # support relation auditable instead of implicit.
    if doc.document_type == "final_report" and doc.url_verified:
        claim_ids = list((await db.execute(
            select(Claim.id).where(Claim.event_id == doc.event_id, Claim.source_id == doc.source_id)
        )).scalars().all())
        for claim_id in claim_ids:
            existing = (await db.execute(
                select(ClaimSourceDocument.id).where(
                    ClaimSourceDocument.claim_id == claim_id,
                    ClaimSourceDocument.source_document_id == doc.id,
                ).limit(1)
            )).scalar_one_or_none()
            if existing is None:
                db.add(ClaimSourceDocument(
                    id=str(uuid.uuid4()),
                    claim_id=claim_id,
                    source_document_id=doc.id,
                    link_reason="operator_verified_final_report",
                ))

    await ProjectionService(db).rebuild_event(doc.event_id)
    await db.commit()
    await db.refresh(doc)
    return SourceDocumentOut.model_validate(doc, from_attributes=True)


@api_router.get(
    "/api/v1/admin/archive/manifests/{manifest_id}/verify",
    summary="Verify an archive manifest checksum/signature (admin only)",
)
async def verify_archive_manifest_api(
    manifest_id: str,
    db: AsyncSession = Depends(get_read_db),
    operator: OperatorContext = Depends(require_admin),
) -> dict[str, Any]:
    from atlas.retention.archive import verify_archive_manifest

    manifest = await db.get(ArchiveManifest, manifest_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="Archive manifest not found")
    return verify_archive_manifest(manifest.output_uri)


@api_router.get("/api/v1/export/accidents.csv", summary="Export accident projection as CSV (admin only)")
async def export_accidents_csv(
    limit: int = Query(10000, ge=1, le=100000),
    db: AsyncSession = Depends(get_read_db),
    operator: OperatorContext = Depends(require_admin),
) -> Response:
    rows = (await db.execute(
        select(AccidentRecord, AccidentEvent)
        .join(AccidentEvent, AccidentRecord.id == AccidentEvent.id)
        .where(AccidentEvent.record_status == "active")
        .order_by(AccidentRecord.occurred_at.desc().nullslast(), AccidentRecord.id.asc())
        .limit(limit)
    )).all()
    return _csv_response([
        {
            "event_id": evt.id,
            "canonical_id": evt.canonical_id,
            "occurred_date": rec.occurred_date,
            "occurred_year": rec.occurred_year,
            "location_text": rec.location_text,
            "country_code": rec.country_code,
            "aircraft_registration": rec.aircraft_registration,
            "aircraft_make": rec.aircraft_make,
            "aircraft_model": rec.aircraft_model,
            "operator_name": rec.operator_name,
            "injury_severity": rec.injury_severity,
            "fatalities_total": rec.fatalities_total,
            "fatalities_crew": rec.fatalities_crew,
            "fatalities_passengers": rec.fatalities_passengers,
            "has_conflicts": rec.has_conflicts,
            "source_completeness_score": float(rec.confidence_score) if rec.confidence_score is not None else None,
        }
        for rec, evt in rows
    ], filename="atlas-accidents.csv")


@api_router.get("/api/v1/export/conflicts.csv", summary="Export conflict queue as CSV (admin only)")
async def export_conflicts_csv(
    status: str | None = Query(None),
    limit: int = Query(10000, ge=1, le=100000),
    db: AsyncSession = Depends(get_read_db),
    operator: OperatorContext = Depends(require_admin),
) -> Response:
    stmt = select(ClaimConflict)
    if status:
        stmt = stmt.where(ClaimConflict.status == status)
    rows = (await db.execute(stmt.order_by(ClaimConflict.created_at.desc(), ClaimConflict.id.asc()).limit(limit))).scalars().all()
    return _csv_response([
        {
            "id": c.id,
            "event_id": c.event_id,
            "field_name": c.field_name,
            "claim_a_id": c.claim_a_id,
            "claim_b_id": c.claim_b_id,
            "status": c.status,
            "resolution_type": c.resolution_type,
            "accepted_claim_id": c.accepted_claim_id,
            "resolved_by": c.resolved_by,
            "resolved_at": c.resolved_at,
            "created_at": c.created_at,
        }
        for c in rows
    ], filename="atlas-conflicts.csv")


@api_router.get("/api/v1/export/data-quality.csv", summary="Export data-quality issue queue as CSV (admin only)")
async def export_data_quality_csv(
    status: str | None = Query(None),
    limit: int = Query(10000, ge=1, le=100000),
    db: AsyncSession = Depends(get_read_db),
    operator: OperatorContext = Depends(require_admin),
) -> Response:
    stmt = select(DataQualityIssue)
    if status:
        stmt = stmt.where(DataQualityIssue.status == status)
    rows = (await db.execute(stmt.order_by(DataQualityIssue.created_at.desc(), DataQualityIssue.id.asc()).limit(limit))).scalars().all()
    return _csv_response([
        {
            "id": i.id,
            "event_id": i.event_id,
            "source_id": i.source_id,
            "issue_code": i.issue_code,
            "field_name": i.field_name,
            "severity": i.severity,
            "status": i.status,
            "details": json.dumps(i.details or {}, sort_keys=True),
            "created_at": i.created_at,
            "resolved_at": i.resolved_at,
            "resolved_by": i.resolved_by,
        }
        for i in rows
    ], filename="atlas-data-quality.csv")


@api_router.get("/api/v1/export/provenance/{event_id}.json", summary="Export event provenance as JSON (admin only)")
async def export_provenance_json(
    event_id: str,
    request: Request,
    db: AsyncSession = Depends(get_read_db),
    operator: OperatorContext = Depends(require_admin),
) -> Response:
    payload = await get_provenance(request=request, event_id=event_id, db=db)
    return _json_response(payload.model_dump(mode="json"), filename=f"atlas-provenance-{event_id}.json")


@api_router.get("/api/v1/admin/archive/manifests/{manifest_id}/export", summary="Export archive manifest JSON (admin only)")
async def export_archive_manifest_json(
    manifest_id: str,
    db: AsyncSession = Depends(get_read_db),
    operator: OperatorContext = Depends(require_admin),
) -> Response:
    manifest = await db.get(ArchiveManifest, manifest_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="Archive manifest not found")
    return _json_response(ArchiveManifestOut.model_validate(manifest, from_attributes=True).model_dump(mode="json"), filename=f"archive-manifest-{manifest_id}.json")


@api_router.post(
    "/api/v1/admin/events/{event_id}/force-resolve-field",
    summary="Force-resolve a contradicted field (admin only)",
    description=(
        "Override a contradiction where `finalize_accepted_claims_for_field` "
        "detected that the same claim was accepted in one conflict and rejected "
        "in another.  Only `admin` role may call this endpoint.\n\n"
        "Provide the canonical `accepted_claim_id` for the field.  All open "
        "conflicts for the field are marked `resolved` with `manual_override`, "
        "and the designated claim is restored to its pre-dispute type."
    ),
)
async def admin_force_resolve_field(
    request: Request,
    event_id: str,
    field_name: str = Query(...),
    accepted_claim_id: str = Query(...),
    resolution: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    operator: OperatorContext = Depends(require_admin),
) -> dict[str, Any]:
    """Admin override for contradicted fields. Requires real admin auth."""
    # Defence in depth for direct unit invocation: require_admin already enforces
    # this for HTTP requests and never returns an auth-disabled sentinel.
    if operator.role != "admin" or not operator.id:
        raise HTTPException(
            403,
            f"Force-resolve requires an audited 'admin' operator. Current role: {operator.role!r}",
        )

    operator_id = operator.id
    now = datetime.now(tz=UTC)

    # Mark all open conflicts for this event+field as resolved via manual_override
    result = await db.execute(
        update(ClaimConflict)
        .where(
            ClaimConflict.event_id == event_id,
            ClaimConflict.field_name == field_name,
            ClaimConflict.status == "open",
        )
        .values(
            status="resolved",
            resolution_type="manual_override",
            accepted_claim_id=accepted_claim_id,
            resolution=resolution or f"Admin force-resolved by {operator_id}",
            resolved_by=operator_id,
            resolved_at=now,
        )
        .returning(ClaimConflict.id)
    )
    resolved_ids = [row[0] for row in result.all()]
    if not resolved_ids:
        raise HTTPException(
            404,
            f"No open conflicts found for event {event_id!r} field {field_name!r}",
        )

    await db.flush()

    # Restore the designated claim
    accepted_claim: Claim | None = await db.get(Claim, accepted_claim_id)
    if accepted_claim and accepted_claim.claim_type not in (
        ClaimType.CONFIRMED.value, ClaimType.INFERRED.value
    ):
        old_type = accepted_claim.claim_type
        accepted_claim.claim_type = ClaimType.CONFIRMED.value
        db.add(ClaimHistory(
            id=str(uuid.uuid4()),
            claim_id=accepted_claim.id,
            old_claim_type=old_type,
            new_claim_type=ClaimType.CONFIRMED.value,
            change_reason=f"admin_force_resolve:{event_id}:{field_name}",
            changed_by=operator_id,
        ))

    await db.flush()

    try:
        await ProjectionService(session=db).rebuild_event(event_id)
    except Exception as exc:
        raise HTTPException(500, f"Projection rebuild failed: {exc}") from exc

    return {
        "resolved_conflict_count": len(resolved_ids),
        "resolved_conflict_ids": resolved_ids,
        "accepted_claim_id": accepted_claim_id,
        "event_id": event_id,
        "field_name": field_name,
        "operator": operator_id,
    }


@api_router.get("/api/v1/accidents/{event_id}", response_model=AccidentDetail)
async def get_accident(event_id: str, db: AsyncSession = Depends(get_read_db)):
    pair = (await db.execute(
        select(AccidentRecord, AccidentEvent)
        .join(AccidentEvent, AccidentRecord.id == AccidentEvent.id)
        .where(AccidentEvent.id == event_id, AccidentEvent.record_status == "active")
    )).first()
    if not pair:
        raise HTTPException(404, f"Event {event_id!r} not found")

    record, event = pair
    summary = _to_summary(record, event)
    return AccidentDetail(
        **summary.model_dump(),
        probable_cause=record.probable_cause,
        contributing_factors=record.contributing_factors,
        ntsb_report_number=record.ntsb_report_number,
        weather_condition=record.weather_condition,
        purpose_of_flight=record.purpose_of_flight,
        aircraft_registration=record.aircraft_registration,
        aircraft_amateur_built=record.aircraft_amateur_built,
        serious_injuries=record.serious_injuries,
        minor_injuries=record.minor_injuries,
        state_code=record.state_code,
        last_projected_at=record.last_projected_at,
        # v20: pass through the backend-computed aggregate document
        # state.  Older records may have this as null; the frontend
        # falls back to deriving from the docs array.
        document_status=getattr(record, "document_status", None),
        # Override confidence to include full breakdown
        confidence=_conf_out(record.confidence_score, record.confidence_breakdown),
    )


@api_router.get("/api/v1/accidents/{event_id}/provenance", response_model=AccidentProvenance)
async def get_provenance(request: Request, event_id: str, db: AsyncSession = Depends(get_read_db)) -> AccidentProvenance:
    """
    Full claim-level provenance. is_winning is now accurate (set by
    ProjectionService during ingest) so winning_claims is non-empty.
    display_value is decoded from the JSONB envelope for the UI.

    Sub-sections are capped at the configured limits (provenance_claim_limit,
    provenance_conflict_limit, provenance_document_limit).  The `truncation`
    field in the response indicates which sections were capped and at what size.
    """
    event = (await db.execute(
        select(AccidentEvent)
        .where(AccidentEvent.id == event_id, AccidentEvent.record_status == "active")
    )).scalar_one_or_none()
    if not event:
        raise HTTPException(404, f"Event {event_id!r} not found")

    # Caps come from settings so they are tunable and testable without code changes.
    # Local name `settings` (shadowing the module-level singleton inside this
    # function only) keeps the lookup readable and avoids the previous `s` which
    # also got reused as a Source ORM row inside the comprehensions below.
    settings = _app_settings(request)
    claim_limit    = settings.provenance_claim_limit
    conflict_limit = settings.provenance_conflict_limit
    doc_limit      = settings.provenance_document_limit

    claims_raw = (await db.execute(
        select(Claim, Source)
        .join(Source, Claim.source_id == Source.id)
        .where(Claim.event_id == event_id)
        .order_by(Claim.field_name.asc(), Claim.created_at.desc(), Claim.id.asc())
        .limit(claim_limit + 1)
    )).all()
    claims_truncated = len(claims_raw) > claim_limit
    claims_with_sources = claims_raw[:claim_limit]

    claims_out = [
        ClaimOut(
            id=c.id, field_name=c.field_name, field_value=c.field_value,
            display_value=cv.display(c.field_value),
            claim_type=c.claim_type,
            confidence=float(c.confidence) if c.confidence else None,
            source_id=c.source_id, source_short_name=s.short_name,
            snapshot_id=c.snapshot_id, effective_at=c.effective_at,
            is_winning=c.is_winning, notes=c.notes,
        )
        for c, s in claims_with_sources
    ]

    conflicts_raw = (await db.execute(
        select(ClaimConflict)
        .where(ClaimConflict.event_id == event_id)
        # Open conflicts first (most actionable), then by recency, then stable by id.
        # The CASE expression sorts open=0 before closed/resolved=1.
        .order_by(
            case((ClaimConflict.status == "open", 0), else_=1).asc(),
            ClaimConflict.created_at.desc(),
            ClaimConflict.id.asc(),
        )
        .limit(conflict_limit + 1)
    )).scalars().all()
    conflicts_truncated = len(conflicts_raw) > conflict_limit
    conflicts_raw = conflicts_raw[:conflict_limit]

    conflicts_out = [
        ConflictOut(
            id=cc.id, field_name=cc.field_name,
            claim_a_id=cc.claim_a_id, claim_b_id=cc.claim_b_id,
            status=getattr(cc, "status", "open") or "open",
            resolution=cc.resolution, resolved_at=cc.resolved_at,
            resolution_type=getattr(cc, "resolution_type", None),
            accepted_claim_id=getattr(cc, "accepted_claim_id", None),
            rejected_claim_ids=getattr(cc, "rejected_claim_ids", None),
            obsolete_reason=getattr(cc, "obsolete_reason", None),
            resolved_by=getattr(cc, "resolved_by", None),
        )
        for cc in conflicts_raw
    ]

    docs_raw = (await db.execute(
        select(SourceDocument)
        .where(SourceDocument.event_id == event_id)
        # Most recently published documents first; stable by id when date is equal.
        .order_by(
            SourceDocument.published_at.desc().nullslast(),
            SourceDocument.last_checked_at.desc().nullslast(),
            SourceDocument.id.asc(),
        )
        .limit(doc_limit + 1)
    )).scalars().all()
    docs_truncated = len(docs_raw) > doc_limit
    docs = docs_raw[:doc_limit]

    docs_out = [
        SourceDocumentOut(
            id=d.id, event_id=d.event_id, source_id=d.source_id, document_type=d.document_type,
            url=d.url, url_verified=d.url_verified, title=d.title,
            published_at=d.published_at, is_available=d.is_available,
            last_checked_at=d.last_checked_at,
            last_http_status=getattr(d, "last_http_status", None),
            last_check_error=getattr(d, "last_check_error", None),
            last_check_method=getattr(d, "last_check_method", None),
        )
        for d in docs
    ]

    # Collect source IDs from every returned section so sources_out is complete.
    # This is a two-pass approach: first collect all IDs, then fetch in one query.
    all_source_ids: set[str] = set()

    # From claims
    for c, _ in claims_with_sources:
        if c.source_id:
            all_source_ids.add(c.source_id)

    # From source documents — a doc can reference a source not in the (capped) claims list
    for d in docs:
        if d.source_id:
            all_source_ids.add(d.source_id)

    # From conflict claim pairs — conflicts reference claim A and claim B; those
    # claims may have sources not present in the returned (capped) claims list.
    # We fetch just the source_id column to avoid loading full claim objects.
    conflict_claim_ids: set[str] = set()
    for cc in conflicts_raw:
        if cc.claim_a_id:
            conflict_claim_ids.add(cc.claim_a_id)
        if cc.claim_b_id:
            conflict_claim_ids.add(cc.claim_b_id)

    if conflict_claim_ids:
        conflict_source_rows = (await db.execute(
            select(Claim.source_id)
            .where(Claim.id.in_(conflict_claim_ids))
            .where(Claim.source_id.isnot(None))
        )).scalars().all()
        all_source_ids.update(conflict_source_rows)

    # Timeline rows are loaded before source metadata so revision-only sources
    # are also represented in sources_out.  Without this, a revision can carry a
    # source_id but render with source_short_name=null because its source was not
    # mentioned by returned claims/documents/conflicts.
    revisions = (await db.execute(
        select(EventRevision)
        .where(EventRevision.event_id == event_id)
        .order_by(EventRevision.occurred_at.desc(), EventRevision.id.asc())
        .limit(200)
    )).scalars().all()
    for r in revisions:
        if r.source_id:
            all_source_ids.add(r.source_id)

    source_ids = list(all_source_ids)
    sources_out: list[SourceOut] = []
    sources_by_id: dict[str, Source] = {}
    if source_ids:
        sources_loaded = (await db.execute(
            select(Source).where(Source.id.in_(source_ids))
        )).scalars().all()
        sources_by_id = {s.id: s for s in sources_loaded}
        sources_out = [
            SourceOut(
                id=s.id, short_name=s.short_name, display_name=s.display_name,
                tier=s.tier, license_type=s.license_type,
                base_url=s.base_url, description=s.description,
            )
            for s in sources_loaded
        ]

    # v20: per-field projection rationale.  Stored on the projection
    # row so we don't recompute on every request.  May be null for
    # records that haven't been rebuilt under v20 yet — return [] in
    # that case (the frontend treats absent/empty as "no explanations").
    record = await db.get(AccidentRecord, event_id)
    projections_out: list[ProjectionExplanationOut] = []
    if record is not None and record.projection_explanations:
        for raw in record.projection_explanations:
            try:
                projections_out.append(ProjectionExplanationOut(**raw))
            except Exception:
                # Defensive: skip rows that don't match the current
                # shape rather than 500-ing the whole response.
                continue

    # v20: real timeline rows.  Capped to the most recent N entries to
    # keep payload size sane on records with long histories.
    revisions_out = [
        EventRevisionOut(
            id=r.id,
            event_id=r.event_id,
            revision_type=r.revision_type,
            occurred_at=r.occurred_at,
            source_id=r.source_id,
            source_short_name=(
                sources_by_id[r.source_id].short_name
                if r.source_id and r.source_id in sources_by_id else None
            ),
            field_names=list(r.field_names) if r.field_names else None,
            description=r.description,
        )
        for r in revisions
    ]

    dq_rows = (await db.execute(
        select(DataQualityIssue)
        .where(DataQualityIssue.event_id == event_id)
        .order_by(DataQualityIssue.status.asc(), DataQualityIssue.created_at.desc(), DataQualityIssue.id.asc())
        .limit(100)
    )).scalars().all()
    dq_out = [DataQualityIssueOut.model_validate(row, from_attributes=True) for row in dq_rows]

    # Emit per-section metrics so operators can see which events are hitting caps.
    if claims_truncated:    _provenance_truncation_total.labels(section="claims").inc()
    if conflicts_truncated: _provenance_truncation_total.labels(section="conflicts").inc()
    if docs_truncated:      _provenance_truncation_total.labels(section="source_documents").inc()

    return AccidentProvenance(
        event_id=event_id,
        claims=claims_out, conflicts=conflicts_out,
        source_documents=docs_out, sources=sources_out,
        projections=projections_out,
        revisions=revisions_out,
        data_quality_issues=dq_out,
        truncation=ProvenanceTruncationOut(
            claims=claims_truncated,
            conflicts=conflicts_truncated,
            source_documents=docs_truncated,
            claims_limit=claim_limit,
            conflicts_limit=conflict_limit,
            source_documents_limit=doc_limit,
        ),
    )




@api_router.get("/api/v1/public/accidents/{event_id}/transparency", summary="Public transparency view for an accident event")
async def public_transparency_view(
    request: Request,
    event_id: str,
    db: AsyncSession = Depends(get_read_db),
) -> dict[str, Any]:
    """Public-safe transparency subset: open disputes, quality warnings, documents, and projection reasons."""
    prov = await get_provenance(request=request, event_id=event_id, db=db)
    return {
        "event_id": prov.event_id,
        "open_conflicts": [c.model_dump(mode="json") for c in prov.conflicts if c.status == "open"],
        "data_quality_warnings": [i.model_dump(mode="json") for i in prov.data_quality_issues if i.status == "open"],
        "source_documents": [d.model_dump(mode="json") for d in prov.source_documents],
        "sources": [s.model_dump(mode="json") for s in prov.sources],
        "projection_explanations": [p.model_dump(mode="json") for p in prov.projections],
        "truncation": prov.truncation.model_dump(mode="json") if prov.truncation else None,
    }


@api_router.post(
    "/api/v1/conflicts/{conflict_id}/resolve",
    response_model=ConflictOut,
    summary="Resolve an open claim conflict",
    description=(
        "Mark an open field-level conflict as resolved and trigger a projection "
        "rebuild for the affected event.\n\n"
        "**Authentication**: pass a reviewer API key in the `X-API-Key` header "
        "when `API_AUTH_ENABLED=true`.  When auth is disabled (local dev), the "
        "`resolved_by` field in the request body identifies the operator.\n\n"
        "**Resolution types**: `claim_accepted` requires `accepted_claim_id`; "
        "`claim_rejected` requires `rejected_claim_ids` (survivor auto-derived); "
        "other types close the conflict without designating a winner.\n\n"
        "Rejected claims are moved to the `rejected` claim type so they are "
        "permanently excluded from projection."
    ),
)
async def resolve_conflict(
    request: Request,
    conflict_id: str,
    body: ConflictResolveIn,
    db: AsyncSession = Depends(get_db),
    operator: OperatorContext = Depends(require_reviewer),
) -> ConflictOut:
    """
    Thin route adapter — all invariants live in ConflictResolutionService.

    Operator identity:
      - When auth enabled: operator.id from authenticated API key.
      - When auth disabled: body.resolved_by (falls back to 'anonymous').
    """
    operator_id = operator.id if operator.id else (body.resolved_by or "anonymous")

    svc = ConflictResolutionService(session=db)
    try:
        conflict = await svc.resolve(
            conflict_id=conflict_id,
            resolution_type=body.resolution_type,  # type: ignore[arg-type]
            operator_id=operator_id,
            accepted_claim_id=body.accepted_claim_id,
            rejected_claim_ids=body.rejected_claim_ids,
            resolution=body.resolution,
        )
    except ConflictNotFoundError as e:
        raise HTTPException(404, f"Conflict {conflict_id!r} not found") from e
    except ConflictAlreadyResolvedError as e:
        raise HTTPException(
            409,
            f"Conflict {conflict_id!r} is already {e.status!r}. "
            "Re-resolving a non-open conflict is not permitted.",
        ) from e
    except ConflictValidationError as e:
        raise HTTPException(422, str(e)) from e
    except ProjectionRebuildError as e:
        _projection_rebuilds_total.labels(outcome="error").inc()
        raise HTTPException(
            500,
            f"Resolution was not saved because the projection rebuild failed: {e}. "
            f"Retry the request or run `atlas reproject --event-id <event_id>`.",
        ) from e

    _conflict_resolutions_total.labels(
        resolution_type=conflict.resolution_type or "unknown"
    ).inc()
    _projection_rebuilds_total.labels(outcome="ok").inc()

    return ConflictOut(
        id=conflict.id,
        field_name=conflict.field_name,
        claim_a_id=conflict.claim_a_id,
        claim_b_id=conflict.claim_b_id,
        status=conflict.status,
        resolution=conflict.resolution,
        resolved_at=conflict.resolved_at,
        resolution_type=conflict.resolution_type,
        accepted_claim_id=conflict.accepted_claim_id,
        rejected_claim_ids=conflict.rejected_claim_ids,
        obsolete_reason=getattr(conflict, "obsolete_reason", None),
        resolved_by=conflict.resolved_by,
    )


# Module-level ASGI application used by uvicorn and legacy imports.
app = create_app(settings)
