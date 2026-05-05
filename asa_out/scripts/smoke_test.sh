#!/usr/bin/env bash
# smoke_test.sh — end-to-end health check for AviationSafetyAtlas.
#
# Requires: docker, docker compose, curl, python3
# Usage:    ./scripts/smoke_test.sh
#
# Exit codes: 0 all checks passed / 1 one or more failed
#
# Proves the boring guarantees:
#   1. PostGIS and Redis start and are healthy
#   2. Alembic migrations run through head (all versions)
#   3. Source seed loads
#   4. API starts
#   5. /health returns ok
#   6. /sources returns seeded sources
#   7. Fixture CSV ingests (tests/fixtures/ntsb_sample.csv)
#   8. Projection runs
#   9. /accidents total > 0 after ingestion
#  10. /accidents/{id} returns detail
#  11. /accidents/{id}/provenance returns claims
#  12. /accidents/map returns response envelope {items,count,truncated,limit}
#  13. /analytics/summary returns valid shape
#  14. Frontend TypeScript type-check and lint pass (mandatory when npm present)

set -euo pipefail

COMPOSE="docker compose"
API_URL="${API_URL:-http://localhost:8000}"
MAX_WAIT=90
PASS=0; FAIL=0
FIRST_ID=""

green() { printf "\033[0;32m✓ %s\033[0m\n" "$*"; }
red()   { printf "\033[0;31m✗ %s\033[0m\n" "$*"; }
info()  { printf "\033[0;34m→ %s\033[0m\n" "$*"; }
warn()  { printf "\033[0;33m⚠ %s\033[0m\n" "$*"; }

pass() { green "$1"; PASS=$((PASS+1)); }
fail() { red "$1"; FAIL=$((FAIL+1)); }

wait_for() {
  local label="$1" cmd="$2"; local elapsed=0
  until eval "$cmd" > /dev/null 2>&1; do
    sleep 2; elapsed=$((elapsed+2))
    if [ $elapsed -ge $MAX_WAIT ]; then return 1; fi
  done
  return 0
}

# 1. Database + Redis
info "Starting database and Redis..."
$COMPOSE up -d db redis
info "Waiting for DB healthy (max ${MAX_WAIT}s)..."
if wait_for "db healthy" "$COMPOSE ps db | grep -q healthy"; then
  pass "Database healthy"
else
  fail "Database not healthy after ${MAX_WAIT}s"; $COMPOSE logs db | tail -20; exit 1
fi
info "Waiting for Redis healthy (max ${MAX_WAIT}s)..."
if wait_for "redis healthy" "$COMPOSE ps redis | grep -q healthy"; then
  pass "Redis healthy"
else
  fail "Redis not healthy after ${MAX_WAIT}s"; $COMPOSE logs redis | tail -20; exit 1
fi

# 2. Migrations
info "Running Alembic migrations..."
if $COMPOSE run --rm migrate; then
  pass "Migrations completed"
else
  fail "Migrations FAILED"; exit 1
fi

# 3. Seed
info "Seeding source registry..."
if $COMPOSE run --rm api atlas db seed 2>&1; then
  pass "Sources seeded"
else
  fail "Source seed FAILED"
fi

# 4. Start API
info "Starting API..."
$COMPOSE up -d api
info "Waiting for API reachable (max ${MAX_WAIT}s)..."
if wait_for "api health" "curl -sf ${API_URL}/api/v1/health"; then
  pass "API reachable"
else
  fail "API not reachable after ${MAX_WAIT}s"; $COMPOSE logs api | tail -30
fi

# 5. Health
HEALTH=$(curl -sf "${API_URL}/api/v1/health" 2>/dev/null || echo "{}")
if echo "$HEALTH" | grep -q '"status":"ok"'; then
  pass "/health → ok"
else
  fail "/health returned: $HEALTH"
fi

# 6. Sources
SOURCES=$(curl -sf "${API_URL}/api/v1/sources" 2>/dev/null || echo "[]")
SC=$(echo "$SOURCES" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo 0)
if [ "$SC" -gt 0 ]; then pass "/sources → $SC sources"; else fail "/sources → 0 sources"; fi

# 7. Fixture ingestion
info "Ingesting tests/fixtures/ntsb_sample.csv..."
FIXTURE="$(pwd)/tests/fixtures/ntsb_sample.csv"
if [ ! -f "$FIXTURE" ]; then
  fail "Fixture not found: $FIXTURE"
elif $COMPOSE run --rm -v "${FIXTURE}:/tmp/ntsb_sample.csv:ro" api      atlas ingest csv /tmp/ntsb_sample.csv 2>&1; then
  pass "Fixture ingestion completed"
else
  fail "Fixture ingestion FAILED"
fi

# 8. Projection
info "Running projection rebuild..."
if $COMPOSE run --rm api atlas reproject 2>&1; then
  pass "Projection completed"
else
  fail "Projection FAILED"
fi

# 9. Accidents list — must be non-empty
ACC=$(curl -sf "${API_URL}/api/v1/accidents" 2>/dev/null || echo "{}")
ACC_TOTAL=$(echo "$ACC" | python3 -c "import sys,json; print(json.load(sys.stdin).get('total',0))" 2>/dev/null || echo 0)
FIRST_ID=$(echo "$ACC" | python3 -c "import sys,json; items=json.load(sys.stdin).get('items',[]); print(items[0]['id'] if items else '')" 2>/dev/null || echo "")
if [ "$ACC_TOTAL" -gt 0 ]; then
  pass "/accidents → total=$ACC_TOTAL"
else
  fail "/accidents → total=0 (ingestion or projection may have failed)"
fi

# 10. Accident detail
if [ -n "$FIRST_ID" ]; then
  DETAIL=$(curl -sf "${API_URL}/api/v1/accidents/${FIRST_ID}" 2>/dev/null || echo "{}")
  if echo "$DETAIL" | grep -q '"canonical_id"'; then
    pass "/accidents/$FIRST_ID → detail returned"
  else
    fail "/accidents/$FIRST_ID → unexpected response"
  fi
else
  warn "Skipping detail check (no accidents)"
fi

# 11. Provenance
if [ -n "$FIRST_ID" ]; then
  PROV=$(curl -sf "${API_URL}/api/v1/accidents/${FIRST_ID}/provenance" 2>/dev/null || echo "{}")
  CC=$(echo "$PROV" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('claims',[])))" 2>/dev/null || echo 0)
  if [ "$CC" -gt 0 ]; then pass "/accidents/$FIRST_ID/provenance → $CC claims"
  else fail "/accidents/$FIRST_ID/provenance → 0 claims"; fi
else
  warn "Skipping provenance check (no accidents)"
fi

# 12. Map endpoint — must return the v28.2+ response envelope {items, count, truncated, limit}
#     The old check asserted isinstance(d, list), which broke when the endpoint was
#     changed to return a capped wrapper object.  Now assert the envelope shape.
MAP=$(curl -sf "${API_URL}/api/v1/accidents/map" 2>/dev/null || echo "null")
if echo "$MAP" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert isinstance(d, dict), f'expected dict, got {type(d).__name__}'
assert 'items' in d, 'missing items key'
assert 'truncated' in d, 'missing truncated key'
assert 'limit' in d, 'missing limit key'
assert isinstance(d['items'], list), 'items must be a list'
" 2>/dev/null; then
  MC=$(echo "$MAP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('count',0))" 2>/dev/null || echo 0)
  TRUNC=$(echo "$MAP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('truncated',False))" 2>/dev/null || echo False)
  pass "/accidents/map → envelope ok, count=$MC truncated=$TRUNC"
else
  fail "/accidents/map → expected response envelope {items,count,truncated,limit}, got: ${MAP:0:200}"
fi

# 13. Analytics
ANA=$(curl -sf "${API_URL}/api/v1/analytics/summary" 2>/dev/null || echo "{}")
if echo "$ANA" | grep -q '"total_accidents"'; then
  AT=$(echo "$ANA" | python3 -c "import sys,json; print(json.load(sys.stdin).get('total_accidents',0))" 2>/dev/null || echo 0)
  pass "/analytics/summary → total_accidents=$AT"
else
  fail "/analytics/summary → unexpected: ${ANA:0:200}"
fi

# 14. Frontend type-check
info "Running frontend TypeScript type-check..."
if ! command -v npm > /dev/null 2>&1; then
  warn "npm not found — skipping type-check (install Node.js to enable)"
else
  if [ ! -d "web/node_modules" ]; then
    info "Installing frontend dependencies..."
    (cd web && npm ci --silent 2>&1) && info "Done" || { fail "npm ci FAILED"; }
  fi
  if [ -d "web/node_modules" ]; then
    if (cd web && npx tsc --noEmit 2>&1); then
      pass "Frontend TypeScript type-check PASSED"
    else
      fail "Frontend TypeScript type-check FAILED"
    fi
    if (cd web && npm run lint 2>&1); then
      pass "Frontend lint PASSED"
    else
      fail "Frontend lint FAILED"
    fi
  fi
fi

# Summary
echo ""
printf "Results: %d passed, %d failed\n" "$PASS" "$FAIL"
echo ""
if [ $FAIL -gt 0 ]; then
  red "Smoke test FAILED ($FAIL check(s))"
  echo "  docker compose logs api      — API logs"
  echo "  docker compose logs migrate  — migration logs"
  exit 1
else
  green "Smoke test PASSED (all $PASS checks)"
  exit 0
fi
