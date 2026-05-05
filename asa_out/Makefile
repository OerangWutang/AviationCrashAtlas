.PHONY: help install test lint migrate seed serve ingest-ntsb ingest-csv ingest-asn-csv reproject check-links perf-fixture-smoke perf-fixture-local perf-smoke perf-ingest-smoke metrics-contract map-explain docker-build ci-full e2e

PYTHONPATH := src

help:
	@echo "Aviation Safety Atlas"
	@echo "  install       pip install -e '.[dev]'"
	@echo "  test          run test suite (tests/ directory)"
	@echo "  lint          ruff check"
	@echo "  migrate       alembic upgrade head"
	@echo "  seed          seed source registry"
	@echo "  serve         start API server"
	@echo "  ingest-ntsb   START=YYYY-MM-DD END=YYYY-MM-DD"
	@echo "  ingest-csv    FILE=/path/to/AviationData.csv"
	@echo "  ingest-asn-csv FILE=/path/to/asn.csv (licensed/authorized export)"
	@echo "  reproject     rebuild all accident_records"
	@echo "  check-links   HEAD-check source document URLs"
	@echo "  perf-fixture-smoke  generate 500-row synthetic performance fixture"
	@echo "  perf-fixture-local  generate 10k-row synthetic performance fixture"
	@echo "  perf-smoke    run HTTP performance smoke checks (BASE_URL=http://localhost:8000)"
	@echo "  perf-ingest-smoke  time ingestion/projection on generated smoke fixture"
	@echo "  metrics-contract   assert /metrics exposes dashboard/alert metric families"
	@echo "  map-explain        EXPLAIN ANALYZE representative map bounding-box query"
	@echo "  e2e                run browser-level reviewer workflow smoke tests"
	@echo "  ci-full       run full local CI chain with PostGIS, Redis, Alembic, pytest, frontend checks"

install:
	pip install -e ".[dev]"

test:
	PYTHONPATH=$(PYTHONPATH) python -m pytest tests/ -v --tb=short

test-fast:
	PYTHONPATH=$(PYTHONPATH) python -m pytest tests/ -x -q

test-cov:
	PYTHONPATH=$(PYTHONPATH) python -m pytest tests/ --cov=atlas --cov-report=term-missing

lint:
	PYTHONPATH=$(PYTHONPATH) python -m ruff check src/ tests/

migrate:
	PYTHONPATH=$(PYTHONPATH) alembic upgrade head

seed:
	PYTHONPATH=$(PYTHONPATH) atlas db seed

serve:
	PYTHONPATH=$(PYTHONPATH) atlas serve

ingest-ntsb:
	@test -n "$(START)" || (echo "Usage: make ingest-ntsb START=2023-01-01 END=2023-12-31"; exit 1)
	@test -n "$(END)"   || (echo "Usage: make ingest-ntsb START=2023-01-01 END=2023-12-31"; exit 1)
	PYTHONPATH=$(PYTHONPATH) atlas ingest ntsb --start $(START) --end $(END)

ingest-csv:
	@test -n "$(FILE)" || (echo "Usage: make ingest-csv FILE=/path/to/AviationData.csv"; exit 1)
	PYTHONPATH=$(PYTHONPATH) atlas ingest csv $(FILE)

ingest-asn-csv:
	@test -n "$(FILE)" || (echo "Usage: make ingest-asn-csv FILE=/path/to/asn.csv"; exit 1)
	PYTHONPATH=$(PYTHONPATH) atlas ingest asn-csv $(FILE)

reproject:
	PYTHONPATH=$(PYTHONPATH) atlas reproject

check-links:
	PYTHONPATH=$(PYTHONPATH) atlas check-links

docker-build:
	docker build -t aviation-safety-atlas:latest .

docker-up:
	docker compose up -d db redis
	docker compose run --rm migrate
	docker compose up api


perf-fixture-smoke:
	PYTHONPATH=$(PYTHONPATH) python scripts/generate_large_data_fixture.py --profile smoke --output-dir .generated/perf/smoke

perf-fixture-local:
	PYTHONPATH=$(PYTHONPATH) python scripts/generate_large_data_fixture.py --profile local-10k --output-dir .generated/perf/local-10k

perf-smoke:
	PYTHONPATH=$(PYTHONPATH) python scripts/performance_smoke.py --base-url $(or $(BASE_URL),http://localhost:8000)

perf-ingest-smoke:
	PYTHONPATH=$(PYTHONPATH) python scripts/performance_ingest.py --ntsb-csv .generated/perf/smoke/ntsb_large.csv --generic-csv .generated/perf/smoke/asn_like_large.csv --mapping asn --reproject --json

metrics-contract:
	PYTHONPATH=$(PYTHONPATH) python scripts/verify_monitoring_contract.py --base-url $(or $(BASE_URL),http://localhost:8000)

map-explain:
	PYTHONPATH=$(PYTHONPATH) python scripts/explain_map_query.py

e2e:
	cd web && npm run e2e

ci-full:
	bash scripts/ci_full_stack.sh
