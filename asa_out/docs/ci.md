# Continuous integration and full-stack verification

This project has enough moving parts that syntax checks are not sufficient. CI is expected to prove the application against the same classes of services used in production:

- PostgreSQL with PostGIS
- Redis-backed rate-limit storage
- Alembic migrations, including a downgrade/upgrade smoke cycle
- backend pytest, including HTTP integration tests
- Docker Compose smoke testing
- frontend dependency install, lint, and TypeScript type-check

## GitHub Actions jobs

### `backend-unit`

Runs without external services. It installs the locked development dependencies, verifies lockfiles, checks repository hygiene, runs Ruff, and runs tests marked neither `integration` nor `performance`.

This job should catch fast unit-level failures and source hygiene problems before service containers are started.

### `backend-integration`

Starts real service containers:

```text
postgis/postgis:16-3.4-alpine
redis:7-alpine
```

Then it runs:

```bash
alembic upgrade head
alembic downgrade -1
alembic upgrade head
pytest -m integration --tb=short -q
pytest --tb=short -q
```

The integration environment sets:

```env
DATABASE_URL=postgresql+asyncpg://atlas:atlas@localhost:5432/atlas_test
RATE_LIMIT_ENABLED=true
RATE_LIMIT_STORAGE_URL=redis://localhost:6379/0
APP_ENV=test
API_AUTH_ENABLED=false
METRICS_PUBLIC_OK=true
```

This is the job that proves database migrations, Redis-backed rate limiting, readiness checks, route behavior, and HTTP contracts against real services.

### `smoke`

Runs `scripts/smoke_test.sh` against Docker Compose. This starts a clean Compose stack with PostGIS, Redis, migrations, API, ingestion, projection, endpoint checks, and frontend type-check/lint when Node is available.

### `frontend`

Runs:

```bash
cd web
npm ci
npm run type-check
npm run lint
```

## Local reproduction

Run the same full-stack verification locally with:

```bash
make ci-full
```

or directly:

```bash
bash scripts/ci_full_stack.sh
```

The script starts PostGIS and Redis using Docker Compose, verifies Redis connectivity, runs Alembic upgrade/downgrade/upgrade, runs pytest, then runs frontend `npm ci`, `type-check`, and `lint`.

## Test markers

`pyproject.toml` defines:

```text
integration: requires PostgreSQL/PostGIS and optional Redis
performance: generated fixture/performance smoke tests
```

Examples:

```bash
pytest -m "not integration and not performance"
pytest -m integration
pytest -m performance
```

## Non-negotiable CI guarantees

Before a build can be considered releasable, CI must prove:

1. locked Python dependencies are current;
2. no generated/cache artifacts are committed;
3. Ruff passes;
4. Alembic can upgrade to head;
5. latest migration can downgrade and upgrade again;
6. integration tests pass against real PostGIS;
7. Redis-backed rate-limit configuration is available and reachable;
8. frontend dependencies install reproducibly with `npm ci`;
9. TypeScript type-check passes;
10. frontend lint passes;
11. Docker Compose smoke test passes.
