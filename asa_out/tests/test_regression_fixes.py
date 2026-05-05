"""Regression checks for review fixes that previously broke startup/loading."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_alembic_down_revision_points_to_existing_revision():
    revisions: set[str] = set()
    down_revisions: dict[str, str | None] = {}
    for path in (ROOT / "migrations" / "versions").glob("*.py"):
        tree = ast.parse(path.read_text())
        values: dict[str, str | None] = {}
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}:
                        values[target.id] = ast.literal_eval(node.value)
        revision = values.get("revision")
        assert revision, f"{path.name} has no revision"
        revisions.add(revision)
        down_revisions[revision] = values.get("down_revision")

    missing = {
        rev: down for rev, down in down_revisions.items()
        if down is not None and down not in revisions
    }
    assert not missing


def test_claim_source_document_has_matching_orm_and_migration():
    orm = (ROOT / "src" / "atlas" / "models" / "orm.py").read_text()
    migration = (ROOT / "migrations" / "versions" / "0017_duplicate_merge_operations.py").read_text()
    assert "class ClaimSourceDocument" in orm
    assert '__tablename__ = "claim_source_documents"' in orm
    assert "claim_source_documents" in migration
    assert "uq_claim_source_document_pair" in orm
    assert "uq_claim_source_document_pair" in migration


def test_frontend_search_filter_key_includes_all_api_filters():
    hook = (ROOT / "web" / "hooks" / "useAccidents.ts").read_text()
    for name in [
        "registration",
        "aircraft_type",
        "operator",
        "source_id",
        "disputed_only",
        "final_report_only",
    ]:
        assert f"filters.{name}" in hook


def test_no_generated_tsbuildinfo_checked_in():
    assert not list((ROOT / "web").glob("*.tsbuildinfo"))
