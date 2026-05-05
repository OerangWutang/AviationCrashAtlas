"""
Central configuration. All settings come from environment variables.
Never hardcode credentials. Use .env for local dev.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = Field(
        default="postgresql+asyncpg://atlas:atlas@localhost:5432/atlas",
    )
    database_pool_size: int = 10
    database_pool_max_overflow: int = 20

    # ── API ───────────────────────────────────────────────────────────────────
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_debug: bool = False
    api_title: str = "Aviation Safety Atlas API"
    api_version: str = "0.1.0"

    # ── NTSB ──────────────────────────────────────────────────────────────────
    ntsb_api_base: str = "https://data.ntsb.gov/carol-main-public/api"
    ntsb_request_delay_s: float = 1.0
    ntsb_max_retries: int = 3
    ntsb_timeout_s: float = 30.0
    ntsb_batch_size: int = 100

    # ── Map endpoint ──────────────────────────────────────────────────────────
    # Hard cap on accidents returned by /api/v1/accidents/map.  Without this
    # the endpoint returns the entire geocoded dataset in one shot — a memory
    # and browser-tab killer on large datasets.  5 000 covers a full global
    # map at typical aviation dataset sizes.  Raise only after adding spatial
    # clustering.  The response includes a `truncated` flag when the cap is hit.
    max_map_results: int = 5000
    # At zoom levels <= this value, /api/v1/accidents/map returns grid clusters
    # instead of individual points. This keeps low-zoom/global map views small
    # and useful while high-zoom views continue to return clickable accidents.
    map_cluster_max_zoom: int = 6

    # ── Confidence weights (all tunable without code changes) ─────────────────
    # Must satisfy tier1 >= tier2 >= tier3 >= tier4 — validated at startup.
    # Bug in pre-v28.1: tier2 was 0.80 and tier3 was 0.90 (inverted), causing
    # lower-authority sources to receive a higher completeness bonus.
    conf_weight_tier1: float = 1.00
    conf_weight_tier2: float = 0.90  # was 0.80 (inverted with tier3 — now fixed)
    conf_weight_tier3: float = 0.80  # was 0.90 (inverted with tier2 — now fixed)
    conf_weight_tier4: float = 0.60
    conf_penalty_preliminary: float = 0.20
    conf_bonus_multi_source: float = 0.10
    conf_penalty_missing_location: float = 0.10
    conf_penalty_missing_date: float = 0.15
    conf_penalty_unresolved_conflict: float = 0.15

    # ── Provenance response caps ──────────────────────────────────────────────
    # Hard limits on how many items each sub-section of the provenance response
    # may contain.  A contested event with many ingestion runs can accumulate
    # hundreds of claims; loading them all in one shot produces a huge response.
    # These are tunable so operators can raise them without a code change.
    provenance_claim_limit: int = 200
    provenance_conflict_limit: int = 200
    provenance_document_limit: int = 100

    # ── Analytics summary cache ───────────────────────────────────────────────
    # Analytics data changes only at ingestion time.  Cache the summary for
    # this many seconds to avoid five aggregation queries per dashboard refresh.
    # Set to 0 in tests to disable caching.
    analytics_cache_ttl_s: int = 60

    # ── Search ────────────────────────────────────────────────────────────────
    # Maximum length of the free-text search query parameter.  Without a cap,
    # a huge search string is passed to five LIKE predicates.
    search_q_max_length: int = 200
    # ── Rate limiting ─────────────────────────────────────────────────────────
    # Set rate_limit_enabled=false in tests and local dev to skip limits.
    # Storage URL should be a Redis URI in production for multi-worker deployments.
    rate_limit_enabled: bool = True
    rate_limit_storage_url: str | None = None   # None → in-memory (single worker only)
    rate_limit_default: str = "120/minute"
    rate_limit_map: str = "30/minute"
    rate_limit_analytics: str = "30/minute"
    rate_limit_provenance: str = "60/minute"
    rate_limit_mutations: str = "30/minute"     # conflict resolution + admin endpoints

    # ── Metrics endpoint ──────────────────────────────────────────────────────
    # Optional bearer token to protect /metrics from public access.
    # Leave unset for internal/firewall-protected deployments.
    # Set to a random secret in production if /metrics is on a public interface.
    metrics_token: str | None = None
    # Set to true to acknowledge /metrics is intentionally public (behind a
    # network policy).  Prevents the production startup guard from triggering.
    metrics_public_ok: bool = False

    # ── Environment ───────────────────────────────────────────────────────────
    # Set APP_ENV=production in production deployments.  When set, the app
    # refuses to start if API_AUTH_ENABLED=false — a warning in the logs is
    # not a sufficient control when auth is the only gate on write endpoints.
    app_env: str = "development"  # "production" | "development" | "test"
    # Set to True in production.  When False, any request is allowed through
    # and resolved_by falls back to the request-body value (useful for local
    # dev and the test suite).
    api_auth_enabled: bool = False
    # Header name used to pass the API key (case-insensitive in FastAPI).
    api_key_header: str = "X-API-Key"

    # ── CORS ──────────────────────────────────────────────────────────────────
    # Comma-separated origins, e.g. "http://localhost:3000,https://app.example.com"
    # Defaults to localhost:3000 for local dev. Set to "*" only if intentional.
    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "console"

    @field_validator("database_url", mode="before")
    @classmethod
    def coerce_dsn(cls, v: object) -> object:
        if isinstance(v, str) and v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    @model_validator(mode="after")
    def validate_tier_weights(self) -> Settings:
        """
        Tier weights must be monotonically non-increasing from tier1 to tier4.
        Violating this causes lower-authority sources to receive a higher
        completeness bonus than higher-authority ones — a silent scoring bug.
        Fail fast at startup so misconfigured deployments surface immediately.
        """
        weights = [
            self.conf_weight_tier1,
            self.conf_weight_tier2,
            self.conf_weight_tier3,
            self.conf_weight_tier4,
        ]
        if weights != sorted(weights, reverse=True):
            raise ValueError(
                f"Confidence tier weights must satisfy tier1 >= tier2 >= tier3 >= tier4. "
                f"Got: tier1={weights[0]}, tier2={weights[1]}, "
                f"tier3={weights[2]}, tier4={weights[3]}"
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
