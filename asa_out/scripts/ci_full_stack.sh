#!/usr/bin/env bash
# Run the production-parity CI chain locally: PostGIS + Redis, Alembic
# upgrade/downgrade, pytest, and frontend type-check/lint.
set -euo pipefail

COMPOSE=${COMPOSE:-"docker compose"}
DB_URL=${DATABASE_URL:-"postgresql+asyncpg://atlas:atlas@localhost:5432/atlas"}
REDIS_URL=${RATE_LIMIT_STORAGE_URL:-"redis://localhost:6379/0"}
PYTHONPATH=${PYTHONPATH:-src}
export PYTHONPATH DATABASE_URL="$DB_URL" RATE_LIMIT_STORAGE_URL="$REDIS_URL" APP_ENV=${APP_ENV:-test} API_AUTH_ENABLED=${API_AUTH_ENABLED:-false} METRICS_PUBLIC_OK=${METRICS_PUBLIC_OK:-true}

echo "==> Starting PostGIS + Redis"
$COMPOSE up -d db redis

echo "==> Waiting for services"
for i in $(seq 1 60); do
  db_ok=0; redis_ok=0
  $COMPOSE ps db | grep -q healthy && db_ok=1 || true
  $COMPOSE ps redis | grep -q healthy && redis_ok=1 || true
  if [ "$db_ok" = 1 ] && [ "$redis_ok" = 1 ]; then
    break
  fi
  sleep 2
  if [ "$i" = 60 ]; then
    echo "Services did not become healthy" >&2
    $COMPOSE logs db redis | tail -100 >&2
    exit 1
  fi
done

echo "==> Verifying Redis connectivity"
python - <<'PY'
import os
import redis
url = os.environ["RATE_LIMIT_STORAGE_URL"]
client = redis.Redis.from_url(url)
assert client.ping() is True
print(f"Redis ok: {url}")
PY

echo "==> Alembic upgrade head"
alembic upgrade head

echo "==> Alembic downgrade/upgrade smoke"
alembic downgrade -1
alembic upgrade head

echo "==> Backend pytest"
pytest --tb=short -q

if [ -f web/package-lock.json ]; then
  echo "==> Frontend npm ci + type-check + lint"
  (cd web && npm ci && npm run type-check && npm run lint)
else
  echo "No web/package-lock.json found; skipping frontend checks"
fi

echo "==> CI full stack completed"
