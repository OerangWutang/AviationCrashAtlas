#!/usr/bin/env python3
"""Run EXPLAIN for the production map bounding-box query.

Use after loading smoke/local/nightly fixtures to prove the lat/lon index is
actually used, or to justify moving to PostGIS geometry clustering.
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import text

from atlas.db.engine import direct_session

SQL = """
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT id, location_lat, location_lon
FROM accident_records
WHERE location_lat IS NOT NULL
  AND location_lon IS NOT NULL
  AND location_lat BETWEEN :south AND :north
  AND location_lon BETWEEN :west AND :east
ORDER BY occurred_at DESC NULLS LAST, id ASC
LIMIT :limit
"""


async def run(args: argparse.Namespace) -> None:
    async with direct_session() as session:
        rows = (await session.execute(text(SQL), {
            "north": args.north,
            "south": args.south,
            "east": args.east,
            "west": args.west,
            "limit": args.limit,
        })).all()
        for (line,) in rows:
            print(line)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--north", type=float, default=26.8)
    parser.add_argument("--south", type=float, default=25.5)
    parser.add_argument("--east", type=float, default=-79.7)
    parser.add_argument("--west", type=float, default=-81.0)
    parser.add_argument("--limit", type=int, default=5000)
    asyncio.run(run(parser.parse_args()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
