# Aviation Safety Atlas — Dashboard Guide

This document defines the first operational dashboard baseline. The reference
Grafana dashboard JSON lives at:

```text
monitoring/grafana/aviation-safety-atlas-overview.json
```

The dashboard intentionally uses only metrics emitted by the current release,
plus optional Prometheus scrape/blackbox signals where noted.

## Dashboard variables

| Variable | Purpose | Default |
|---|---|---|
| `job` | Prometheus scrape job for the Atlas API | `aviation-safety-atlas` |
| `interval` | Rate window for request panels | `5m` |

## Dashboard sections

### 1. Service health

Panels:

- API scrape status: `up{job="$job"}`
- In-flight requests: `atlas_http_requests_in_flight{job="$job"}`
- Optional readiness probe: `probe_success{job="atlas-readyz"}`

Use this section to answer: *is the service alive, ready, and overloaded?*

### 2. Traffic and errors

Panels:

- Request rate by status:

```promql
sum by (status_code) (rate(atlas_http_requests_total{job="$job"}[$interval]))
```

- 5xx percentage:

```promql
100 * sum(rate(atlas_http_requests_total{job="$job",status_code=~"5.."}[$interval]))
/
clamp_min(sum(rate(atlas_http_requests_total{job="$job"}[$interval])), 0.001)
```

- Top routes by request rate:

```promql
topk(10, sum by (path_template) (rate(atlas_http_requests_total{job="$job"}[$interval])))
```

Use this section to answer: *what is being called, and is it failing?*

### 3. Latency

Panels:

- p50 latency:

```promql
histogram_quantile(0.50, sum(rate(atlas_http_request_duration_seconds_bucket{job="$job"}[$interval])) by (le))
```

- p95 latency:

```promql
histogram_quantile(0.95, sum(rate(atlas_http_request_duration_seconds_bucket{job="$job"}[$interval])) by (le))
```

- p99 latency:

```promql
histogram_quantile(0.99, sum(rate(atlas_http_request_duration_seconds_bucket{job="$job"}[$interval])) by (le))
```

Use this section to answer: *is the API getting slow?*

### 4. Data-trust guardrails

Panels:

- Map truncation rate:

```promql
sum(rate(atlas_map_truncation_total{job="$job"}[$interval]))
```

- Provenance truncation rate by section:

```promql
sum by (section) (rate(atlas_provenance_truncation_total{job="$job"}[$interval]))
```

- Conflict resolution rate by type:

```promql
sum by (resolution_type) (rate(atlas_conflict_resolutions_total{job="$job"}[$interval]))
```

- Projection rebuild outcomes:

```promql
sum by (outcome) (rate(atlas_projection_rebuilds_total{job="$job"}[$interval]))
```

Use this section to answer: *are trust-preserving caps or rebuild failures
becoming operationally important?*

## Interpretation notes

### Map truncation

Occasional map truncation is acceptable. Sustained truncation means at least one
of these is true:

1. clients are not sending viewport bounds;
2. users are viewing too large a region at high zoom;
3. clustering threshold is too low;
4. `MAX_MAP_RESULTS` is too low for the chosen UX;
5. dense regions need server-side clustering or tiles.

### Provenance truncation

Provenance truncation should be rare. If it grows, investigate:

1. a highly contested accident;
2. duplicate ingestion creating repeated claims;
3. missing provenance pagination/export;
4. unreviewed conflict backlog.

### Projection rebuild failures

Any non-zero error rate is serious. Conflict resolution may have changed claim
state but failed to update the read projection, so operators should inspect logs
and verify event projection state.

## Planned dashboard panels requiring future metrics

Do not add these panels until the metrics exist:

| Panel | Required metric |
|---|---|
| Last successful ingestion by source | `atlas_ingestion_last_success_timestamp_seconds{source=...}` |
| Ingestion run outcomes | `atlas_ingestion_runs_total{source=...,status=...}` |
| Open conflict backlog | `atlas_conflicts_open_total` |
| Oldest open conflict age | `atlas_conflicts_oldest_open_age_seconds` |
| Archive run outcomes | `atlas_archive_runs_total{status=...}` |
| Last successful archive restore drill | `atlas_archive_last_restore_drill_timestamp_seconds` |

The current dashboard deliberately avoids panels that would silently stay empty
because the metrics are not emitted yet.

## Importing the Grafana dashboard

1. Open Grafana.
2. Go to **Dashboards → New → Import**.
3. Upload `monitoring/grafana/aviation-safety-atlas-overview.json`.
4. Select the Prometheus datasource.
5. Set the `job` variable to your Atlas scrape job if it is not
   `aviation-safety-atlas`.

## Dashboard maintenance rules

- Keep route labels as route templates, not raw paths.
- Do not add high-cardinality labels such as event IDs, source document IDs, or
  raw URLs.
- When adding a new alert, add a matching dashboard panel or annotation.
- When adding a new panel, document what action an operator should take when it
  looks bad.

## Additional operational panels

The latest operational workflow additions expose database-backed gauges at
scrape time. Add these panels to the overview dashboard or a reviewer-ops
subdashboard:

| Panel | Query | Notes |
|---|---|---|
| Ingestion freshness | `time() - max by (source) (atlas_ingestion_last_success_timestamp_seconds)` | Show age in seconds/hours per source |
| Ingestion outcomes | `atlas_ingestion_runs_total` | Table by `source` and `status` |
| Open conflict backlog | `atlas_conflicts_open_total` | Single stat |
| Oldest open conflict age | `atlas_conflicts_oldest_open_age_seconds` | Single stat with threshold at 7 days |
| Pending duplicate candidates | `atlas_duplicate_candidates_pending_total` | Reviewer merge workload |
| Open data-quality issues | `sum by (issue_code) (atlas_data_quality_issues_open_total)` | Split mismatch and future warnings |

These gauges are intentionally low-cardinality. Do not add event IDs, source
record IDs, or raw URLs as Prometheus labels.
