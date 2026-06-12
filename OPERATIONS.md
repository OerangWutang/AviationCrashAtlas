# Atlas operational readiness

Atlas is designed to run with separate API and worker processes, an external Postgres/PostGIS database, and a Prometheus-compatible metrics scraper.

## First production boot sequence

Run first boot in this order. Do not swap traffic until all steps pass:

1. Validate environment and production safety checks:
   - free/self-hosted: `cd deploy/free && ./check-env.sh .env`
   - other deployments: ensure required production vars are set (`ENVIRONMENT`, DB URLs, `API_KEY_HASH_SECRET`, `ALLOWED_HOSTS`, `CORS_ORIGINS`, metrics access controls, and `HSTS_ENABLED` after TLS is end-to-end)
2. Apply schema before starting the API:
   - `alembic upgrade head`
3. Seed first admin key when needed:
   - first deployment (or no active admin key): `atlas bootstrap`
4. Confirm app health/readiness:
   - `/health` returns 200
   - `/ready` returns 200
5. Run post-boot smoke checks:
   - authenticated API request with the newly generated key
   - `/metrics` reachable only from approved CIDRs or bearer token

Startup fails fast when required schema/seed data are missing (for example, the
`CuratorOverride` source inserted by migrations). `/ready` itself only checks DB
reachability, so keep the authenticated smoke request in the checklist.

For full cutover sequencing and sign-off fields, use
`ops/runbooks/production-cutover-checklist.md`.

## Production ASGI process model

The container entrypoint runs Gunicorn with Uvicorn workers:

```bash
gunicorn atlas.presentation.api.app:app --config gunicorn_conf.py
```

Key environment variables:

| Variable | Purpose | Default |
|---|---:|---:|
| `WEB_CONCURRENCY` | Gunicorn worker processes | `(2 * cores) + 1` |
| `GUNICORN_KEEPALIVE_SECONDS` | Keep-alive, keep below load-balancer idle timeout | `55` |
| `GUNICORN_TIMEOUT_SECONDS` | Hard worker timeout | `60` |
| `GUNICORN_GRACEFUL_TIMEOUT_SECONDS` | Shutdown grace window | `30` |
| `GUNICORN_MAX_REQUESTS` | Worker recycle count | `10000` |
| `GUNICORN_MAX_REQUESTS_JITTER` | Recycle jitter to avoid herd restarts | `1000` |

For small Kubernetes pods, set `WEB_CONCURRENCY` explicitly rather than letting CPU detection overestimate on shared nodes. Gunicorn heartbeats are forced onto `/dev/shm` through both `gunicorn_conf.py` and the Docker command to avoid slow overlay-filesystem worker timeouts under load.

## Container hardening

The Dockerfile uses a multi-stage wheel build, installs from the pinned lockfile, copies only runtime artifacts, and runs as a non-root user. Healthchecks are intentionally configured per service rather than baked into the image: API containers check `/health`, while worker and migration containers do not expose HTTP. Use `docker build .` or `make docker-build` as the release-image smoke test.

## Easy security baseline

Atlas now applies a few cheap application-level protections even when a reverse proxy is misconfigured:

- `ALLOWED_HOSTS` enables Host-header allow-listing through Starlette's `TrustedHostMiddleware`. Development defaults to `*`, but `ENVIRONMENT=production` refuses to start until explicit hostnames are configured.
- `SECURITY_HEADERS_ENABLED=true` adds `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, `Cross-Origin-Opener-Policy`, and `Cross-Origin-Resource-Policy` to responses. Keep this enabled in production.
- `API_DOCS_ENABLED` defaults to enabled outside production and disabled in production. Do not expose `/docs`, `/redoc`, or `/openapi.json` publicly unless they are behind private auth or a VPN.
- `HSTS_ENABLED=false` by default because it should only be enabled once HTTPS is working end-to-end. After TLS is stable, set `HSTS_ENABLED=true`.
- Production CORS origins must use `https://`, except localhost development origins.

For a simple free/self-hosted deployment behind Caddy, a typical production starting point is:

```env
ENVIRONMENT=production
ALLOWED_HOSTS=api.example.com,localhost,127.0.0.1
API_DOCS_ENABLED=false
SECURITY_HEADERS_ENABLED=true
HSTS_ENABLED=true
CORS_ORIGINS=https://app.example.com
```

## Free/self-hosted deployment

For a completely free VPS or homelab launch, use `deploy/free/` instead of the
AWS/ECS-style production plan. It runs API, worker, Postgres/PostGIS, Redis,
PgBouncer, and Caddy on one Docker host while exposing only Caddy on ports
80/443. Postgres and Redis are never bound to public host ports.

```bash
cd deploy/free
cp .env.example .env
# edit domain, email, URL-safe DB values, and strong secrets
docker build -t atlas-backend:local ../..
docker compose --env-file .env up -d
```

Backups are intentionally explicit:

```bash
cd deploy/free
./backup-postgres.sh
```

Copy those backups off the VM. A free server with a local Docker volume is not a
backup strategy.


## Hermes fetch worker

Hermes fetch jobs should be processed by the dedicated worker command, not by repeatedly calling the manual API run endpoint:

```bash
atlas hermes-worker --sleep-seconds 5 --batch-limit 5
```

The worker atomically claims due queued jobs, sets a finite RUNNING lease, commits before network I/O, and fences finalization by worker ID plus attempt count. Expired RUNNING leases are recovered at the start of each worker cycle so a crashed process does not permanently block a crawl target.

Hermes performs server-side URL fetches. The production fetcher rejects non-HTTP(S) schemes, private/loopback/link-local/reserved address ranges, unsafe redirect targets, excessive redirect chains, and responses larger than the configured in-memory cap. Keep crawler targets to trusted/public sources where possible.

## PgBouncer

For high worker counts or many API/worker pods, put PgBouncer in front of Postgres and run it in `transaction` pooling mode. Then set:

```env
DB_USE_NULL_POOL=true
DATABASE_URL=postgresql+asyncpg://atlas:...@pgbouncer:6432/atlas
DATABASE_SYNC_URL=postgresql://atlas:...@postgres:5432/atlas
```

Alembic migrations should connect directly to Postgres, not to transaction-pooled PgBouncer, because migrations may need session-level behavior.

A starter PgBouncer config lives in `ops/pgbouncer/`.

## Metrics

`/metrics` exposes Prometheus-format HTTP metrics plus Atlas domain gauges:

- `atlas_outbox_events_total{status="pending|processing|failed|dead_letter"}`
- `atlas_conflicts_total{status="open"}`
- optional exact historical gauges when `PROMETHEUS_EXPENSIVE_DOMAIN_METRICS_ENABLED=true`:
  - `atlas_outbox_events_total{status="processed"}`
  - `atlas_conflicts_total{status="resolved"}`
  - `atlas_claims_total`
  - `atlas_projected_events_total`
- `atlas_operational_metrics_refresh_success`
- `atlas_operational_metrics_last_refresh_timestamp_seconds`

The existing authenticated JSON endpoint remains available at `/api/v1/admin/metrics` for dashboards/debugging and returns the exact full snapshot on demand. Prometheus scrapes skip exact historical totals by default because those can become expensive on append-only tables; enable them only when you have measured the cost. DB-backed gauges are cached for `PROMETHEUS_DOMAIN_METRICS_TTL_SECONDS` seconds so the scrape interval does not repeatedly run exact counts over large tables.

Scrape `/metrics` from trusted infrastructure only. Atlas also gates the endpoint in-app: by default only localhost can scrape it via `PROMETHEUS_ALLOWED_CIDRS=127.0.0.1/32,::1/128`. In Kubernetes or private VPC deployments, set `PROMETHEUS_ALLOWED_CIDRS` to the Prometheus scraper CIDRs, or set `PROMETHEUS_BEARER_TOKEN` and configure Prometheus to send `Authorization: Bearer <token>`. Keep an ingress/network-policy deny rule for `/metrics` as the outer perimeter.

Baseline Prometheus alert rules are provided at
`ops/alerts/prometheus-atlas-rules.yml`, including:

- outbox backlog age too high (`atlas_outbox_oldest_unprocessed_age_seconds`)
- missing outbox worker heartbeat (`atlas_outbox_worker_heartbeat_present`)
- metrics refresh pipeline failure (`atlas_operational_metrics_refresh_success`)

## Load testing

The k6 scenario in `ops/load/atlas_k6_load_test.js` stresses duplicate-heavy ingestion while provenance reads occur concurrently. Each ingestion request uses a unique `source_record_id` and idempotency key so the test cannot accidentally benchmark only idempotency replay/cache behavior, while registrations remain bucketed to create duplicate-match pressure.

```bash
BASE_URL=https://atlas.example.com \
API_KEY=... \
SOURCE_ID=<source-uuid> \
PROVENANCE_EVENT_ID=<large-event-uuid> \
k6 run ops/load/atlas_k6_load_test.js
```

Watch these while it runs:

- API p95/p99 latency
- Postgres CPU and memory
- PgBouncer `cl_waiting`, `sv_active`, and `sv_idle`
- `atlas_outbox_events_total{status="pending"}` growth rate
- lock waits and deadlocks

A healthy system should show bounded pending outbox growth and stable DB memory even as the ingestion scenario generates duplicate/review pressure.

Capture and commit staging baseline results before go-live (p95/p99, throughput,
error rate, backlog behavior) so future regressions have a reference point.

## Deployment-proofing guardrails

- Use `deploy/free/check-env.sh .env` before starting the free/self-hosted stack. It refuses placeholder secrets, wildcard production hosts, public Prometheus CIDRs, and non-HTTPS CORS origins.
- Free/self-hosted preflight also enforces `HSTS_ENABLED=true` and requires an explicit `EDGE_RATE_LIMIT_OWNER` declaration so edge rate limiting ownership is never implicit.
- Docker Compose services use bounded JSON-file logs (`10m` x `5`) to avoid filling small VPS disks during error loops.
- Production HTTP projection rebuilds are bounded by `ADMIN_MAX_PROJECTION_REBUILD_EVENTS`; unlimited rebuilds require an explicit maintenance-window override.
- CI runs Trivy image/filesystem scans and Gitleaks secret scanning before an image should be promoted.
- Dependabot is configured for Python dependencies, Docker base images, and GitHub Actions.

## API_KEY_HASH_SECRET rotation

`API_KEY_HASH_SECRET` is a live auth dependency. Rotating it without a staged
plan can invalidate all active API keys at once.

Use `API_KEY_HASH_SECRET_PREVIOUS` for bounded dual-secret cutovers (verify old
and new hashes during migration), then remove it after client key rollover.
Follow the full procedure in `ops/runbooks/api-key-hash-secret-rotation.md`.

## API key auth cache

Validated API keys are cached in-process for `API_KEY_CACHE_TTL_SECONDS`
(default: **5 seconds**) to reduce auth-DB load on hot paths. The cache is
per-process and per-instance:

- A revoked or role-changed key remains valid on each running process for up to
  `API_KEY_CACHE_TTL_SECONDS` — the revocation is not visible until the cached
  entry expires.
- Set `API_KEY_CACHE_TTL_SECONDS=0` to disable the cache entirely when
  immediate key revocation is required (e.g., during a security incident).
- A cache of successfully validated keys only. Failed lookups are never cached
  so a spray of random keys cannot fill memory.
- To invalidate the cache inside a single API process without restarting, call
  `atlas auth clear-cache` (or equivalently
  `clear_auth_cache()` from `atlas.presentation.api.dependencies`).
- Cross-process invalidation requires either setting `API_KEY_CACHE_TTL_SECONDS`
  to 0 (no cache) or issuing a new API key and waiting for the old window to
  expire.

**Production note:** In a multi-worker Gunicorn deployment, each worker has its
own independent auth cache. A revoked key may still be accepted by workers that
have not yet hit TTL expiry. For highest assurance deployments where key
revocation must be immediate, set `API_KEY_CACHE_TTL_SECONDS=***.

The cache size is bounded by `API_KEY_CACHE_MAX_ENTRIES` (default: 10,000).
LRU eviction drops the least-recently-used entry when the limit is exceeded.
`atlas auth clear-cache` flushes the LRU map entirely.

## Backup and disaster recovery

Backup and restore tooling lives in `deploy/free/`. Full runbook with RPO/RTO
targets, restore drill procedure, and monitoring configuration:

**`ops/runbooks/backup-restore.md`**

Quick reference:

| Action | Command |
|--------|---------|
| Manual backup | `deploy/free/backup-postgres.sh` |
| Restore (with confirmation) | `ATLAS_RESTORE_CONFIRM=1 ./deploy/free/restore-postgres.sh backups/atlas-<stamp>.sql.gz` |
| Check freshness | `deploy/free/check-latest-backup.sh` |
| Prune old backups | `deploy/free/prune-backups.sh` |

## OpenTelemetry tracing

Atlas emits distributed traces when `OTEL_EXPORTER_OTLP_ENDPOINT` is set.
Tracing is **off by default** — an unconfigured deploy never contacts an OTLP
collector and startup is not slowed by gRPC channel initialisation.

### Enabling tracing

Set these environment variables before starting the API:

```env
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317   # gRPC endpoint
OTEL_SERVICE_NAME=atlas-backend                           # resource attribute (default: atlas-backend)
```

`ENVIRONMENT` is forwarded to the `deployment.environment` resource attribute automatically.

### What is instrumented

| Layer | Instrumentation | Scope |
|-------|----------------|-------|
| HTTP requests | `opentelemetry-instrumentation-fastapi` | All routes, status code, method |
| SQLAlchemy | `opentelemetry-instrumentation-sqlalchemy` | Query text, DB name, connection pool |

Spans inherit the W3C TraceContext from upstream callers (BFF, load balancer) when
present.  All async and sync DB calls are instrumented; asyncpg pool events produce
child spans under the parent route span.

### Self-hosted (Grafana stack)

Add an OpenTelemetry Collector sidecar or use Grafana Alloy:

```yaml
# docker-compose.yml fragment
otel-collector:
  image: otel/opentelemetry-collector-contrib:latest
  command: ["--config=/etc/otelcol/config.yaml"]
  volumes:
    - ./ops/otel-collector.yaml:/etc/otelcol/config.yaml:ro
  ports:
    - "4317:4317"   # gRPC
    - "4318:4318"   # HTTP
```

Point `OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317` on the `api` service.

### Required packages

OTel packages are declared in `requirements.in` / `pyproject.toml`. When building
the release image they are included in the pinned lockfile. If running outside the
standard lockfile, install:

```
opentelemetry-sdk>=1.25
opentelemetry-instrumentation-fastapi>=0.46b0
opentelemetry-instrumentation-sqlalchemy>=0.46b0
opentelemetry-exporter-otlp-proto-grpc>=1.25
```

If packages are missing and `OTEL_EXPORTER_OTLP_ENDPOINT` is set, the API logs a
warning at startup and continues without tracing.

## Zero-downtime migrations

Atlas uses Alembic for schema migrations. All migrations applied to a live
production database must avoid full-table rewrites and excessive lock times.

### CI guard

`backend-ci.yml` runs a zero-downtime migration guard on every PR that adds a new
migration file. Violations block the CI with actionable guidance.

To suppress a known-safe violation (e.g., on a table small enough that a full
rewrite is acceptable), add the ignore marker on the same line as the operation:

```python
op.drop_column("small_staging_table", "tmp_col")  # zdtcheck: ignore-drop-column
```

### Safe patterns

**Adding a NOT NULL column**
```python
# Step 1 (deploy): add nullable + server_default
op.add_column("events", sa.Column("severity", sa.Integer, nullable=True, server_default="0"))
# Step 2 (backfill + constraint, after all pods run step-1 code):
op.execute("UPDATE events SET severity = 0 WHERE severity IS NULL")
op.alter_column("events", "severity", nullable=False)
```

**Creating an index on a large table**
```python
# Use postgresql_concurrently=True; wrap in transaction=False migration
op.create_index("ix_claims_event_id", "claims", ["event_id"], postgresql_concurrently=True)
```

```python
# At the top of the migration file — required for CONCURRENTLY:
def upgrade() -> None:
    ...

# alembic env.py / migration-level override:
context.execute("SET lock_timeout = '5s'")
```

**Dropping a column (3-step)**
1. Migration A: set column nullable, remove any NOT NULL constraint.
2. Code deploy: remove all application references to the column.
3. Migration B (separate PR): `op.drop_column(...)  # zdtcheck: ignore-drop-column`.

**Renaming a column (expand/contract)**
1. Migration A: add new column + trigger or application dual-write.
2. Migration B: backfill + NOT NULL.
3. Code deploy: read from new column.
4. Migration C: drop old column.

### Lock timeouts

Production Postgres should have a statement-level lock timeout set to avoid silent
queue buildup during migrations:

```sql
SET lock_timeout = '5s';      -- fail fast if blocked by a long transaction
SET statement_timeout = '60s'; -- hard cap for DDL statements
```

Set these in `alembic/env.py` for the migration connection, or in PgBouncer's
`server_reset_query` for session-pooled connections.
