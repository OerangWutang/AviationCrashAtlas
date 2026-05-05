#!/usr/bin/env python3
"""Small HTTP performance smoke runner for a running Atlas API.

This is intentionally lightweight and deterministic.  It does not replace a
real benchmark harness; it catches obvious regressions after loading generated
large-data fixtures.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from dataclasses import asdict, dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class CheckResult:
    name: str
    path: str
    status_code: int
    elapsed_ms: float
    threshold_ms: float
    ok: bool
    detail: str | None = None


def _timed_get(client: httpx.Client, path: str, threshold_ms: float, name: str) -> CheckResult:
    started = time.perf_counter()
    response = client.get(path)
    elapsed_ms = (time.perf_counter() - started) * 1000
    detail = None
    try:
        body: Any = response.json()
        if isinstance(body, dict):
            if "mode" in body:
                detail = f"mode={body.get('mode')} count={body.get('count')} truncated={body.get('truncated')}"
            elif "total_accidents" in body:
                detail = f"total_accidents={body.get('total_accidents')}"
            elif "total" in body:
                detail = f"total={body.get('total')}"
    except Exception:
        detail = response.text[:120]
    return CheckResult(
        name=name,
        path=path,
        status_code=response.status_code,
        elapsed_ms=round(elapsed_ms, 2),
        threshold_ms=threshold_ms,
        ok=response.status_code < 500 and elapsed_ms <= threshold_ms,
        detail=detail,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--api-key", default=None, help="Optional API key for protected endpoints")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--threshold-map-ms", type=float, default=750)
    parser.add_argument("--threshold-cluster-ms", type=float, default=750)
    parser.add_argument("--threshold-analytics-ms", type=float, default=1500)
    parser.add_argument("--threshold-list-ms", type=float, default=1000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    headers = {"X-API-Key": args.api_key} if args.api_key else {}
    checks = [
        (
            "map_points_dense_viewport",
            "/api/v1/accidents/map?north=27&south=25&east=-79&west=-81&zoom=9",
            args.threshold_map_ms,
        ),
        (
            "map_clusters_low_zoom",
            "/api/v1/accidents/map?north=50&south=20&east=-60&west=-130&zoom=4",
            args.threshold_cluster_ms,
        ),
        ("analytics_summary", "/api/v1/analytics/summary", args.threshold_analytics_ms),
        ("accident_list_first_page", "/api/v1/accidents?limit=50", args.threshold_list_ms),
    ]
    results: list[CheckResult] = []
    with httpx.Client(base_url=args.base_url.rstrip("/"), headers=headers, timeout=30) as client:
        for name, path, threshold_ms in checks:
            per_check = [
                _timed_get(client, path, threshold_ms=threshold_ms, name=name)
                for _ in range(max(args.repeat, 1))
            ]
            # Keep the worst iteration; that is what should gate smoke success.
            worst = max(per_check, key=lambda item: item.elapsed_ms)
            median = statistics.median(item.elapsed_ms for item in per_check)
            results.append(
                CheckResult(
                    name=worst.name,
                    path=worst.path,
                    status_code=worst.status_code,
                    elapsed_ms=worst.elapsed_ms,
                    threshold_ms=worst.threshold_ms,
                    ok=all(item.ok for item in per_check),
                    detail=f"worst={worst.elapsed_ms}ms median={round(median, 2)}ms {worst.detail or ''}".strip(),
                )
            )
    print(json.dumps([asdict(result) for result in results], indent=2, sort_keys=True))
    failed = [result for result in results if not result.ok]
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
