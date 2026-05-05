"""Retention/archive automation scaffolding.

The archive job is intentionally conservative: it exports rows older than a
cutoff into JSONL files and records an ArchiveManifest row. Destructive delete
is opt-in and only allowed with execute=True. The first implementation focuses
on reversibility and proof over clever storage backends; operators can point the
output directory at a mounted object-storage sync path.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from atlas.models.orm import (
    ArchiveManifest,
    ClaimHistory,
    EventRevision,
    IngestionRun,
    RawSnapshot,
)


@dataclass
class ArchiveTableResult:
    table: str
    exported: int = 0
    deleted: int = 0
    file: str | None = None


@dataclass
class ArchiveRunResult:
    manifest_id: str
    cutoff_at: datetime
    output_dir: str
    execute: bool
    tables: list[ArchiveTableResult] = field(default_factory=list)

    def to_manifest(self) -> dict[str, Any]:
        return {
            "manifest_id": self.manifest_id,
            "cutoff_at": self.cutoff_at.isoformat(),
            "output_dir": self.output_dir,
            "execute": self.execute,
            "tables": [t.__dict__ for t in self.tables],
            "created_at": datetime.now(tz=UTC).isoformat(),
            "format": "jsonl-v1",
        }


_TABLES = {
    "ingestion_runs": (IngestionRun, IngestionRun.completed_at),
    "event_revisions": (EventRevision, EventRevision.occurred_at),
    "claim_history": (ClaimHistory, ClaimHistory.changed_at),
    "raw_snapshots": (RawSnapshot, RawSnapshot.ingested_at),
}


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_signature(manifest: dict[str, Any], secret: str | None = None) -> str | None:
    secret = secret or os.environ.get("ARCHIVE_MANIFEST_SECRET")
    if not secret:
        return None
    payload = json.dumps(
        {k: v for k, v in manifest.items() if k != "signature"},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


async def archive_old_rows(
    session: AsyncSession,
    *,
    output_dir: Path | str,
    cutoff_days: int = 730,
    execute: bool = False,
    created_by: str | None = None,
    tables: list[str] | None = None,
    batch_size: int = 1000,
) -> ArchiveRunResult:
    """Export rows older than cutoff and optionally delete them.

    Deletion is intentionally table-level and conservative. Raw snapshots and
    audit tables are never removed unless execute=True; dry-run exports the same
    rows and records a manifest with status='dry_run'.
    """
    cutoff_at = datetime.now(tz=UTC) - timedelta(days=cutoff_days)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    manifest_id = str(uuid.uuid4())
    selected_tables = tables or list(_TABLES)
    result = ArchiveRunResult(
        manifest_id=manifest_id,
        cutoff_at=cutoff_at,
        output_dir=str(output_path),
        execute=execute,
    )

    for table_name in selected_tables:
        if table_name not in _TABLES:
            raise ValueError(f"Unsupported archive table: {table_name}")
        model, time_col = _TABLES[table_name]
        file_path = output_path / f"{table_name}-{manifest_id}.jsonl"
        exported = 0

        # Repeated keyset-ish batches by timestamp/id are overkill here; this
        # is a retention job, not request-path code. Batch by LIMIT until no rows.
        with file_path.open("w", encoding="utf-8") as f:
            while True:
                rows = (await session.execute(
                    select(model)
                    .where(time_col.isnot(None), time_col < cutoff_at)
                    .order_by(time_col.asc(), model.id.asc())
                    .limit(batch_size)
                )).scalars().all()
                if not rows:
                    break
                for row in rows:
                    f.write(json.dumps(_row_to_dict(row), default=_json_default, sort_keys=True) + "\n")
                exported += len(rows)
                if not execute:
                    # Dry-run exports only first batch to keep accidental dry-runs
                    # from dumping millions of rows.
                    break
                ids = [row.id for row in rows]
                await session.execute(delete(model).where(model.id.in_(ids)))
                await session.flush()

        result.tables.append(ArchiveTableResult(
            table=table_name,
            exported=exported,
            deleted=exported if execute else 0,
            file=str(file_path),
        ))

    manifest = result.to_manifest()
    file_hashes: dict[str, str] = {}
    for table in result.tables:
        if table.file:
            path = Path(table.file)
            if path.exists():
                file_hashes[path.name] = _sha256_file(path)
    manifest["file_hashes"] = file_hashes
    signature = _manifest_signature(manifest)
    if signature:
        manifest["signature"] = {"algorithm": "hmac-sha256", "value": signature}
    manifest_file = output_path / f"manifest-{manifest_id}.json"
    manifest_file.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    session.add(ArchiveManifest(
        id=manifest_id,
        archive_type="retention",
        status="completed" if execute else "dry_run",
        cutoff_at=cutoff_at,
        output_uri=str(manifest_file),
        manifest=manifest,
        completed_at=datetime.now(tz=UTC),
        created_by=created_by,
    ))
    await session.commit()
    return result


def _parse_value_for_column(column: Any, value: Any) -> Any:
    if value is None:
        return None
    typename = column.type.__class__.__name__.lower()
    if "datetime" in typename and isinstance(value, str):
        return datetime.fromisoformat(value)
    if typename == "date" and isinstance(value, str):
        return date.fromisoformat(value)
    return value


def _dict_to_model(model: Any, data: dict[str, Any]) -> Any:
    values: dict[str, Any] = {}
    for column in model.__table__.columns:
        if column.name in data:
            values[column.name] = _parse_value_for_column(column, data[column.name])
    return model(**values)


async def restore_archive(
    session: AsyncSession,
    *,
    manifest_path: Path | str,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Restore rows from a JSONL archive manifest.

    This is intentionally conservative and idempotent: rows are merged by their
    primary key. Dry-run mode counts rows only. Operators should restore into a
    staging database first and compare counts before touching production.
    """
    manifest_file = Path(manifest_path)
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    base_dir = manifest_file.parent
    restored: dict[str, int] = {}

    for table in manifest.get("tables", []):
        table_name = table["table"]
        if table_name not in _TABLES:
            continue
        model, _time_col = _TABLES[table_name]
        data_file = Path(table.get("file") or "")
        if not data_file.is_absolute():
            data_file = base_dir / data_file
        count = 0
        if data_file.exists():
            with data_file.open(encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    row_data = json.loads(line)
                    count += 1
                    if not dry_run:
                        await session.merge(_dict_to_model(model, row_data))
        restored[table_name] = count

    if not dry_run:
        await session.commit()
    return {"manifest_id": manifest.get("manifest_id"), "dry_run": dry_run, "restored": restored}




def verify_archive_manifest(manifest_path: Path | str, *, secret: str | None = None) -> dict[str, Any]:
    """Verify file checksums and optional HMAC signature for an archive manifest."""
    manifest_file = Path(manifest_path)
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    base_dir = manifest_file.parent
    errors: list[str] = []

    expected_hashes = manifest.get("file_hashes") or {}
    verified_files: dict[str, str] = {}
    for filename, expected in expected_hashes.items():
        path = base_dir / filename
        if not path.exists():
            errors.append(f"missing archived file: {filename}")
            continue
        actual = _sha256_file(path)
        verified_files[filename] = actual
        if actual != expected:
            errors.append(f"checksum mismatch for {filename}: expected {expected}, got {actual}")

    signature = manifest.get("signature")
    signature_ok: bool | None = None
    if signature:
        actual_sig = _manifest_signature(manifest, secret=secret)
        signature_ok = hmac.compare_digest(actual_sig or "", signature.get("value", ""))
        if not signature_ok:
            errors.append("manifest signature mismatch")

    return {
        "manifest_id": manifest.get("manifest_id"),
        "ok": not errors,
        "errors": errors,
        "verified_files": verified_files,
        "signature_present": bool(signature),
        "signature_ok": signature_ok,
    }


async def archive_plan(
    session: AsyncSession,
    *,
    cutoff_days: int = 730,
    tables: list[str] | None = None,
) -> dict[str, Any]:
    cutoff_at = datetime.now(tz=UTC) - timedelta(days=cutoff_days)
    selected_tables = tables or list(_TABLES)
    counts: dict[str, int] = {}
    for table_name in selected_tables:
        if table_name not in _TABLES:
            raise ValueError(f"Unsupported archive table: {table_name}")
        model, time_col = _TABLES[table_name]
        n = (await session.execute(
            select(func.count()).select_from(model).where(time_col.isnot(None), time_col < cutoff_at)
        )).scalar_one()
        counts[table_name] = int(n or 0)
    return {"cutoff_at": cutoff_at.isoformat(), "counts": counts}
