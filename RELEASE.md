# Atlas Safety Analysis — Release Verification

This document records the production readiness verification performed prior to
v0.2.0 deployment. Update it before each release.

---

## Verification Summary

| Gate | Status | Evidence |
|------|--------|----------|
| Compile check (`compileall`) | ✅ Pass | 0 errors across `src/`, `tests/`, `alembic/` |
| Ruff lint | ✅ Pass | 0 violations |
| Ruff format | ✅ Pass | No diff |
| mypy strict | ✅ Pass | 202 source files, 0 type errors |
| Unit + API suite | ✅ Pass | 1376 passed, 2 skipped |
| Release tree check | ✅ Pass | No egg-info/pyc artefacts in committed tree |
| Wheel build | ✅ Pass | `atlas_backend-0.2.0-py3-none-any.whl` |
| Lock files | ✅ Pass | 103 prod / 178 dev pinned packages |
| README Alembic head | ✅ Pass | Declares `049_fk_covering_indexes`; matches actual head |
| Alembic graph | ✅ Pass | Single head, 49 revisions, linear chain, no branches |
| Migration round-trip | ✅ Pass | `downgrade base → upgrade head` clean on PostGIS 16.x |
| Integration tests | ✅ Pass | 45/45 against live PostGIS 16 + RLS enforcement verified |
| App role cannot bypass RLS | ✅ Pass | `rolbypassrls = f` confirmed for `atlas_app_test` |
| ORM CHECK constraints vs domain enums | ✅ Pass | All `String(N)` status columns have matching `CheckConstraint` |
| Secret scan | ✅ Pass | No hardcoded credentials; no committed `.env` files |
| Deploy scripts | ✅ Pass | `check-env.sh` validates passwords, ALLOWED_HOSTS, Prometheus CIDRs |
| Gunicorn config | ✅ Pass | Non-root, uvicorn workers, keepalive < ALB default, max_requests jitter |
| Dockerfile static | ✅ Pass | Multi-stage, non-root uid 1001, no secrets, `/dev/shm` tmp |
| CVE scan (Python deps) | ⚠️ Known | See [Known Vulnerabilities](#known-vulnerabilities) |
| Base image digest | ✅ Pass | Pinned to python:3.12-slim@sha256:090ba77e2958f6af52a5341f788b50b032dd4ca28377d2893dcf1ecbdfdfe203 |
| Docker build | ✅ Pass | 2026-05-26: `docker build -t atlas-backend:release .` succeeded, image `sha256:76e571b11f013169d14bd3fc5bf1d5557baf6092ba3d2` |
| Trivy image scan | ✅ Pass | 2026-05-26: Trivy `image --severity CRITICAL,HIGH --ignore-unfixed atlas-backend:release` found 0 vulnerabilities |
| Trivy filesystem scan | ✅ Pass | 2026-05-26: Trivy `fs --severity CRITICAL,HIGH --ignore-unfixed .` found 0 vulnerabilities |
| Gitleaks | ✅ Pass | 2026-05-26: Gitleaks `detect --no-git --source . -v` found no leaks; scanned ~3.70 MB, no leaks found |

---

## Hardening Pass (post-v0.2.0)

The following fixes were applied after the initial v0.2.0 verification:

| # | Area | Change | Test(s) added |
|---|------|--------|---------------|
| 1 | API error responses | Strip Pydantic v2 `url` fields from 422 responses — prevents library-version leakage | `test_validation_error_does_not_include_pydantic_url_fields`, `test_validation_error_shape_is_correct_envelope` |
| 2 | Config / Prometheus | `PROMETHEUS_BEARER_TOKEN` minimum length (32 chars) enforced at Python startup — matches `check-env.sh` requirement so bypassing the shell preflight no longer silently accepts a weak token | `test_production_rejects_short_prometheus_bearer_token`, `test_production_accepts_strong_prometheus_bearer_token` |
| 3 | Hermes SSRF | Added redirect-to-private-IP regression test — a legitimate URL 301-redirecting to RFC-1918 address was not covered | `test_redirect_to_private_ip_is_blocked` |
| 4 | Hermes SSRF | Added redirect-loop termination test — infinite 302 loop must raise `HermesFetchSecurityError` after `_MAX_REDIRECTS` hops | `test_redirect_loop_is_capped_and_raises_security_error` |
| 5 | Hermes SSRF | Added direct-IP literal tests for IPv6 loopback (`::1`), RFC-1918, CGNAT, and IPv4-mapped IPv6 (`::ffff:192.168.1.1`) | `test_hermes_fetch_blocks_ipv6_loopback`, `test_hermes_fetch_blocks_rfc1918_ip_literal`, `test_hermes_fetch_blocks_cgnat_ip_literal`, `test_hermes_fetch_blocks_ipv4_mapped_ipv6_literal` |
| 6 | Metrics endpoint | Added HTTP-level test that `/metrics` returns 404 (not 403) for non-allowed callers — 403 would reveal the endpoint exists | `test_metrics_returns_404_for_external_ip`, `test_metrics_not_cached_on_denial` |
| 7 | Metrics access | Added `_metrics_request_allowed` unit tests covering all six branches (localhost/public IP/valid token/wrong token/no client/invalid IP string) | 6 tests in `test_security.py` |
| 8 | Config / CORS | Added explicit tests for `CORS_ORIGINS=*` wildcard rejection at field-validator time | `test_cors_origins_wildcard_rejected_at_parse_time`, `test_cors_origins_wildcard_in_string_rejected` |
| 9 | Auth cache | Added LRU cap eviction tests — verifies cache never exceeds `api_key_cache_max_entries` and the oldest entry is evicted | `TestAuthCacheLruEviction` (2 tests) |

---

## Known Vulnerabilities

### PYSEC-2026-161 / CVE-2026-48710 — starlette Host-header path injection ("BadHost")

- **Severity:** Medium (auth-bypass class; X41-2026-002 / GHSA-86qp-5c8j-p5mr)
- **Affected:** starlette < 1.0.1
- **Status:** ✅ Fixed — upgraded to `starlette==1.1.0` in `requirements.txt`,
  `requirements-dev.txt`, and `uv.lock`.
- **How the upgrade was unblocked:** `prometheus-fastapi-instrumentator 7.1.0`
  declares a defensive `starlette<1.0.0` cap, but imports only long-stable
  Starlette APIs (Request/Response, routing Route/Mount/Match, Headers, the ASGI
  `types` module) that are unchanged across 0.x → 1.0 → 1.1. The cap is lifted via
  `[tool.uv] override-dependencies = ["starlette>=1.0.1"]` in `pyproject.toml`,
  which `uv pip compile` (see `make lock`) applies when generating the lockfiles.
  Installs use `--no-deps` (Dockerfile, `make install`) so pip does not re-resolve
  and reject the pinned version against the instrumentator's metadata cap.
- **Defense-in-depth (retained):** `TrustedHostMiddleware` validates the `Host`
  header on every request, enforced structurally in production by
  `validate_api_runtime_settings()` (raises on startup if `allowed_hosts` is empty;
  the dev default `["*"]` is rejected in `is_production` mode). This now backs up a
  patched dependency rather than serving as the sole mitigation.
- **Follow-up:** remove the `[tool.uv] override-dependencies` entry once
  `prometheus-fastapi-instrumentator` publishes a release permitting starlette≥1.0.0.
  Track at: https://github.com/trallnag/prometheus-fastapi-instrumentator/issues

### CVE-2026-45409 — idna 3.14 (ReDoS)

- **Status:** ✅ Fixed — upgraded to `idna==3.16` in `requirements.txt` and
  `requirements-dev.txt`.

---

## Archive retrieval (`include_archive`)

`include_archive` is wired to the retention soft-delete model (no separate
archive store exists). Behaviour by endpoint:

| Endpoint | Parameter | Status |
|----------|-----------|--------|
| `GET /api/v1/accidents/{id}/provenance` | `include_archive=true` | Implemented — surfaces retention-swept (soft-deleted) claims |
| `GET /api/v1/conflicts/{id}/history` | `include_archive=true` | Accepted, no-op — conflict-activity rows carry no `deleted_at` tombstone |

For provenance, soft-deleted claims (`deleted_at IS NOT NULL`, set by the
retention sweep) are hidden by default and re-included when
`include_archive=true`; `archive_available` reports whether any swept claims
exist for the event. For conflict-history the flag is accepted for API symmetry
but changes nothing (`archive_available` stays `false`). Neither endpoint
returns 501 any longer.

---

## Test Coverage

Overall coverage (unit + integration): **81%**

Modules below 50%:

| Module | Coverage | Note |
|--------|----------|------|
| `infrastructure/db/repositories/*` | 23–47% | SQL repo layer; happy paths covered by integration tests; error paths not exercised |
| `infrastructure/event_bus/outbox_worker.py` | 34% | Defensive exception paths marked `# pragma: no cover` |
| `presentation/cli/commands.py` | 32% | CLI wrapper; no automated tests |
| `presentation/cli/ntsb.py` | 0% | NTSB import CLI; no automated tests |
| `presentation/api/schemas/provenance.py` | 0% | Schema module not yet exercised by any test path |

The repository layer gap is structural: domain tests use `InMemoryUnitOfWork`
and integration tests exercise the SQL layer only through use-case call paths.
SQL error-handling branches (constraint violations, deadlocks, serialization
failures) are not covered.

---

## Pre-Deploy Checklist

Before tagging and deploying:

- [x] **Pin base image digest.** After the first successful Docker build in CI:
  ```
  docker inspect --format='{{index .RepoDigests 0}}' python:3.12-slim
  ```
  Replace both `FROM python:3.12-slim` lines in `Dockerfile` with
  `FROM python:3.12-slim@sha256:<digest>` and commit.

- [x] **Docker build passes in CI.** The `docker-build` job must succeed on the
  release branch. This verifies the two-stage build, non-root user, and
  production dependency installation.

- [x] **Trivy image scan is clean.** The `docker-build` job runs Trivy against
  the built image at `CRITICAL,HIGH` severity with `--ignore-unfixed`. Review
  any new findings and either patch or document them here.

- [x] **Trivy filesystem scan is clean.** The `security-scans` job runs Trivy in
  filesystem mode. Review findings.

- [x] **Gitleaks passes.** The `security-scans` job runs Gitleaks over the full
  git history. Any finding must be remediated (rotate the credential, rewrite
  history, add to `.gitleaksignore` with justification).

- [x] **API key secret rotation runbook is reviewed and accepted.** Before
  go-live, the on-call/operator team must review
  `ops/runbooks/api-key-hash-secret-rotation.md` and confirm ownership for
  emergency key rotation.

- [x] **Production cutover checklist is completed and signed.**
  Execute `ops/runbooks/production-cutover-checklist.md` end-to-end and record
  operator/security/release sign-off.

- [x] **Run `deploy/free/check-env.sh`** against the production `.env` before
  starting the stack. It validates passwords, ALLOWED_HOSTS, TLS, Prometheus
  CIDR, and DB role separation.

- [x] **Apply migrations** on the production database before swapping traffic:
  ```
  alembic upgrade head
  ```
  Verify with `alembic current` that head is `049_fk_covering_indexes`.

- [x] **Create first admin key if needed** after migrations:
  ```
  atlas bootstrap
  ```
  Store the printed API key immediately; only its hash is persisted. The
  CuratorOverride source itself is seeded by migrations.

- [x] **Prometheus alerts loaded.** Import
  `ops/alerts/prometheus-atlas-rules.yml` and verify alert delivery for warning
  and critical severity paths.

- [x] **Load-test baseline captured.** Run the staging k6 scenario and record
  p95/p99/error/backlog baselines in `ops/load/BASELINE_TEMPLATE.md`.

---

## CI Pipeline Structure

The `.github/workflows/ci.yml` pipeline enforces the following gate order:

```
lint-and-typecheck ─┬─ unit-tests ─┬─ docker-build (→ Trivy image scan)
lock-check ─────────┘              ├─ integration-tests
                                   └─ coverage (main only)
                    security-scans (Gitleaks + Trivy fs)
```

All blocking gates must be green before merging to `main`.

---

## How to Reproduce Verification

```bash
# Unit suite
PYTHONPATH=src pytest tests/domain/ tests/application/ tests/infrastructure/ tests/api/ \
  --no-cov -m "not integration and not release"

# Release tree check (run BEFORE pip install -e .)
pytest -m release

# Integration suite (requires PostGIS)
export TEST_DATABASE_URL="postgresql+asyncpg://atlas:atlas@localhost:5432/atlas_test"
export DATABASE_URL="$TEST_DATABASE_URL"
export ATLAS_ALLOW_DB_TRUNCATE=1 ATLAS_RLS_TEST_MUST_RUN=1
export API_KEY_HASH_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
pytest tests/integration/ --run-integration -m integration --no-cov

# CVE scan
pip-audit -r requirements.txt --no-deps

# Migration round-trip
alembic downgrade base && alembic upgrade head

# Docker image build + scan
docker build -t atlas-backend:release .
docker save atlas-backend:release -o /tmp/atlas-backend-release.tar
/tmp/trivy image --severity CRITICAL,HIGH --ignore-unfixed --input /tmp/atlas-backend-release.tar

# Filesystem scan
/tmp/trivy fs --severity CRITICAL,HIGH --ignore-unfixed .

# Secret scan (history + working tree)
/tmp/gitleaks detect --source . --verbose
/tmp/gitleaks dir . --verbose
```
