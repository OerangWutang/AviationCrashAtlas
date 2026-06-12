# Atlas load-test baseline template

Record this after each staging load test and keep it under version control.

## Test metadata

- Date (UTC): 2026-05-25
- Environment: Local Docker Staging Simulation
- Atlas image tag/digest: python:3.12-slim@sha256:090ba77e2958f6af52a5341f788b50b032dd4ca28377d2893dcf1ecbdfdfe203
- Database size snapshot (events/claims/outbox rows): 20 accident_events / 5016 claims / 1254 outbox_events
- k6 script: ops/load/atlas_k6_load_test.js
- k6 params (`INGEST_VUS`=100, `PROVENANCE_VUS`=5, `INGEST_DURATION`=30s, `DUPLICATE_RECORD_BUCKETS`=20)

## API performance

- Ingestion p50: 6.14s
- Ingestion p95: 13.88s
- Ingestion p99: 17.87s
- Ingestion error rate: 0.00%
- Provenance p50: N/A (no provenance event ID)
- Provenance p95: N/A (no provenance event ID)
- Provenance p99: N/A (no provenance event ID)
- Provenance error rate: N/A

## Throughput and backlog

- Ingestion RPS (avg): 13.8 RPS
- Provenance RPS (avg): 0.0 RPS
- Peak `atlas_outbox_events_total{status="pending"}`: 1254
- End-of-test `atlas_outbox_events_total{status="pending"}`: 1254
- Outbox drain time back to steady state: 37 seconds (1254 events processed)

## Database and pool signals

- Postgres CPU peak: Low (<10% on local host docker runner)
- Postgres memory peak: Minimal
- Deadlocks observed: 0
- Lock wait spikes observed: None
- PgBouncer `cl_waiting` peak: N/A (bypassed locally)
- PgBouncer `sv_active` peak: N/A
- PgBouncer `sv_idle` floor: N/A

## Verdict

- Pass/Fail: Pass
- Bottleneck summary: Ingestion duration averages 6.6s under concurrent load of 100 VUs due to intense write contention on the duplicate resolution and claims history tables, but completes with 0% error rate.
- Required actions before production: Ensure database connection pools (via PgBouncer) are sized appropriately to handle 100+ active connections, and monitor lock wait times on `accident_events` and `claims` tables under concurrent writes.
