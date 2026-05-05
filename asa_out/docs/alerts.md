# Aviation Safety Atlas — Alerting Guide

This guide defines the first production alerting baseline for Aviation Safety
Atlas. It intentionally separates alerts that are supported by metrics emitted
by the current application from alerts that are recommended but require future
metrics.

## Assumptions

Prometheus scrapes the application `/metrics` endpoint with a job label such as:

```yaml
scrape_configs:
  - job_name: aviation-safety-atlas
    bearer_token: ${ATLAS_METRICS_TOKEN}
    static_configs:
      - targets: ["atlas-api:8000"]
```

The examples below assume:

```promql
job="aviation-safety-atlas"
```

If your scrape job uses a different label, update the expressions accordingly.

## Alert severity model

| Severity | Meaning | Expected response |
|---|---|---|
| `page` | User-visible outage, data trust risk, or production safety control failure | Immediate response |
| `ticket` | Degradation, capacity risk, or growing operational backlog | Next business day or current sprint |
| `info` | Operational signal worth tracking, not immediately actionable | Trend review |

## Supported production alerts

The following alerts use metrics or scrape signals available in this release.
The canonical rule file is:

```text
monitoring/prometheus/atlas-alerts.yml
```

### Atlas API down

Fires when Prometheus cannot scrape the service.

```promql
up{job="aviation-safety-atlas"} == 0
```

### High 5xx rate

Fires when more than 2% of requests return 5xx for 10 minutes.

```promql
sum(rate(atlas_http_requests_total{job="aviation-safety-atlas",status_code=~"5.."}[5m]))
/
sum(rate(atlas_http_requests_total{job="aviation-safety-atlas"}[5m]))
> 0.02
```

### High request latency

Fires when p95 request latency is above 2 seconds for 10 minutes.

```promql
histogram_quantile(
  0.95,
  sum(rate(atlas_http_request_duration_seconds_bucket{job="aviation-safety-atlas"}[5m])) by (le)
) > 2
```

### Sustained in-flight request buildup

Fires when in-flight requests remain elevated for 10 minutes.

```promql
avg_over_time(atlas_http_requests_in_flight{job="aviation-safety-atlas"}[10m]) > 25
```

Tune the threshold for deployment size. A small single-worker deployment should
use a much lower threshold.

### Map truncation spike

Fires when the map endpoint frequently hits its response cap. This usually means
clients are not using viewport bounds, the bounds are too large, or clustering is
not being used aggressively enough.

```promql
sum(rate(atlas_map_truncation_total{job="aviation-safety-atlas"}[15m])) > 0.1
```

### Provenance truncation spike

Fires when provenance sections frequently hit caps. This may indicate a highly
contested event, ingestion duplication, missing pagination/export workflows, or
review backlog pressure.

```promql
sum(rate(atlas_provenance_truncation_total{job="aviation-safety-atlas"}[15m])) > 0.05
```

### Projection rebuild failures via API

Fires when conflict resolution triggers projection rebuild failures.

```promql
sum(rate(atlas_projection_rebuilds_total{job="aviation-safety-atlas",outcome="error"}[10m])) > 0
```

### No successful conflict resolutions recently

This is an optional workflow alert. It is useful only when the review team is
expected to resolve conflicts regularly.

```promql
sum(increase(atlas_conflict_resolutions_total{job="aviation-safety-atlas"}[7d])) == 0
```

Use this as `info` or `ticket`, not as a page.

## Readiness and health alerting

`/api/v1/readyz` is not exported as an application metric in this release.
Use one of these options:

1. Kubernetes readiness probes for local rollout control.
2. Prometheus blackbox exporter for alerting:

```yaml
- job_name: atlas-readyz
  metrics_path: /probe
  params:
    module: [http_2xx]
  static_configs:
    - targets:
        - https://atlas.example.com/api/v1/readyz
  relabel_configs:
    - source_labels: [__address__]
      target_label: __param_target
    - source_labels: [__param_target]
      target_label: instance
    - target_label: __address__
      replacement: blackbox-exporter:9115
```

Recommended alert:

```promql
probe_success{job="atlas-readyz"} == 0
```

## Additional database-backed alerts now supported

The `/metrics` endpoint refreshes several low-cardinality gauges from the
database at scrape time. The committed rule file includes examples for:

| Alert | Metric | Why it matters |
|---|---|---|
| Ingestion stale | `atlas_ingestion_last_success_timestamp_seconds{source=...}` | Detect stale data pipelines |
| Open conflict backlog too old | `atlas_conflicts_oldest_open_age_seconds` | Prevent unresolved disputes from lingering |
| Conflict backlog size | `atlas_conflicts_open_total` | Track reviewer workload |
| Duplicate candidate backlog | `atlas_duplicate_candidates_pending_total` | Track merge-review workload |
| Open data-quality issues | `atlas_data_quality_issues_open_total` | Surface split/total inconsistencies |

Still future-only: archive job failure counters and restore-drill metrics. Do
not alert on those until code emits them.

## Alert tuning notes

- Tune map/provenance truncation thresholds after observing real traffic for one
  to two weeks.
- Keep health/readiness alerts separate from HTTP 5xx alerts. A service can be
  reachable but not ready because migrations or Redis are broken.
- Do not page on lack of conflict resolutions unless a staffed review workflow
  exists.
- Avoid high-cardinality labels. Route labels must remain route templates such
  as `/api/v1/accidents/map`, not raw event IDs or random paths.

## Runbook links

| Alert | First checks |
|---|---|
| API down | container logs, database connectivity, deployment rollout, `/api/v1/health` |
| Readiness failing | `/api/v1/readyz`, Alembic head, NTSB source row, Redis connectivity |
| High 5xx rate | recent deploys, traceback logs, DB errors, Redis errors |
| High latency | DB query plans, map/provenance traffic, connection pool saturation |
| Map truncation spike | frontend viewport bounds, clustering zoom threshold, client filters |
| Provenance truncation spike | contested event volume, duplicate ingestion, need pagination/export |
| Projection rebuild failure | conflict resolution logs, claim state consistency, DB transaction errors |
