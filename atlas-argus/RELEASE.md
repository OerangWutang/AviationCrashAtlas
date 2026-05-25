# Atlas Safety Analysis — Release Verification

This document records the production readiness verification performed prior to
v0.2.0 deployment. Update it before each release.

---

## Verification Summary

| Gate | Status | Evidence |
|------|--------|----------|
| Compile check (`compileall`) | ✅ Pass | 0 errors across `src/`, `tests/`, `alembic/` |
| Ruff lint | ✅ Pass | 586 files, 0 violations |
| Ruff format | ✅ Pass | 586 files, no diff |
| mypy strict | ✅ Pass | 202 source files, 0 type errors |
| Unit + API suite | ✅ Pass | 1330 passed, 2 skipped, ~5s |
| Release tree check | ✅ Pass | No egg-info/pyc artefacts in committed tree |
| Wheel build | ✅ Pass | `atlas_backend-0.2.0-py3-none-any.whl` |
| Lock files | ✅ Pass | 103 prod / 178 dev pinned packages |
| README Alembic head | ✅ Pass | Declares `049_fk_covering_indexes`; matches actual head |
| Migration round-trip | ✅ Pass | `downgrade base → upgrade head` clean on PostGIS 16.x |
| Integration tests | ✅ Pass | 45/45 against live PostGIS 16 + RLS enforcement verified |
| App role cannot bypass RLS | ✅ Pass | `rolbypassrls = f` confirmed for `atlas_app_test` |
| Secret scan | ✅ Pass | No hardcoded credentials; no committed `.env` files |
| Deploy scripts | ✅ Pass | `check-env.sh` validates passwords, ALLOWED_HOSTS, Prometheus CIDRs |
| Gunicorn config | ✅ Pass | Non-root, uvicorn workers, keepalive < ALB default, max_requests jitter |
| Dockerfile static | ✅ Pass | Multi-stage, non-root uid 1001, no secrets, `/dev/shm` tmp |
| CVE scan (Python deps) | ⚠️ Known | See [Known Vulnerabilities](#known-vulnerabilities) |
| Base image digest | ⚠️ Unpinned | See [Pre-Deploy Checklist](#pre-deploy-checklist) |
| Docker build | ⏳ Pending | Requires CI runner with Docker Hub access |
| Trivy image scan | ⏳ Pending | Requires built image |
| Trivy filesystem scan | ⏳ Pending | Requires CI runner |
| Gitleaks | ⏳ Pending | Binary unavailable in verification environment; manual scan clean |

---

## Known Vulnerabilities

### PYSEC-2026-161 — starlette 0.52.1 (Host header path injection)

- **Severity:** Medium
- **Fix version:** starlette 1.0.1
- **Status:** Blocked — `prometheus-fastapi-instrumentator 7.1.0` pins `starlette<1.0.0`
- **Mitigation:** `TrustedHostMiddleware` validates the `Host` header before URL
  reconstruction on every request. This is enforced structurally in production
  by `validate_api_runtime_settings()`, which raises on startup if `allowed_hosts`
  is empty (dev default `["*"]` is rejected in `is_production` mode).
- **Action:** Upgrade starlette when `prometheus-fastapi-instrumentator` releases
  support for starlette ≥ 1.0.0. Track at: https://github.com/trallnag/prometheus-fastapi-instrumentator/issues

### CVE-2026-45409 — idna 3.14 (ReDoS)

- **Status:** ✅ Fixed — upgraded to `idna==3.16` in `requirements.txt` and
  `requirements-dev.txt`.

---

## Known Incomplete Features

The following endpoints accept a parameter but return HTTP 501 when it is used:

| Endpoint | Parameter | Status |
|----------|-----------|--------|
| `GET /api/v1/conflicts/{id}/history` | `include_archive=true` | Not implemented |
| `GET /api/v1/accidents/{id}/provenance` | `include_archive=true` | Not implemented |

Both return a clean 501 response with a human-readable message. The parameter
is documented in the OpenAPI schema with a note. No crash or data leak occurs.

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

- [ ] **Pin base image digest.** After the first successful Docker build in CI:
  ```
  docker inspect --format='{{index .RepoDigests 0}}' python:3.12-slim
  ```
  Replace both `FROM python:3.12-slim` lines in `Dockerfile` with
  `FROM python:3.12-slim@sha256:<digest>` and commit.

- [ ] **Docker build passes in CI.** The `docker-build` job must succeed on the
  release branch. This verifies the two-stage build, non-root user, and
  production dependency installation.

- [ ] **Trivy image scan is clean.** The `docker-build` job runs Trivy against
  the built image at `CRITICAL,HIGH` severity with `--ignore-unfixed`. Review
  any new findings and either patch or document them here.

- [ ] **Trivy filesystem scan is clean.** The `security-scans` job runs Trivy in
  filesystem mode. Review findings.

- [ ] **Gitleaks passes.** The `security-scans` job runs Gitleaks over the full
  git history. Any finding must be remediated (rotate the credential, rewrite
  history, add to `.gitleaksignore` with justification).

- [ ] **Run `deploy/free/check-env.sh`** against the production `.env` before
  starting the stack. It validates passwords, ALLOWED_HOSTS, TLS, Prometheus
  CIDR, and DB role separation.

- [ ] **Apply migrations** on the production database before swapping traffic:
  ```
  alembic upgrade head
  ```
  Verify with `alembic current` that head is `049_fk_covering_indexes`.

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
```
