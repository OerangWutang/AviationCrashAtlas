# Large-data performance fixtures

Aviation Safety Atlas does **not** commit large generated CSVs. Instead, the repo includes a deterministic fixture generator that produces synthetic NTSB-like and ASN-like CSV files on demand.

The generated data is designed to stress the parts of the system that have historically been risky:

- map point queries in dense geographic regions
- low-zoom map clustering
- analytics aggregation over many projected records
- search pagination and sorting
- multi-source overlap/conflict paths
- crew/passenger injury split projection

All generated rows are synthetic. They are not real NTSB, ASN, ICAO, or accident records.

## Profiles

| Profile | NTSB rows | ASN-like rows | Purpose |
|---|---:|---:|---|
| `smoke` | 500 | 50 | Fast local generation and dry-run verification |
| `local-10k` | 10,000 | 1,000 | Local performance profiling |
| `nightly-100k` | 100,000 | 10,000 | Nightly or staging-scale checks |

Profile files are stored in:

```text
 tests/fixtures/performance/profiles/
```

## Generate fixtures

Smoke fixture:

```bash
python scripts/generate_large_data_fixture.py \
  --profile smoke \
  --output-dir .generated/perf/smoke
```

Local 10k fixture:

```bash
python scripts/generate_large_data_fixture.py \
  --profile local-10k \
  --output-dir .generated/perf/local-10k
```

Nightly/staging profile:

```bash
python scripts/generate_large_data_fixture.py \
  --profile nightly-100k \
  --output-dir .generated/perf/nightly-100k
```

You can override row counts without editing profile files:

```bash
python scripts/generate_large_data_fixture.py \
  --profile local-10k \
  --output-dir .generated/perf/custom \
  --ntsb-rows 25000 \
  --asn-rows 2500 \
  --seed 123
```

Each output directory contains:

```text
ntsb_large.csv
asn_like_large.csv
manifest.json
README.md
```

## Load fixtures into a local database

Start the database and run migrations first:

```bash
docker compose up -d db
PYTHONPATH=src alembic upgrade head
PYTHONPATH=src atlas db seed
```

Then ingest the generated files:

```bash
PYTHONPATH=src atlas ingest csv .generated/perf/local-10k/ntsb_large.csv
PYTHONPATH=src atlas ingest generic-csv .generated/perf/local-10k/asn_like_large.csv --mapping asn
PYTHONPATH=src atlas reproject
```

The ASN-like fixture intentionally overlaps the NTSB-like fixture by registration/date. Some rows intentionally disagree on fatality counts, which is useful for exercising conflict creation and projection withholding at scale.

## Run HTTP performance smoke checks

Start the API against the loaded database:

```bash
PYTHONPATH=src atlas serve
```

Then run:

```bash
python scripts/performance_smoke.py --base-url http://localhost:8000
```

The smoke runner checks representative endpoints:

- bounded high-zoom map points
- low-zoom map clusters
- analytics summary
- first accident list page

It prints JSON and exits non-zero if a route exceeds its threshold or returns a server error.

## Suggested manual performance gates

These are intentionally conservative local/staging targets, not universal guarantees:

| Endpoint | Fixture | Suggested threshold |
|---|---:|---:|
| `/api/v1/accidents/map` high zoom bounded viewport | 10k rows | < 750 ms |
| `/api/v1/accidents/map` low zoom clusters | 10k rows | < 750 ms |
| `/api/v1/analytics/summary` | 10k rows | < 1500 ms |
| `/api/v1/accidents?limit=50` | 10k rows | < 1000 ms |

Nightly/staging thresholds should be calibrated after recording baseline timings on production-like hardware.

## CI strategy

Normal CI should validate the generator with small row counts only. Do not ingest 10k/100k rows in every pull request.

Recommended split:

1. Pull requests: run generator unit tests only.
2. Nightly: generate `local-10k`, ingest, run `performance_smoke.py`.
3. Weekly/staging: generate `nightly-100k`, ingest, run smoke and store timings.

## Storage and hygiene

Generated fixture directories must stay out of git. Use one of:

```text
.generated/perf/
perf-data/
```

Both are ignored by `.gitignore`.

## Run ingestion performance timing

The HTTP smoke runner measures serving behavior after data is loaded. To time
real ingestion and projection rebuilds against generated fixtures, use:

```bash
PYTHONPATH=src python scripts/performance_ingest.py \
  --ntsb-csv .generated/perf/local-10k/ntsb_large.csv \
  --generic-csv .generated/perf/local-10k/asn_like_large.csv \
  --mapping asn \
  --reproject \
  --json
```

For the committed smoke profile:

```bash
make perf-ingest-smoke
```

This prints timing JSON for NTSB ingestion, ASN-like ingestion, and optional
full reprojection. Use the results to compare PRs, nightly runs, and staging
hardware. The script intentionally does not enforce universal thresholds; data
volume, database hardware, and PostGIS configuration vary too much. Nightly CI
or staging should define environment-specific gates.
