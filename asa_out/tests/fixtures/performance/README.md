# Performance fixture profiles

Large performance fixture CSVs are intentionally **not** committed. Generate them with:

```bash
python scripts/generate_large_data_fixture.py \
  --profile tests/fixtures/performance/profiles/smoke.json \
  --output-dir .generated/perf/smoke
```

Profiles:

| Profile | NTSB rows | ASN-like rows | Purpose |
|---|---:|---:|---|
| `smoke.json` | 500 | 50 | Fast local/CI verification of fixture generation |
| `local-10k.json` | 10,000 | 1,000 | Local endpoint profiling |
| `nightly-100k.json` | 100,000 | 10,000 | Nightly/staging scale checks |

All generated data is synthetic. The ASN-like file is a deterministic overlap fixture; it is not real ASN data.
