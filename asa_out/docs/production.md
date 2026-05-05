# Aviation Safety Atlas — Production Deployment Guide

## Required environment variables

```env
APP_ENV=production
API_AUTH_ENABLED=true
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/atlas
RATE_LIMIT_ENABLED=true
RATE_LIMIT_STORAGE_URL=redis://redis:6379/0
```

Either protect `/metrics` with a bearer token:
```env
METRICS_TOKEN=<random-secret>
```

Or explicitly acknowledge it is behind a network policy:
```env
METRICS_PUBLIC_OK=true
```

## Production startup checks

The app refuses to start if any of these are violated:

| Condition | Error |
|---|---|
| `APP_ENV=production` + `API_AUTH_ENABLED=false` | Fatal — write endpoints are unauthenticated |
| `APP_ENV=production` + `RATE_LIMIT_ENABLED=true` + no `RATE_LIMIT_STORAGE_URL` | Fatal — in-memory limiting breaks under multiple workers |
| `APP_ENV=production` + no `METRICS_TOKEN` + `METRICS_PUBLIC_OK=false` | Fatal — `/metrics` is unprotected |

## Pre-startup checklist

1. Apply migrations: `atlas db migrate`
2. Seed sources: `atlas db seed`
3. Verify readiness: `GET /api/v1/readyz` returns `{"ready": true}`
4. `/readyz` checks: database connectivity, migration head, NTSB source row, Redis connectivity

## Rate limiting

- In-memory (single worker, dev/staging): set no `RATE_LIMIT_STORAGE_URL`
- Multi-worker production: **Redis is required**. Each worker has its own in-memory bucket without Redis, multiplying the effective limit by worker count.

Default limits:
```
Default (all routes): 120/minute
Map endpoint:         30/minute
Analytics summary:    30/minute
Provenance:           60/minute
Mutations:            30/minute
```

## Endpoints

| Path | Purpose |
|---|---|
| `GET /api/v1/health` | Liveness probe — cheap `SELECT 1`. Not rate-limited. |
| `GET /api/v1/readyz` | Readiness probe — checks DB, migrations, sources, Redis. Not rate-limited. |
| `GET /metrics` | Prometheus metrics. Requires `METRICS_TOKEN` bearer auth in production unless `METRICS_PUBLIC_OK=true`. Not rate-limited. |

## Map endpoint bounding-box support

Use viewport bounds to avoid hitting `MAX_MAP_RESULTS`:

```
GET /api/v1/accidents/map?north=52&south=48&east=10&west=4&severity=FATAL
```

All four bbox parameters (`north`, `south`, `east`, `west`) must be provided together.

## Data retention and archival

Production deployments should follow the documented retention policy in
[docs/data-retention.md](data-retention.md). The current release defines the
policy baseline but does not enable automated destructive archive/purge jobs.

Before enabling archive automation in production, implement and verify:

1. archive manifest registry;
2. dry-run-first archive CLI;
3. checksum verification;
4. restore drill into staging;
5. archive metrics and alerts;
6. legal/licensing review for non-NTSB sources.

## Alerting and dashboards

Production deployments should import the alert rules and dashboard shipped with
this repository:

| Artifact | Purpose |
|---|---|
| `monitoring/prometheus/atlas-alerts.yml` | Prometheus alert rules for API availability, 5xx rate, latency, truncation, and projection rebuild failures |
| `monitoring/grafana/aviation-safety-atlas-overview.json` | Grafana overview dashboard for API traffic, latency, truncation, conflict resolution, and projection rebuild outcomes |
| `docs/alerts.md` | Alert explanations, tuning guidance, readiness blackbox example, and runbook pointers |
| `docs/dashboards.md` | Dashboard panel definitions and interpretation notes |

Minimum production monitoring checklist:

1. Prometheus scrapes `/metrics` with the configured `METRICS_TOKEN`.
2. `/api/v1/readyz` is checked by the orchestrator and, preferably, by a
   blackbox exporter.
3. `AtlasApiDown`, `AtlasHigh5xxRate`, `AtlasProjectionRebuildFailures`, and
   readiness-failure alerts are routed to paging channels.
4. Map/provenance truncation alerts are routed as tickets until real traffic
   baselines are known.
5. Dashboard variable `job` matches the production scrape job label.

The current alerting baseline uses metrics emitted by this release only.
Ingestion freshness, conflict backlog age, and archive-job alerts are documented
as planned alerts and should not be enabled until their metrics exist.

## Large-data performance fixtures

Use the synthetic fixture generator before exposing large ingestion changes or map/search changes to production:

```bash
python scripts/generate_large_data_fixture.py --profile local-10k --output-dir .generated/perf/local-10k
PYTHONPATH=src atlas ingest csv .generated/perf/local-10k/ntsb_large.csv
PYTHONPATH=src atlas ingest generic-csv .generated/perf/local-10k/asn_like_large.csv --mapping asn
PYTHONPATH=src atlas reproject
python scripts/performance_smoke.py --base-url http://localhost:8000
```

See [docs/performance-fixtures.md](performance-fixtures.md) for profiles and suggested thresholds. Generated fixture directories are ignored by git and must not be committed.

## Operational workflow checks before production scale

Before loading large multi-source datasets, run the following against staging:

```bash
PYTHONPATH=src atlas archive plan --cutoff-days 730
make perf-fixture-smoke
make perf-ingest-smoke
make perf-smoke BASE_URL=https://staging-atlas.example.com
```

Reviewer queues that must be staffed or explicitly disabled in operating
procedures:

- `/api/v1/conflicts` for claim disagreements
- `/api/v1/duplicates` for ambiguous cross-source matches
- `/api/v1/data-quality/issues` for split/total consistency warnings

Prometheus should scrape `/metrics` and alert on open conflicts, duplicate
candidate backlog, stale ingestion, and open data-quality issues.

## CI and release gate

Before deploying, run the same full-stack checks used by GitHub Actions:

```bash
make ci-full
```

This verifies PostGIS, Redis-backed rate limiting, Alembic upgrade/downgrade, backend pytest, and frontend type-check/lint. See [CI/full-stack verification](ci.md) for the complete contract.
