from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.performance

from atlas.ingestion.generic_csv_adapter import (
    SourceMapping,
    load_csv_with_mapping,
    normalise_generic,
)
from atlas.ingestion.normalizer import build_canonical_fields
from atlas.ingestion.ntsb_adapter import load_from_csv


def _load_generator_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "generate_large_data_fixture.py"
    spec = importlib.util.spec_from_file_location("generate_large_data_fixture", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_large_data_fixture_generator_writes_valid_csvs(tmp_path):
    generator = _load_generator_module()
    manifest = generator.generate_fixture(
        tmp_path,
        profile_name="unit-test",
        ntsb_rows=25,
        asn_rows=5,
        dense_fraction=0.6,
        seed=7,
        start=generator.date(2020, 1, 1),
    )

    ntsb_path = tmp_path / manifest.ntsb_csv
    asn_path = tmp_path / manifest.asn_like_csv
    assert ntsb_path.exists()
    assert asn_path.exists()
    assert (tmp_path / "manifest.json").exists()

    with ntsb_path.open(newline="", encoding="utf-8") as handle:
        ntsb_rows = list(csv.DictReader(handle))
    with asn_path.open(newline="", encoding="utf-8") as handle:
        asn_rows = list(csv.DictReader(handle, delimiter=";"))

    assert len(ntsb_rows) == 25
    assert len(asn_rows) == 5
    assert "FatalCrewInjuries" in ntsb_rows[0]
    assert "registration" in asn_rows[0]


def test_generated_ntsb_fixture_normalizes(tmp_path):
    generator = _load_generator_module()
    manifest = generator.generate_fixture(
        tmp_path,
        profile_name="unit-test",
        ntsb_rows=10,
        asn_rows=0,
        dense_fraction=1.0,
        seed=11,
        start=generator.date(2021, 1, 1),
    )
    import asyncio

    raw_records = asyncio.run(load_from_csv(str(tmp_path / manifest.ntsb_csv)))

    canonical = [build_canonical_fields(raw) for raw in raw_records]
    assert len(canonical) == 10
    assert any(row.get("location_coordinates") for row in canonical)
    assert any("fatalities_crew" in row or "serious_injuries_crew" in row for row in canonical)


def test_generated_asn_like_fixture_validates_against_bundled_mapping(tmp_path):
    generator = _load_generator_module()
    manifest = generator.generate_fixture(
        tmp_path,
        profile_name="unit-test",
        ntsb_rows=50,
        asn_rows=8,
        dense_fraction=0.5,
        seed=13,
        start=generator.date(2022, 1, 1),
    )
    mapping = SourceMapping.from_file(
        Path(__file__).resolve().parents[2]
        / "src"
        / "atlas"
        / "ingestion"
        / "source_mappings"
        / "asn_mapping.json"
    )
    rows = load_csv_with_mapping(tmp_path / manifest.asn_like_csv, mapping)
    canonical = [normalise_generic(row["__canonical__"]) for row in rows]

    assert len(canonical) == 8
    assert all(row.get("aircraft_registration") for row in canonical)
    assert all(row.get("occurred_at") for row in canonical)
    assert any("fatalities_total" in row for row in canonical)
