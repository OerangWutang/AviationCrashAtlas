#!/usr/bin/env python3
"""Ingestion performance smoke runner for generated large-data fixtures.

This script runs real ingestion commands against a configured database and
prints simple timing JSON. It is intended for local/nightly/staging runs, not
for every PR.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

from atlas.claims.projection import ProjectionService
from atlas.db.engine import direct_session
from atlas.ingestion.generic_csv_adapter import SourceMapping, load_bundled_mapping
from atlas.ingestion.pipeline import IngestionPipeline


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    pipeline = IngestionPipeline()
    timings: dict[str, Any] = {"steps": []}

    t0 = time.perf_counter()
    ntsb = await pipeline.run_ntsb_csv(str(args.ntsb_csv))
    timings["steps"].append({
        "name": "ingest_ntsb_csv",
        "seconds": round(time.perf_counter() - t0, 3),
        "records": ntsb.records_fetched,
        "claims": ntsb.claims_written,
        "errors": len(ntsb.errors),
    })

    if args.generic_csv:
        if args.mapping and Path(args.mapping).exists():
            mapping = SourceMapping.from_file(args.mapping)
        else:
            mapping = load_bundled_mapping(args.mapping or "asn")
        t0 = time.perf_counter()
        generic = await pipeline.run_generic_csv(str(args.generic_csv), mapping, dry_run=False)
        timings["steps"].append({
            "name": "ingest_generic_csv",
            "seconds": round(time.perf_counter() - t0, 3),
            "records": generic.records_fetched,
            "claims": generic.claims_written,
            "errors": len(generic.errors),
        })

    if args.reproject:
        t0 = time.perf_counter()
        async with direct_session() as session:
            rebuilt, failed = await ProjectionService(session).rebuild_all(batch_size=args.batch_size)
        timings["steps"].append({
            "name": "reproject_all",
            "seconds": round(time.perf_counter() - t0, 3),
            "rebuilt": rebuilt,
            "failed": failed,
        })

    timings["total_seconds"] = round(sum(step["seconds"] for step in timings["steps"]), 3)
    return timings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ntsb-csv", required=True, type=Path)
    parser.add_argument("--generic-csv", type=Path)
    parser.add_argument("--mapping", default="asn")
    parser.add_argument("--reproject", action="store_true")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(_run(args))
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        for step in result["steps"]:
            print(f"{step['name']}: {step['seconds']}s {step}")
        print(f"total: {result['total_seconds']}s")


if __name__ == "__main__":
    main()
