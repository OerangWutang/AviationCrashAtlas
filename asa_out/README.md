# Aviation Safety Atlas

A claim-based aviation accident data platform that records every claim,
its source, and the disagreements between sources — and refuses to
project a confident value when sources disagree.

The platform is built around an explicit principle: **never display
more certainty than the underlying data supports.** Field values are
projected from winning claims; disputes withhold the projected value;
documents and links are only shown as verified after a successful HTTP
check; and timeline entries are recorded by the ingestion pipeline
rather than synthesised on the fly.

## What it does

Aviation Safety Atlas aggregates accident records from multiple authoritative
sources (NTSB, ASN, etc.) and stores them as **field-level claims** rather than
flat rows. When sources disagree — different fatality counts, conflicting
locations, revised dates — the platform records the conflict explicitly and
presents a scored "winning" projection alongside the full provenance trail.

The data model:

```
raw_snapshots  →  claims  →  (conflict detection)  →  accident_records (read projection)
                    ↑                                         ↑
               ClaimHistory                         confidence_breakdown
               (audit trail)
```

`accident_records` is a **derived read model**, never a source of truth.
The claims table is the truth store.

---

## Recent changes

**Multi-source ingestion**, **field-level conflict resolution**,
**reviewer authentication**, and a **global conflict queue** all landed
in releases since the v20 honesty pass.  The full release history,
including the foundational v20 invariants this codebase still enforces,
lives in [CHANGELOG.md](CHANGELOG.md).

The product principles below have not changed:

- **Open disputes never fabricate a winner.**  The projection withholds
  the field; the UI never invents a value or justification.
- **Selection rationale is backend-supplied.**  `ProjectionService`
  emits structured `selection_reason` codes; the frontend humanises
  them but never invents them.
- **Documents are linked but unverified by default** — a separate
  `atlas check-links` step verifies URLs before they are labelled
  *Verified*.
- **Field status reflects evidence, not investigation maturity.**
  Final-report status does not promote every field to *Confirmed*.
- **Official source does not mean final report.**
  `selected_official_final` is emitted only when a tier-1 source has a
  verified, available final-report document linked for that event; otherwise
  official winners use the weaker latest-official rationale.
- **Timeline entries come from `event_revisions`,** written by the
  ingestion pipeline.  The UI does not synthesise timeline entries.
- **One active claim per (event, source, field)** — Postgres-enforced
  via a partial unique index.

---

## Operational hardening additions

This build adds concrete operational workflows beyond the original claim model:

- duplicate candidate review APIs for ambiguous cross-source matches
- crew/passenger split consistency warnings via data-quality issues
- archive planning/export CLI commands backed by `archive_manifests`
- database-backed Prometheus gauges for ingestion freshness, open conflicts, duplicate candidates, and data-quality issues
- ingestion performance timing with `scripts/performance_ingest.py`

Useful commands:

```bash
PYTHONPATH=src atlas archive plan --cutoff-days 730
PYTHONPATH=src atlas archive run --output-dir .generated/archive --cutoff-days 730
make perf-ingest-smoke
```

Reviewer-only endpoints now include:

```text
GET  /api/v1/duplicates
POST /api/v1/duplicates/{id}/confirm
POST /api/v1/duplicates/{id}/reject
GET  /api/v1/data-quality/issues
POST /api/v1/data-quality/issues/{id}/resolve
GET  /api/v1/admin/audit-log
GET  /api/v1/admin/archive/manifests
```

---

## Architecture

| Layer | Technology |
|---|---|
| API | FastAPI + asyncpg + SQLAlchemy (async) |
| DB | PostgreSQL 15 + PostGIS |
| Migrations | Alembic + psycopg2 (sync, migration-only) |
| Frontend | Next.js + TypeScript + Tailwind |
| Ingestion | Python adapters (NTSB API + CSV) |

---

## Local setup

### Prerequisites

- Docker and Docker Compose
- Python 3.11+
- Node.js 18+

### 1. Start the database

```bash
docker compose up db -d
# Wait for the health check to pass (≈10 seconds)
docker compose ps
```

### 2. Run migrations

Migrations create the full schema **and** seed the required source registry rows
(including the NTSB source that ingestion depends on as a FK target).

```bash
docker compose run --rm migrate
# This runs: atlas db migrate && atlas db seed
# atlas db migrate shells out to: alembic upgrade head
```

To run migrations locally against the default localhost DB:

```bash
pip install -e ".[dev]"
alembic upgrade head
atlas db seed   # seeds additional sources not in the migration
```

**Setup order matters:**
```
1. Start DB
2. Run migrations (creates schema + seeds NTSB source)
3. (Optional) Run ingestion
4. Start API
5. Start frontend
```

Skipping or reordering these steps will produce FK violations or empty results.

### 3. Start the API

```bash
docker compose up api
# API available at http://localhost:8000
# Docs at http://localhost:8000/docs
```

### 4. Start the frontend

```bash
cd web
npm install
npm run dev
# Frontend at http://localhost:3000
```

The frontend uses **mock mode** when `NEXT_PUBLIC_USE_MOCK=true` is set.
Mock mode must be explicitly enabled; the frontend does **not** automatically
fall back to mock data when the API is unavailable.

**Mock mode coverage:**

| Feature | Mock mode | Backend required |
|---|---|---|
| Accident search/list | ✓ hardcoded 11 records | — |
| Accident detail | ✓ 2 records (evt-001, evt-002) | — |
| Provenance panel | ✓ 1 record (evt-001) | — |
| Map view | ✗ shows empty map | ✓ |
| Analytics dashboard | ✗ shows error message | ✓ |

The map and analytics pages require the backend because they return
full-dataset aggregates that cannot be reasonably hardcoded.

---

## Ingestion

### NTSB API (date range)

```bash
atlas ingest ntsb --start 2023-01-01 --end 2023-12-31
```

### NTSB CSV export

```bash
atlas ingest csv /path/to/AviationData.csv
```

Ingestion is idempotent: records are deduplicated via `payload_hash` (SHA-256
of the raw source payload). Re-running ingestion for the same date range is
safe.

### What ingestion does

For each source record:

1. Computes `payload_hash` — skips if already in `raw_snapshots`
2. Normalises raw fields to Python-typed canonical values. Adapters must not
   pre-encode claim envelopes.
3. Finds or creates an `AccidentEvent` (keyed by `canonical_id`)
4. Writes one `Claim` per field via `ClaimWriter`, the single boundary that
   encodes values into the JSONB claim envelope format
5. Detects conflicts against claims from other sources for the same event+field
6. Runs `ProjectionService.rebuild_event()` — selects winning claims and
   writes the `accident_records` projection
7. Commits

---

## Projection and source completeness scoring

**Winner selection priority** (ascending = better):

1. Claim type: `confirmed` > `inferred` — only these two types are eligible to
   become winners. `pending` (unreviewed) and `disputed` (conflicting) claims
   are **never projected** into `accident_records`.
2. Source tier: tier 1 (official NTSB) > tier 4 (unverified)
3. Recency: newer wins on equal type + tier

Projection rationale is deliberately stricter than winner selection. A tier-1
source can win because it is official, but the backend only emits
`selected_official_final` when a verified, available `final`/`final_report`
`SourceDocument` exists for that same source. Otherwise the explanation remains
`selected_latest_official`, avoiding a false claim that a preliminary official
record is a final report.

If a field has only `pending` or `disputed` claims, that field is absent from
the projected record. The UI shows a conflict warning at the event level; a
future release will show field-level dispute indicators.

**Source completeness scoring** factors (see `src/atlas/confidence/engine.py`).
Internal DB columns retain the `confidence_*` name for migration stability;
the UI and public docs use "source completeness" throughout.

| Factor | Max delta |
|---|---|
| Best source tier | +0.40 |
| Investigation status (final/closed) | +0.25 |
| Multi-source coverage (confirmed/inferred only) | +0.10 |
| All critical fields present | +0.10 |
| Final report linked **and url-verified** | +0.05 |
| Preliminary investigation | −0.20 |
| Missing date | −0.15 |
| Unresolved conflicts | −0.15 per conflict (max −0.30) |
| Missing location | −0.10 |
| Missing critical fields | −0.04 per field (max −0.20) |

Thresholds: **Well sourced** ≥ 0.90 · **Mostly sourced** ≥ 0.70 · **Partially sourced** ≥ 0.50 · **Weakly sourced** < 0.50

These labels intentionally say "sourced" not "confident" — the score measures source completeness and data coverage, not statistical truth-probability.

These thresholds are the **canonical definition** — both backend
(`confidence/engine.py`) and frontend (`web/lib/utils.ts`) must match them.

---

## Conflict detection

Conflicts are field-level disagreements between sources:

- **Fatality/injury counts** (`fatalities_total`, `fatalities_crew`,
  `fatalities_passengers`, `serious_injuries`, `serious_injuries_crew`,
  `serious_injuries_passengers`, `minor_injuries`, `minor_injuries_crew`,
  `minor_injuries_passengers`, `uninjured_crew`, `uninjured_passengers`,
  `aboard_total`): any integer difference is a conflict. A 1-fatality or
  injury discrepancy is always significant. Split fields are nullable; NULL
  means the source did not provide that split, not confirmed zero.
- **Coordinates**: conflict if sources differ by more than 0.5° (~55 km)
- **Other numerics**: conflict if difference exceeds 5% of the larger value
- **Strings**: conflict if values differ after case-normalisation
- **null vs anything**: never a conflict (null means unknown, not zero)

When a conflict is detected, both claims are marked `DISPUTED` and a
`ClaimConflict` row is written. Both type changes are recorded in
`ClaimHistory`. Claims explicitly rejected by a reviewer move to the
`rejected` claim type; the API exposes claim types as an explicit enum and the
web UI treats `rejected` as a first-class state with exhaustive badge/label
mapping, rendering it as excluded rather than unknown. Manual conflict resolution is available via
`POST /api/v1/conflicts/{id}/resolve` — see the API reference for
resolution types, accepted/rejected claim semantics, and survivor
auto-derivation for `claim_rejected` resolutions.

---

## CLI reference

```bash
atlas --help

# Ingestion
atlas ingest ntsb --start YYYY-MM-DD --end YYYY-MM-DD
atlas ingest csv PATH

# Database
atlas db migrate          # run alembic upgrade head
atlas db seed             # seed source registry

# Operations
atlas reproject [--event-id ID]   # rebuild projection (one event or all)
atlas check-links [--limit N]     # HEAD-check source document URLs
```

---

## API reference

| Endpoint | Description |
|---|---|
| `GET /api/v1/health` | Liveness check |
| `GET /api/v1/sources` | List source registry |
| `GET /api/v1/accidents` | Paginated accident list (filters, sort) |
| `GET /api/v1/accidents/{id}` | Full accident detail |
| `GET /api/v1/accidents/{id}/provenance` | All claims, conflicts, source docs, projection explanations, timeline |
| `GET /api/v1/accidents/map` | Geocoded map data — high zoom returns capped points; low zoom (`zoom <= MAP_CLUSTER_MAX_ZOOM`, default 6) returns capped clusters. Returns `{mode, items, clusters, count, truncated, limit, zoom, cluster_cell_degrees}`. When `truncated=true`, add filters, use viewport bounds, or zoom in. |
| `GET /api/v1/analytics/summary` | Full-dataset aggregate statistics (severity, phase, year, source completeness) |
| `POST /api/v1/conflicts/{id}/resolve` | Resolve an open claim conflict; triggers projection rebuild |

Operational docs: [production deployment](docs/production.md) · [CI/full-stack verification](docs/ci.md) · [data retention and archival policy](docs/data-retention.md) · [alerting guide](docs/alerts.md) · [dashboard guide](docs/dashboards.md) · [large-data performance fixtures](docs/performance-fixtures.md)

Full interactive docs: `http://localhost:8000/docs`

---

## Development

### End-to-end smoke test

Proves the full stack works end-to-end:

```bash
./scripts/smoke_test.sh
```

Requires: Docker, `docker compose`, `curl`, `python3`. If `npm` is available,
the script installs frontend dependencies automatically and runs
`npx tsc --noEmit`. Frontend type-check is **not** optional when npm is present.

The script performs 14 checks:

1. PostGIS database starts and is healthy
2. Alembic migrations run through head (all versions)
3. Sources seed successfully
4. API starts and is reachable
5. `/health` returns `{"status":"ok"}`
6. `/sources` returns at least one source
7. `tests/fixtures/ntsb_sample.csv` ingests without error
8. Projection rebuilds
9. `/accidents` total > 0 after ingestion
10. `/accidents/{id}` returns a detail record
11. `/accidents/{id}/provenance` returns claims
12. `/accidents/map` returns response envelope `{mode, items, clusters, count, truncated, limit}` and supports low-zoom clustering (verifies route is not shadowed by `/{event_id}` and v28.8+ wrapper shape is intact)
13. `/analytics/summary` returns valid shape
14. Frontend TypeScript type-check passes

### Full-stack CI reproduction

Run the same production-parity verification used by CI locally:

```bash
make ci-full
```

This starts PostGIS and Redis, runs Alembic upgrade/downgrade/upgrade, executes pytest, and runs frontend install/type-check/lint. See [docs/ci.md](docs/ci.md).

### Large-data performance fixtures

Generate synthetic large datasets on demand; large CSVs are not committed:

```bash
make perf-fixture-smoke      # 500 NTSB-like rows + 50 ASN-like overlap rows
make perf-fixture-local      # 10k NTSB-like rows + 1k ASN-like overlap rows
```

After loading a generated fixture into a local/staging database, run:

```bash
make perf-smoke BASE_URL=http://localhost:8000
```

See [docs/performance-fixtures.md](docs/performance-fixtures.md) for profiles, loading commands, thresholds, and nightly/staging guidance.

### Running unit tests

```bash
pip install -e ".[dev]"
pytest
```

Tests cover: claim value serialisation, normaliser, conflict detection,
deduplicator, confidence label thresholds, projection winner eligibility,
datetime timezone semantics, and country/state schema safety.
They run without a live database.

### Linting

```bash
ruff check src/ tests/
```

### Frontend type check

```bash
cd web && npx tsc --noEmit
```

---

## Known limitations

- **Retention/archive automation is policy-defined but not yet automated.**
  The project now documents hot retention, archive formats, restore expectations,
  and required safeguards in [docs/data-retention.md](docs/data-retention.md).
  Automated purge/archive CLI commands must not be enabled until archive manifests,
  dry-run mode, checksum verification, restore tests, and operational metrics are
  implemented.
- **Pagination scope** — date-sorted accident lists use cursor pagination;
  non-date sorts still use offset pagination for compatibility.
- **Search scope** — substring search is backed by `pg_trgm` expression indexes
  that match the current `lower(column) LIKE '%term%'` predicate. It is not yet
  a relevance-ranked full-text search engine.
- **Authentication UX** — API-key authentication exists for protected reviewer
  endpoints, and successful key use updates `api_keys.last_used_at` for audit
  visibility. The web UI now has a browser-local reviewer API-key control and
  sends that key as `X-API-Key` for conflict-resolution actions. This is not a
  full identity-provider sign-in flow or central key-management UI. Admin
  override endpoints require `API_AUTH_ENABLED=true` plus an `admin` key; they
  do not run in auth-disabled local/dev mode. Do not expose reviewer/admin
  endpoints publicly without production auth configured and tested.
- **Single source in production** — only NTSB is currently ingested.
  Multi-source conflict handling is implemented and exercised end-to-end
  by the test suite.  A live second source (e.g. ASN, ICAO) can be wired
  in via `atlas ingest generic-csv --mapping <name>` using the bundled
  mappings in `src/atlas/ingestion/source_mappings/` or a custom JSON
  mapping file. Generic CSV rows are normalised to plain Python values first;
  `ClaimWriter` then encodes them exactly once for storage. At the time of
  writing only NTSB is enabled by default in the seed data.
- **Confidence scores are heuristic** — scores reflect data completeness
  and source quality, not statistical reliability. Labels like "Well
  sourced" mean high source completeness, not statistical verification.
- **Source documents are not URL-verified at ingestion** —
  `url_verified` starts `false` on every newly-created `SourceDocument`
  row. The frontend explicitly labels these as
  *Linked, unverified* until `atlas check-links` runs an HTTP HEAD
  check and updates `url_verified` and `is_available`. Verified
  documents render with a ✓; checked-and-unavailable with ✗;
  linked-but-unchecked with ?.
- **NTSB document extraction is conservative.** The pipeline only
  emits a `SourceDocument` from a real URL field present in the raw
  NTSB record (e.g. `FinalReportUrl`, `DocketUrl`) plus one
  deterministic CAROL search URL when an `EventId` is present. We do
  not fabricate per-record investigation page URLs — those URLs are
  not stable and synthesising them would create exactly the
  false-authority pattern we are trying to avoid.
- **Approximate locations.** The NTSB CSV reports nearest-city
  granularity, not precise crash coordinates. When `latitude` /
  `longitude` are absent but a `location_text` is present, the
  projection labels the field as `approximate_nearest_city_only` and
  the frontend renders it as *Approximate*, not *Confirmed*.
- **`last_projected_at` is a local rebuild marker, not a source-update
  timestamp.** The UI labels this as *Record rebuilt* (not "Last
  updated") to prevent the misreading that the source data changed
  when the projection was rebuilt.
- **Reviewer workflow is minimal.** Conflict resolution is available via
  `POST /api/v1/conflicts/{id}/resolve` with a JSON body specifying
  `resolution_type`, `accepted_claim_id`, and `resolved_by`. The endpoint
  validates the resolution, persists it, and immediately triggers a projection
  rebuild for the affected event. A web-based conflict review queue is available
  at `/conflicts` — reviewers can enter their API key in the browser-local
  auth control and resolve conflicts directly from the queue page.

---

## Licensing and data sources

| Source | License | Notes |
|---|---|---|
| NTSB | Public domain (49 U.S.C. § 1154) | US civil aviation accidents |

Non-NTSB sources require individual licensing review before ingestion.
Do not ingest proprietary data without confirming redistribution rights.

- **Alerting/dashboard baseline defined** — Prometheus alert rules and a Grafana
  overview dashboard are now provided under `monitoring/`, with operational
  interpretation in [docs/alerts.md](docs/alerts.md) and
  [docs/dashboards.md](docs/dashboards.md). Ingestion freshness, conflict
  backlog age, and archive-job alerts remain planned until their metrics are
  emitted.


## Additional production-readiness docs

- [ASN second-source integration](docs/second-source-asn.md)
- [CI/full-stack validation](docs/ci.md)
- [Performance fixtures](docs/performance-fixtures.md)
- [Alerting](docs/alerts.md) and [dashboards](docs/dashboards.md)
- [Data retention](docs/data-retention.md)

- [Production feature checklist](docs/production-features.md)
