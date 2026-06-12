"""Seed a synthetic 'NewsStream Pilot' second source to generate real conflicts.

Reads 500 high-quality NTSB projected records from the DB and submits a
second-source version of each through IngestSourceData. Variations are
modelled on documented early-reporting patterns in aviation journalism:

  fatalities_total    ±1-2 (early wire reports mis-count at scene)
  aircraft_make       capitalization / common alias variants
  registration        one transposed character (misread at scene)
  location_city       alternate city name or nearby location
  highest_injury_level one level off (boundary cases misclassified early)

The source is registered at reliability_tier=2 (below NTSB tier=1) so the
winner policy will favour NTSB on conflicts — reviewers can override.

Run: python scripts/seed_news_crawler_source.py [--limit N] [--dry-run]
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import typer

# Make sure atlas package is importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from atlas.application.dto import IngestionClaimDTO
from atlas.application.use_cases.ingest_source_data import IngestSourceData
from atlas.domain.entities import Source
from atlas.domain.enums import SourceKind
from atlas.infrastructure.db.unit_of_work import create_uow
from atlas.logging_config import setup_logging

SOURCE_NAME = "NewsStream Pilot"
SOURCE_TIER = 2  # less trusted than NTSB (tier 1)

# ---------------------------------------------------------------------------
# Variation rules — all modelled on real early-reporting patterns
# ---------------------------------------------------------------------------

# make → plausible news-source alias
_MAKE_ALIASES: dict[str, list[str]] = {
    "BOEING": ["Boeing", "Boeing Co.", "Boeing Aircraft"],
    "AIRBUS": ["Airbus Industries", "Airbus S.A.S.", "Airbus"],
    "AIRBUS INDUSTRIE": ["Airbus", "Airbus Industries"],
    "CESSNA": ["Cessna Aircraft", "Cessna", "Textron Cessna"],
    "PIPER": ["Piper Aircraft", "Piper", "New Piper Aircraft"],
    "BEECH": ["Beechcraft", "Beech Aircraft", "Textron Aviation"],
    "ROBINSON": ["Robinson Helicopter", "Robinson"],
    "BELL": ["Bell Helicopter", "Bell Textron", "Bell"],
    "SIKORSKY": ["Sikorsky Aircraft", "Lockheed Martin Sikorsky"],
}

# injury level adjacency for boundary mis-classification
_INJURY_ADJACENT: dict[str, str] = {
    "Fatal": "Serious",
    "Serious": "Fatal",
    "Minor": "Serious",
    "None": "Minor",
}

# Plausible nearby city alternates (city → list of alternates).
# Only a small curated set; the rest use a generic suffix variation.
_CITY_ALTERNATES: dict[str, list[str]] = {
    "Anchorage": ["Eagle River", "Wasilla"],
    "Miami": ["Opa-locka", "Hialeah", "Doral"],
    "Los Angeles": ["Burbank", "El Monte", "Compton"],
    "Chicago": ["Midway", "Schiller Park", "Rosemont"],
    "Atlanta": ["College Park", "East Point"],
    "Seattle": ["Renton", "Tukwila", "Kent"],
    "Dallas": ["Irving", "Grand Prairie", "Addison"],
    "Houston": ["Hobby", "Pearland", "La Porte"],
    "New York": ["Queens", "Jamaica", "East Elmhurst"],
    "Denver": ["Centennial", "Aurora", "Englewood"],
    "Phoenix": ["Scottsdale", "Tempe", "Mesa"],
    "Las Vegas": ["Henderson", "North Las Vegas"],
    "Orlando": ["Kissimmee", "Sanford", "Apopka"],
    "Memphis": ["Bartlett", "Germantown"],
    "Portland": ["Hillsboro", "Beaverton", "Gresham"],
    "Salt Lake City": ["West Jordan", "Murray", "Sandy"],
    "Nashville": ["Smyrna", "Murfreesboro"],
    "Honolulu": ["Kailua", "Kaneohe", "Pearl City"],
}


def _vary_registration(reg: str) -> str | None:
    """Transpose one character in the registration — simulates mis-read at scene."""
    if len(reg) < 4:
        return None
    # Pick a position that is not the prefix letter (keep country prefix intact)
    # Swap two adjacent non-hyphen characters somewhere in the middle/end.
    chars = list(reg)
    candidates = [
        i for i in range(1, len(chars) - 1)
        if chars[i].isalnum() and chars[i + 1].isalnum()
    ]
    if not candidates:
        return None
    i = random.choice(candidates)
    chars[i], chars[i + 1] = chars[i + 1], chars[i]
    varied = "".join(chars)
    return varied if varied != reg else None


def _vary_city(city: str) -> str | None:
    """Return an alternate city name, or None if no good variant exists."""
    if city in _CITY_ALTERNATES:
        return random.choice(_CITY_ALTERNATES[city])
    # Generic: append 'Airport Area' or 'County' to suggest a nearby location
    if len(city) > 3 and not city.endswith((" County", " Area", " Airport")):
        return random.choice([f"{city} Airport Area", f"{city} County"])
    return None


def build_news_claims(
    fields: dict,
    rng: random.Random,
) -> tuple[list[IngestionClaimDTO], dict[str, str]]:
    """Return (claims, conflict_map) where conflict_map describes what was varied."""
    claims: list[IngestionClaimDTO] = []
    conflicts: dict[str, str] = {}  # field_name -> description

    def add(name: str, value) -> None:
        if value is None:
            return
        claims.append(IngestionClaimDTO(field_name=name, field_value=value))

    # --- Always carry through agreement fields --------------------------------
    for passthrough in (
        "occurred_on", "event_date", "ntsb_number", "event_type",
        "occurred_time_local", "occurred_timezone",
        "location_state", "location_country",
        "aircraft_category", "amateur_built",
        "far_part", "engine_count",
        "latitude", "longitude",
        "light_condition", "ceiling_condition",
        "visibility_statute_miles", "wind_speed_knots",
    ):
        v = fields.get(passthrough)
        if v is not None:
            add(passthrough, v)

    # --- fatalities_total: ±1-2 for fatal accidents ---------------------------
    fatals = fields.get("fatalities_total")
    if fatals is not None:
        try:
            n = int(fatals)
        except (TypeError, ValueError):
            n = None
        if n is not None and n >= 2 and rng.random() < 0.65:
            delta = rng.choice([-2, -1, -1, 1, 1, 2])
            varied = max(0, n + delta)
            add("fatalities_total", varied)
            conflicts["fatalities_total"] = f"NTSB={n} NewsStream={varied}"
        else:
            add("fatalities_total", fatals)

    # Carry through injury counts as-is (only fatals vary)
    for field in ("serious_injuries_total", "minor_injuries_total", "uninjured_total"):
        v = fields.get(field)
        if v is not None:
            add(field, v)

    # --- highest_injury_level: adjacent mis-classification --------------------
    injury = fields.get("highest_injury_level")
    if injury is not None:
        if rng.random() < 0.30 and injury in _INJURY_ADJACENT:
            varied = _INJURY_ADJACENT[injury]
            add("highest_injury_level", varied)
            conflicts["highest_injury_level"] = f"NTSB={injury!r} NewsStream={varied!r}"
        else:
            add("highest_injury_level", injury)

    # --- aircraft_make: alias variation ----------------------------------------
    make = fields.get("aircraft_make")
    if make is not None:
        upper = make.upper() if make else ""
        aliases = _MAKE_ALIASES.get(upper) or _MAKE_ALIASES.get(make)
        if aliases and rng.random() < 0.45:
            varied = rng.choice(aliases)
            if varied != make:
                add("aircraft_make", varied)
                conflicts["aircraft_make"] = f"NTSB={make!r} NewsStream={varied!r}"
            else:
                add("aircraft_make", make)
        else:
            add("aircraft_make", make)

    # Pass through aircraft_type / model unchanged
    for field in ("aircraft_type", "aircraft_model", "aircraft_series",
                  "aircraft_damage", "total_seats"):
        v = fields.get(field)
        if v is not None:
            add(field, v)

    # --- registration: one transposition ---------------------------------------
    reg = fields.get("registration") or fields.get("registration_number")
    if reg is not None:
        if rng.random() < 0.25:
            varied = _vary_registration(str(reg))
            if varied:
                add("registration", varied)
                conflicts["registration"] = f"NTSB={reg!r} NewsStream={varied!r}"
            else:
                add("registration", reg)
        else:
            add("registration", reg)

    # --- location_city: alternate city ----------------------------------------
    city = fields.get("location_city")
    if city is not None:
        if rng.random() < 0.20:
            varied = _vary_city(str(city))
            if varied:
                add("location_city", varied)
                conflicts["location_city"] = f"NTSB={city!r} NewsStream={varied!r}"
            else:
                add("location_city", city)
        else:
            add("location_city", city)

    # --- nearest airport info (pass through) -----------------------------------
    for field in ("nearest_airport_name", "nearest_airport_distance_nm"):
        v = fields.get(field)
        if v is not None:
            add(field, v)

    # --- narrative: abbreviated version (news has less detail) ----------------
    narrative = fields.get("probable_cause_narrative") or fields.get("narrative")
    if narrative:
        text = str(narrative)
        # News summaries are shorter — take first 800 chars if longer
        summary = text[:800].rsplit(" ", 1)[0] + " [news summary]" if len(text) > 800 else text
        add("factual_narrative", summary)

    return claims, conflicts


def _idempotency_key(source_id: UUID, event_id: UUID, claims: list[IngestionClaimDTO]) -> str:
    material = json.dumps(
        {
            "source_id": str(source_id),
            "event_id": str(event_id),
            "claims": [c.model_dump(mode="json") for c in claims],
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    h = hashlib.sha256(material.encode()).hexdigest()
    return f"newsstream-pilot:{event_id}:{h[:16]}"


app = typer.Typer()


@app.command()
def seed(
    limit: int = typer.Option(500, help="Max events to seed"),
    min_fatalities: int = typer.Option(1, help="Minimum fatalities filter"),
    dry_run: bool = typer.Option(False, help="Print plan, do not write"),
    seed_val: int = typer.Option(42, help="RNG seed for reproducibility"),
) -> None:
    """Seed a synthetic NewsStream Pilot source to generate field-level conflicts."""
    setup_logging()
    asyncio.run(_run(limit=limit, min_fatalities=min_fatalities,
                     dry_run=dry_run, seed_val=seed_val))


async def _run(
    *,
    limit: int,
    min_fatalities: int,
    dry_run: bool,
    seed_val: int,
) -> None:
    from sqlalchemy import text
    from atlas.infrastructure.db.session import async_session_factory

    rng = random.Random(seed_val)

    # ------------------------------------------------------------------ #
    # 1. Load candidate projections                                        #
    # ------------------------------------------------------------------ #
    async with async_session_factory() as session:
        rows = await session.execute(
            text("""
                SELECT p.event_id, p.fields
                FROM projected_accident_records p
                WHERE
                  (p.fields->>'fatalities_total')::int >= :min_f
                  AND p.fields->>'registration'   IS NOT NULL
                  AND p.fields->>'aircraft_make'  IS NOT NULL
                  AND p.fields->>'occurred_on'    IS NOT NULL
                ORDER BY (p.fields->>'fatalities_total')::int DESC,
                         p.fields->>'occurred_on' DESC
                LIMIT :lim
            """),
            {"min_f": min_fatalities, "lim": limit},
        )
        candidates = [(row.event_id, row.fields) for row in rows]

    typer.echo(f"Loaded {len(candidates)} candidate events (limit={limit})")

    # ------------------------------------------------------------------ #
    # 2. Resolve or create the NewsStream source                          #
    # ------------------------------------------------------------------ #
    async with create_uow() as uow:
        source = await uow.sources.get_by_name(SOURCE_NAME)
        if source is None:
            source = Source(
                name=SOURCE_NAME,
                kind=SourceKind.EXTERNAL,
                reliability_tier=SOURCE_TIER,
            )
            if not dry_run:
                await uow.sources.add(source)
                await uow.commit()
                typer.echo(f"Registered source {SOURCE_NAME!r} (tier={SOURCE_TIER})")
            else:
                typer.echo(f"[dry-run] Would register source {SOURCE_NAME!r}")
    source_id = source.id

    # ------------------------------------------------------------------ #
    # 3. Build and submit claims                                          #
    # ------------------------------------------------------------------ #
    submitted = conflict_events = total_conflicts = failed = 0

    for event_id, fields in candidates:
        claims, conflict_map = build_news_claims(fields, rng)
        if not claims:
            continue

        ikey = _idempotency_key(source_id, event_id, claims)
        run_id = IngestSourceData.derive_ingestion_run_id(source_id, ikey)

        raw_payload = {
            "schema": "newsstream-pilot",
            "source_name": SOURCE_NAME,
            "original_event_id": str(event_id),
            "conflicts_introduced": conflict_map,
        }

        if dry_run:
            if conflict_map:
                typer.echo(
                    f"  [dry-run] event={str(event_id)[:8]}… "
                    f"conflicts={list(conflict_map.keys())}"
                )
            submitted += 1
            if conflict_map:
                conflict_events += 1
                total_conflicts += len(conflict_map)
            continue

        try:
            async with create_uow() as uow:
                await IngestSourceData(uow).execute_with_result(
                    source_id=source_id,
                    raw_payload=raw_payload,
                    ingestion_run_id=run_id,
                    claims_data=claims,
                    captured_at=datetime.now(UTC),
                    source_record_id=str(event_id),
                    event_id=event_id,
                )
            submitted += 1
            if conflict_map:
                conflict_events += 1
                total_conflicts += len(conflict_map)
        except Exception as exc:
            failed += 1
            typer.echo(f"  ! {event_id}: {type(exc).__name__}: {exc}", err=True)

        if submitted % 100 == 0:
            typer.echo(f"  ... {submitted} submitted ({conflict_events} with conflicts, {failed} failed)")

    typer.echo(
        f"\nDone. submitted={submitted} events_with_conflicts={conflict_events} "
        f"total_conflict_fields={total_conflicts} failed={failed}"
    )
    if total_conflicts:
        typer.echo(f"Avg conflicts per conflicting event: {total_conflicts/max(conflict_events,1):.1f}")


if __name__ == "__main__":
    app()
