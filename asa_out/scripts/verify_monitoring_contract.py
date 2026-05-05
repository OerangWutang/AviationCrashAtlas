#!/usr/bin/env python3
"""Verify that the running API emits the metrics required by docs/alerts.md.

This is intentionally lightweight: it scrapes /metrics and asserts the metric
families used by the shipped alert rules/dashboards are present. It prevents
monitoring docs from drifting into aspirational fiction.
"""
from __future__ import annotations

import argparse
import sys
import urllib.request

REQUIRED_METRICS = {
    "atlas_http_requests_total",
    "atlas_http_request_duration_seconds",
    "atlas_http_requests_in_flight",
    "atlas_map_truncation_total",
    "atlas_provenance_truncation_total",
    "atlas_projection_rebuilds_total",
    "atlas_ingestion_last_success_timestamp_seconds",
    "atlas_ingestion_runs_total",
    "atlas_conflicts_open_total",
    "atlas_conflicts_oldest_open_age_seconds",
    "atlas_duplicate_candidates_pending_total",
    "atlas_data_quality_issues_open_total",
    "atlas_archive_manifests_total",
    "atlas_archive_last_success_timestamp_seconds",
    "atlas_source_documents_unverified_total",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--metrics-token", default=None)
    args = parser.parse_args()
    req = urllib.request.Request(args.base_url.rstrip("/") + "/metrics")
    if args.metrics_token:
        req.add_header("Authorization", f"Bearer {args.metrics_token}")
    text = urllib.request.urlopen(req, timeout=10).read().decode("utf-8", "replace")
    missing = sorted(m for m in REQUIRED_METRICS if m not in text)
    if missing:
        print("Missing required metric families:")
        for metric in missing:
            print(f"- {metric}")
        return 1
    print(f"Monitoring contract OK: {len(REQUIRED_METRICS)} metric families present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
