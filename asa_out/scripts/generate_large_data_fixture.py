#!/usr/bin/env python3
"""Generate deterministic large-data fixtures for Aviation Safety Atlas.

The generator intentionally writes CSV fixtures instead of committing large
static files.  The generated NTSB CSV can be ingested with::

    atlas ingest csv .generated/perf/local-10k/ntsb_large.csv

The generated ASN-like CSV can be ingested with::

    atlas ingest generic-csv .generated/perf/local-10k/asn_like_large.csv --mapping asn

Profiles are available for quick smoke fixtures and larger local/nightly runs.
All data is synthetic; no generated row represents a real accident.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

NTSB_HEADERS = [
    "EventId",
    "EventDate",
    "EventTime",
    "Make",
    "Model",
    "Registration",
    "OperatorName",
    "Country",
    "State",
    "City",
    "LatDecimal",
    "LongDecimal",
    "HighestInjury",
    "TotalFatalInjuries",
    "FatalCrewInjuries",
    "FatalPassengerInjuries",
    "TotalSeriousInjuries",
    "SeriousCrewInjuries",
    "SeriousPassengerInjuries",
    "TotalMinorInjuries",
    "MinorCrewInjuries",
    "MinorPassengerInjuries",
    "UninjuredCrew",
    "UninjuredPassengers",
    "AboardPassengerCount",
    "AboardCrewCount",
    "AircraftDamage",
    "InvestigationType",
    "ReportNumber",
    "ProbableCause",
    "AmateurBuilt",
    "PhaseOfFlight",
    "PurposeOfFlight",
    "WeatherCondition",
]

ASN_HEADERS = [
    "date",
    "type",
    "registration",
    "operator",
    "fat.",
    "location",
    "country",
    "phase",
    "nature",
    "departure airport",
    "destination airport",
    "narrative",
    "acc_no",
]

PROFILES: dict[str, dict[str, int | float]] = {
    # Fast enough for local verification of generation + dry-run parsing.
    "smoke": {"ntsb_rows": 500, "asn_rows": 50, "dense_fraction": 0.65},
    # Intended for manual endpoint profiling on a developer machine.
    "local-10k": {"ntsb_rows": 10_000, "asn_rows": 1_000, "dense_fraction": 0.70},
    # Intended for nightly or staging-scale performance runs, not normal CI.
    "nightly-100k": {"ntsb_rows": 100_000, "asn_rows": 10_000, "dense_fraction": 0.75},
}

MAKES_MODELS = [
    ("Cessna", "172S"),
    ("Piper", "PA-28-181"),
    ("Beechcraft", "Bonanza A36"),
    ("Cirrus", "SR22"),
    ("Mooney", "M20J"),
    ("Robinson", "R44"),
    ("Air Tractor", "AT-802"),
    ("Diamond", "DA40"),
]

PHASES = ["TAKEOFF", "CLIMB", "CRUISE", "DESCENT", "APPROACH", "LANDING"]
PURPOSES = ["Personal", "Instructional", "Business", "Aerial Application", "Positioning"]
WEATHER = ["VMC", "IMC", "VMC", "VMC", "UNKNOWN"]
STATES_CITIES = [
    ("Florida", "Fort Lauderdale", 26.0742, -80.1506),
    ("Oregon", "Bend", 44.0582, -121.3153),
    ("Oklahoma", "Oklahoma City", 35.4634, -97.5151),
    ("California", "Fresno", 36.7378, -119.7871),
    ("Texas", "Austin", 30.2672, -97.7431),
    ("Arizona", "Mesa", 33.4152, -111.8315),
]


@dataclass(frozen=True)
class FixtureManifest:
    profile: str
    seed: int
    ntsb_rows: int
    asn_rows: int
    dense_fraction: float
    ntsb_csv: str
    asn_like_csv: str
    notes: list[str]


def _registration(index: int) -> str:
    return f"N{10000 + (index % 90000):05d}"


def _event_date(index: int, start: date) -> date:
    # Spread rows over roughly 12 years while keeping deterministic overlap keys.
    return start + timedelta(days=index % 4380)


def _event_time(index: int) -> str | None:
    # Keep some day-precision rows to exercise cursor/map behavior around null times.
    if index % 17 == 0:
        return None
    hour = (7 + index * 3) % 24
    minute = (index * 11) % 60
    return f"{hour:02d}{minute:02d}"


def _severity_counts(index: int) -> tuple[str, int, int, int, int, int, int, int, int, int]:
    """Return severity and total/split injury counts for one synthetic accident."""
    if index % 11 == 0:
        fatal_total = 1 + (index % 6)
        fatal_crew = 1 if fatal_total else 0
        fatal_passengers = max(fatal_total - fatal_crew, 0)
        return (
            "Fatal",
            fatal_total,
            fatal_crew,
            fatal_passengers,
            0,
            0,
            0,
            0,
            0,
            0,
        )
    if index % 5 == 0:
        serious_total = 1 + (index % 3)
        serious_crew = 1 if index % 2 == 0 else 0
        serious_passengers = serious_total - serious_crew
        return (
            "Serious",
            0,
            0,
            0,
            serious_total,
            serious_crew,
            serious_passengers,
            0,
            0,
            0,
        )
    if index % 3 == 0:
        minor_total = 1 + (index % 4)
        minor_crew = 1 if index % 2 == 1 else 0
        minor_passengers = minor_total - minor_crew
        return (
            "Minor",
            0,
            0,
            0,
            0,
            0,
            0,
            minor_total,
            minor_crew,
            minor_passengers,
        )
    return ("None", 0, 0, 0, 0, 0, 0, 0, 0, 0)


def _location(index: int, rng: random.Random, dense_fraction: float) -> tuple[str, str, float, float]:
    """Return state/city/lat/lon with many rows in a dense Florida viewport."""
    if rng.random() < dense_fraction:
        base_state, base_city, base_lat, base_lon = STATES_CITIES[0]
        lat = base_lat + rng.uniform(-0.65, 0.65)
        lon = base_lon + rng.uniform(-0.65, 0.65)
        return base_state, base_city, lat, lon
    state, city, base_lat, base_lon = STATES_CITIES[index % len(STATES_CITIES)]
    lat = base_lat + rng.uniform(-1.5, 1.5)
    lon = base_lon + rng.uniform(-1.5, 1.5)
    return state, city, lat, lon


def iter_ntsb_rows(count: int, *, seed: int, dense_fraction: float, start: date) -> Iterable[dict[str, str]]:
    rng = random.Random(seed)
    for index in range(count):
        event_date = _event_date(index, start)
        make, model = MAKES_MODELS[index % len(MAKES_MODELS)]
        state, city, lat, lon = _location(index, rng, dense_fraction)
        (
            severity,
            fatal_total,
            fatal_crew,
            fatal_passengers,
            serious_total,
            serious_crew,
            serious_passengers,
            minor_total,
            minor_crew,
            minor_passengers,
        ) = _severity_counts(index)
        crew_aboard = 1 + (index % 2)
        pax_aboard = max(fatal_passengers + serious_passengers + minor_passengers, index % 5)
        uninjured_crew = max(crew_aboard - fatal_crew - serious_crew - minor_crew, 0)
        uninjured_passengers = max(
            pax_aboard - fatal_passengers - serious_passengers - minor_passengers,
            0,
        )
        row = {
            "EventId": f"PERF{event_date.year}{index:07d}",
            "EventDate": event_date.isoformat(),
            "EventTime": _event_time(index) or "",
            "Make": make,
            "Model": model,
            "Registration": _registration(index),
            "OperatorName": f"Performance Fixture Operator {index % 37:02d}",
            "Country": "United States",
            "State": state,
            "City": city,
            "LatDecimal": f"{lat:.6f}",
            "LongDecimal": f"{lon:.6f}",
            "HighestInjury": severity,
            "TotalFatalInjuries": str(fatal_total),
            "FatalCrewInjuries": str(fatal_crew),
            "FatalPassengerInjuries": str(fatal_passengers),
            "TotalSeriousInjuries": str(serious_total),
            "SeriousCrewInjuries": str(serious_crew),
            "SeriousPassengerInjuries": str(serious_passengers),
            "TotalMinorInjuries": str(minor_total),
            "MinorCrewInjuries": str(minor_crew),
            "MinorPassengerInjuries": str(minor_passengers),
            "UninjuredCrew": str(uninjured_crew),
            "UninjuredPassengers": str(uninjured_passengers),
            "AboardPassengerCount": str(pax_aboard),
            "AboardCrewCount": str(crew_aboard),
            "AircraftDamage": "DEST" if severity == "Fatal" else "SUBS",
            "InvestigationType": "FINAL" if index % 13 == 0 else "PROBABLE CAUSE",
            "ReportNumber": f"PERF-{index:07d}",
            "ProbableCause": (
                "Synthetic performance fixture row; not a real accident. "
                f"Deterministic index {index}."
            ),
            "AmateurBuilt": "No" if index % 19 else "Yes",
            "PhaseOfFlight": PHASES[index % len(PHASES)],
            "PurposeOfFlight": PURPOSES[index % len(PURPOSES)],
            "WeatherCondition": WEATHER[index % len(WEATHER)],
        }
        yield row


def iter_asn_like_rows(
    count: int,
    *,
    seed: int,
    ntsb_count: int,
    start: date,
) -> Iterable[dict[str, str]]:
    """Generate ASN-like rows, mostly overlapping deterministic NTSB keys.

    Every fourth ASN row intentionally disagrees on fatality count to exercise
    the conflict path when used with the NTSB fixture.
    """
    rng = random.Random(seed + 1000)
    for index in range(count):
        ntsb_index = (index * 7) % max(ntsb_count, 1)
        event_date = _event_date(ntsb_index, start)
        make, model = MAKES_MODELS[ntsb_index % len(MAKES_MODELS)]
        state, city, _, _ = STATES_CITIES[ntsb_index % len(STATES_CITIES)]
        severity, fatal_total, *_ = _severity_counts(ntsb_index)
        if severity != "Fatal":
            asn_fatal = 0
        else:
            asn_fatal = fatal_total + (1 if index % 4 == 0 else 0)
        row = {
            "date": event_date.isoformat(),
            "type": f"{make} {model}",
            "registration": _registration(ntsb_index),
            "operator": f"Performance Fixture Operator {ntsb_index % 37:02d}",
            "fat.": str(asn_fatal),
            "location": f"{city}, {state}, United States",
            "country": "United States",
            "phase": PHASES[ntsb_index % len(PHASES)].lower(),
            "nature": "Synthetic performance fixture overlap",
            "departure airport": f"Fixture Departure {rng.randrange(100):02d}",
            "destination airport": f"Fixture Destination {rng.randrange(100):02d}",
            "narrative": (
                "ASN-like synthetic row for large-data performance and conflict testing; "
                "not real ASN data."
            ),
            "acc_no": f"ASN-PERF-{index:07d}",
        }
        yield row


def write_csv(path: Path, headers: list[str], rows: Iterable[dict[str, str]], *, delimiter: str = ",") -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, delimiter=delimiter)
        writer.writeheader()
        for row in rows:
            writer.writerow({header: row.get(header, "") for header in headers})
            count += 1
    return count


def load_profile(path: Path) -> dict[str, int | float]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    return {
        "ntsb_rows": int(data["ntsb_rows"]),
        "asn_rows": int(data["asn_rows"]),
        "dense_fraction": float(data.get("dense_fraction", 0.70)),
    }


def resolve_profile(name_or_path: str) -> tuple[str, dict[str, int | float]]:
    maybe_path = Path(name_or_path)
    if maybe_path.exists():
        return maybe_path.stem, load_profile(maybe_path)
    if name_or_path not in PROFILES:
        valid = ", ".join(sorted(PROFILES))
        raise SystemExit(f"Unknown profile {name_or_path!r}. Valid profiles: {valid}, or pass JSON path.")
    return name_or_path, dict(PROFILES[name_or_path])


def write_readme(path: Path, manifest: FixtureManifest) -> None:
    path.write_text(
        "\n".join(
            [
                "# Generated Aviation Safety Atlas performance fixture",
                "",
                "This directory is generated and should not be committed.",
                "All records are synthetic and deterministic.",
                "",
                "## Files",
                "",
                f"- `{manifest.ntsb_csv}` — NTSB-like bulk CSV fixture",
                f"- `{manifest.asn_like_csv}` — ASN-like semicolon CSV fixture",
                "- `manifest.json` — generation metadata",
                "",
                "## Load commands",
                "",
                "```bash",
                f"atlas ingest csv {manifest.ntsb_csv}",
                f"atlas ingest generic-csv {manifest.asn_like_csv} --mapping asn",
                "atlas reproject",
                "```",
                "",
                "## Notes",
                "",
                *[f"- {note}" for note in manifest.notes],
                "",
            ]
        ),
        encoding="utf-8",
    )


def generate_fixture(
    output_dir: Path,
    *,
    profile_name: str,
    ntsb_rows: int,
    asn_rows: int,
    dense_fraction: float,
    seed: int,
    start: date,
) -> FixtureManifest:
    output_dir.mkdir(parents=True, exist_ok=True)
    ntsb_path = output_dir / "ntsb_large.csv"
    asn_path = output_dir / "asn_like_large.csv"
    actual_ntsb_rows = write_csv(
        ntsb_path,
        NTSB_HEADERS,
        iter_ntsb_rows(ntsb_rows, seed=seed, dense_fraction=dense_fraction, start=start),
    )
    actual_asn_rows = write_csv(
        asn_path,
        ASN_HEADERS,
        iter_asn_like_rows(asn_rows, seed=seed, ntsb_count=ntsb_rows, start=start),
        delimiter=";",
    )
    manifest = FixtureManifest(
        profile=profile_name,
        seed=seed,
        ntsb_rows=actual_ntsb_rows,
        asn_rows=actual_asn_rows,
        dense_fraction=dense_fraction,
        ntsb_csv=ntsb_path.name,
        asn_like_csv=asn_path.name,
        notes=[
            "Rows are synthetic and deterministic; do not treat them as source data.",
            "Dense-region rows are concentrated near Fort Lauderdale for map stress tests.",
            "ASN-like rows overlap NTSB registration/date keys and intentionally include some fatality disagreements.",
            "Large generated files belong under .generated/ or perf-data/ and should not be committed.",
        ],
    )
    (output_dir / "manifest.json").write_text(
        json.dumps(asdict(manifest), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_readme(output_dir / "README.md", manifest)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="smoke", help="Profile name or JSON profile path")
    parser.add_argument("--output-dir", type=Path, default=Path(".generated/perf-fixture"))
    parser.add_argument("--ntsb-rows", type=int, default=None, help="Override NTSB row count")
    parser.add_argument("--asn-rows", type=int, default=None, help="Override ASN-like row count")
    parser.add_argument("--dense-fraction", type=float, default=None, help="Override dense viewport fraction")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(2010, 1, 1))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    profile_name, profile = resolve_profile(args.profile)
    ntsb_rows = args.ntsb_rows if args.ntsb_rows is not None else int(profile["ntsb_rows"])
    asn_rows = args.asn_rows if args.asn_rows is not None else int(profile["asn_rows"])
    dense_fraction = (
        args.dense_fraction if args.dense_fraction is not None else float(profile["dense_fraction"])
    )
    if ntsb_rows < 0 or asn_rows < 0:
        raise SystemExit("Row counts must be non-negative.")
    if not 0 <= dense_fraction <= 1:
        raise SystemExit("--dense-fraction must be between 0 and 1.")
    manifest = generate_fixture(
        args.output_dir,
        profile_name=profile_name,
        ntsb_rows=ntsb_rows,
        asn_rows=asn_rows,
        dense_fraction=dense_fraction,
        seed=args.seed,
        start=args.start_date,
    )
    print(json.dumps(asdict(manifest), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
