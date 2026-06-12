# Atlas production cutover checklist

Use this checklist for first production launch and major re-launches.

## 1) Pre-cutover security and build gates

- [x] CI `docker-build` job green on release branch.
- [x] CI Trivy image scan green (`CRITICAL,HIGH --ignore-unfixed`).
- [x] CI Trivy filesystem scan green.
- [x] CI Gitleaks scan green (full git history).
- [x] Local/CI release notes updated with exact scan dates and results.

## 2) Production configuration

- [x] `ENVIRONMENT=production`
- [x] `DATABASE_URL` / `TENANT_DATABASE_URL` / `SYSTEM_DATABASE_URL` set correctly.
- [x] `API_KEY_HASH_SECRET` set (64+ hex chars).
- [x] `ALLOWED_HOSTS` set to explicit hosts (no wildcard).
- [x] `CORS_ORIGINS` set to explicit `https://` origins.
- [x] `PROMETHEUS_ALLOWED_CIDRS` or strong `PROMETHEUS_BEARER_TOKEN` configured.
- [x] `HSTS_ENABLED=true` once HTTPS is confirmed end-to-end.
- [x] free/self-hosted only: `deploy/free/check-env.sh .env` passes.

## 3) Database readiness

- [x] Run `alembic upgrade head` against production database.
- [x] Verify `alembic current` equals the expected head revision.
- [x] If no active admin API key exists, run `atlas bootstrap` and securely store printed admin API key.

## 4) Health and readiness

- [x] `/health` returns 200.
- [x] `/ready` returns 200.
- [x] Authenticated smoke request succeeds with new API key.
- [x] `/metrics` is inaccessible from public internet and available only to approved scraper identity/network.

## 5) Alerting and observability

- [x] Prometheus rules from `ops/alerts/prometheus-atlas-rules.yml` loaded.
- [x] Alert routing verified (warning + critical paths).
- [x] Dashboards show:
  - `atlas_outbox_oldest_unprocessed_age_seconds`
  - `atlas_outbox_worker_heartbeat_present`
  - `atlas_operational_metrics_refresh_success`

## 6) Load and capacity validation

- [x] Run k6 staging load test (`ops/load/atlas_k6_load_test.js`) with production-like data.
- [x] Record results in `ops/load/BASELINE_TEMPLATE.md`.
- [x] Confirm p99 latency and outbox backlog behavior are within acceptance limits.

## 7) Risk acknowledgements

- [x] Starlette PYSEC-2026-161 / CVE-2026-48710 (BadHost) resolved by upgrade to `starlette==1.1.0`; `TrustedHostMiddleware` + strict `ALLOWED_HOSTS` retained as defense-in-depth (see RELEASE.md → Known Vulnerabilities).
- [x] `API_KEY_HASH_SECRET` rotation runbook reviewed: `ops/runbooks/api-key-hash-secret-rotation.md`.
- [x] `include_archive=true` behavior reviewed: provenance surfaces retention-swept claims; conflict-history accepts the flag as a documented no-op (no endpoint returns 501). See RELEASE.md → Archive retrieval.

## 8) Sign-off

- [ ] Operator sign-off:
- [ ] Security sign-off:
- [ ] Release manager sign-off:
- [ ] Cutover timestamp (UTC):

---

# Rolling-upgrade deployment checklist

Use this checklist when deploying an update to an **already-running** production
Atlas instance (not a first launch — use the checklist above for that).

## Pre-deploy

- [ ] Migrations pass the ZDT guard locally: `git diff --diff-filter=A --name-only origin/main HEAD -- alembic/versions/` then review each added file.
- [ ] All new migrations use zero-downtime patterns (see OPERATIONS.md § Zero-downtime migrations).
- [ ] New code is backward-compatible with the **previous** migration state (old schema, new code must run without errors for the duration of the rolling window).
- [ ] If the migration drops a column or table: the previous deploy has already removed all code references.
- [ ] Backend CI green on release branch.
- [ ] Release image built and pushed: `make docker-build && docker push ...`
- [ ] Trivy image scan green on the release image.

## Migration window

Run the migration service while the current API is still serving traffic:

```bash
# free/self-hosted:
docker compose run --rm migrate
# verify:
docker compose exec api alembic current
```

Migrations that use `postgresql_concurrently=True` require `autocommit` mode; the
migration service (`alembic upgrade head`) handles this automatically when the
migration file sets `transaction=False`.

## Deploy

- [ ] Update the API image tag in `deploy/free/docker-compose.yml` (or equivalent).
- [ ] `docker compose up -d --no-deps api` — rolling restart of the API service.
- [ ] Wait for `/ready` to return 200 on the new container before removing the old one.
- [ ] Update worker image tag and restart: `docker compose up -d --no-deps worker hermes-worker`.

## Post-deploy smoke

- [ ] `/health` → 200
- [ ] `/ready` → 200
- [ ] Authenticated API call succeeds.
- [ ] `atlas_outbox_worker_heartbeat_present` gauge is 1 within 2 minutes.
- [ ] No spike in `atlas_outbox_oldest_unprocessed_age_seconds`.
- [ ] No 5xx errors in API logs.

## Rollback

If the new version must be rolled back:

1. Identify the previous image tag.
2. `docker compose up -d --no-deps api` with the old tag.
3. If the migration added a column or index and the old code does not reference it: the migration can be left applied (additive migrations are backward-compatible).
4. If the migration requires rollback: `alembic downgrade -1` against the production database **before** rolling back the code image; confirm `alembic current` shows the expected revision.
5. Test `/health` + `/ready` + authenticated smoke request before re-enabling traffic.

---

# Alert runbooks

## AtlasOutboxWorkerHeartbeatMissing (critical)

**Alert fires when:** `atlas_outbox_worker_heartbeat_present == 0` for 5 minutes.

**Meaning:** The outbox worker process has not updated its heartbeat. Outbox events
(cross-service domain events) are not being delivered. This will eventually stall
background ingestion, Hermes fetches, and any other subscriber.

**Immediate steps:**

1. Check if the worker container is running: `docker compose ps worker`
2. Inspect logs: `docker compose logs --tail 100 worker`
3. Common causes:
   - Container OOM-killed: check `dmesg` or `docker inspect` exit code 137.
   - Database unreachable: verify `DATABASE_URL` and run `/ready` on the API.
   - Deadlock loop: look for repeated "deadlock detected" in worker logs.
4. Restart the worker: `docker compose restart worker`
5. Monitor `atlas_outbox_worker_heartbeat_present` — it should flip to 1 within 30 seconds.
6. If the worker keeps crashing: examine the stack trace, open an incident.

**Escalate if:** The worker does not stay up for 5 minutes after restart, or if
`atlas_outbox_events_total{status="failed"}` is growing.

---

## AtlasOutboxBacklogAgeHigh (warning)

**Alert fires when:** `atlas_outbox_oldest_unprocessed_age_seconds > 300` for 10 minutes.

**Meaning:** The oldest unprocessed outbox event is more than 5 minutes old. The
worker is running but not keeping up, or a specific event is repeatedly failing.

**Immediate steps:**

1. Confirm the worker is running and healthy (heartbeat present).
2. Check worker throughput: `docker compose logs --tail 200 worker | grep -i "processed\|error\|retry"`
3. Check for stuck events:
   ```sql
   SELECT id, status, attempt_count, last_error, scheduled_for
   FROM outbox_events
   WHERE status IN ('PENDING', 'FAILED')
   ORDER BY created_at
   LIMIT 20;
   ```
4. If `attempt_count` is high on specific events: those events may be poisoning the queue.
   Mark them as dead-letter manually if the payload is invalid:
   ```sql
   UPDATE outbox_events SET status = 'DEAD_LETTER' WHERE id = '<id>';
   ```
5. If the backlog is growing uniformly: check Postgres CPU, I/O, and lock waits.
   `pg_stat_activity` and `pg_locks` are the first stop.
6. Scale the worker (`WEB_CONCURRENCY`-equivalent) or increase `--batch-limit` if the
   database can sustain it.

**Escalate if:** Backlog age exceeds 30 minutes, or `atlas_outbox_events_total{status="dead_letter"}`
is growing faster than expected.

---

## AtlasOperationalMetricsRefreshFailed (warning)

**Alert fires when:** `atlas_operational_metrics_refresh_success == 0` for 5 minutes.

**Meaning:** The background task that refreshes DB-backed Prometheus gauges (open
conflict count, outbox backlog stats, etc.) has been failing. Metrics shown in
dashboards may be stale.

**Note:** This is a warning, not a critical alert. The API continues serving
correctly; only the metrics refresh pipeline is broken.

**Immediate steps:**

1. Check API logs for the refresh task:
   ```bash
   docker compose logs --tail 200 api | grep -i "metrics\|refresh\|operational"
   ```
2. Common causes:
   - Database connection pool exhausted: check `pg_stat_activity` connection count.
   - Slow query: `PROMETHEUS_EXPENSIVE_DOMAIN_METRICS_ENABLED=true` with a very large
     table may time out. Set `PROMETHEUS_DOMAIN_METRICS_TTL_SECONDS` to a higher value,
     or disable expensive metrics.
   - Migration left a table in an inconsistent state: check for recent schema changes.
3. If the cause is a slow count query, temporarily set
   `PROMETHEUS_EXPENSIVE_DOMAIN_METRICS_ENABLED=false` and restart the API.
4. Confirm the metric recovers: `atlas_operational_metrics_refresh_success` should
   return to 1 within `PROMETHEUS_DOMAIN_METRICS_TTL_SECONDS` (default: 60 seconds).

**Escalate if:** Refresh stays failed after restarting the API, or if the API itself
starts returning 5xx errors on health endpoints.
