"""atlas CLI — installed as 'atlas' command via pyproject.toml [project.scripts]."""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from atlas.config import get_settings

app = typer.Typer(name="atlas", help="Aviation Safety Atlas operations")
ingest_app = typer.Typer(help="Ingestion commands")
db_app = typer.Typer(help="Database commands")
keys_app = typer.Typer(help="API key management")
archive_app = typer.Typer(help="Retention/archive commands")
app.add_typer(ingest_app, name="ingest")
app.add_typer(db_app, name="db")
app.add_typer(keys_app, name="keys")
app.add_typer(archive_app, name="archive")

console = Console()
settings = get_settings()


@app.command()
def serve(
    host: str = typer.Option(settings.api_host),
    port: int = typer.Option(settings.api_port),
    reload: bool = typer.Option(False),
) -> None:
    """Start the FastAPI server."""
    import uvicorn
    console.print(f"[green]Starting Atlas API → http://{host}:{port}[/green]")
    uvicorn.run("atlas.api.app:app", host=host, port=port, reload=reload,
                log_level=settings.log_level.lower())


@ingest_app.command("ntsb")
def ingest_ntsb(
    start: str = typer.Option(..., help="YYYY-MM-DD"),
    end: str = typer.Option(..., help="YYYY-MM-DD"),
) -> None:
    """Run NTSB API ingestion for a date range."""
    from atlas.ingestion.pipeline import IngestionPipeline
    result = asyncio.run(IngestionPipeline().run_ntsb_api(
        date.fromisoformat(start), date.fromisoformat(end)
    ))
    t = Table(title="Ingestion result")
    t.add_column("Metric"); t.add_column("Value")
    t.add_row("Fetched", str(result.records_fetched))
    t.add_row("New snapshots", str(result.snapshots_new))
    t.add_row("Skipped (dedup)", str(result.snapshots_skipped))
    t.add_row("Events created", str(result.events_created))
    t.add_row("Claims written", str(result.claims_written))
    t.add_row("Errors", str(len(result.errors)))
    console.print(t)
    for e in result.errors[:10]:
        console.print(f"[red]{e}[/red]")


@ingest_app.command("csv")
def ingest_csv(filepath: Path = typer.Argument(...)) -> None:
    """Ingest from NTSB bulk CSV export."""
    from atlas.ingestion.pipeline import IngestionPipeline
    result = asyncio.run(IngestionPipeline().run_ntsb_csv(str(filepath)))
    console.print(
        f"[green]✓ {result.events_created} events, {result.claims_written} claims[/green]"
        if result.success else f"[red]✗ {len(result.errors)} errors[/red]"
    )


@ingest_app.command("asn-csv")
def ingest_asn_csv(
    filepath: Path = typer.Argument(..., help="Path to licensed ASN CSV export"),
    dry_run: bool = typer.Option(False, help="Parse and normalise only — no DB writes"),
) -> None:
    """Ingest a licensed Aviation Safety Network CSV export using the bundled ASN mapping.

    This command intentionally requires a local CSV file. Atlas does not scrape ASN;
    operators must provide data obtained under a license/permission that allows use.
    """
    from atlas.ingestion.generic_csv_adapter import load_bundled_mapping
    from atlas.ingestion.pipeline import IngestionPipeline

    src_mapping = load_bundled_mapping("asn")
    console.print(f"[blue]{'DRY RUN: ' if dry_run else ''}Ingesting ASN CSV {filepath.name}[/blue]")
    result = asyncio.run(IngestionPipeline().run_generic_csv(
        str(filepath), src_mapping, dry_run=dry_run,
    ))
    t = Table(title=f"{'Dry run — ' if dry_run else ''}ASN ingestion result")
    t.add_column("Metric"); t.add_column("Value")
    t.add_row("Records read", str(result.records_fetched))
    t.add_row("New snapshots", str(result.snapshots_new))
    t.add_row("Skipped (dedup)", str(result.snapshots_skipped))
    t.add_row("Events created", str(result.events_created))
    t.add_row("Events updated", str(result.events_updated))
    t.add_row("Claims written", str(result.claims_written))
    t.add_row("Errors", str(len(result.errors)))
    console.print(t)
    for e in result.errors[:10]:
        console.print(f"[red]{e}[/red]")


@ingest_app.command("generic-csv")
def ingest_generic_csv(
    filepath: Path = typer.Argument(..., help="Path to the source CSV file"),
    mapping: str = typer.Option(
        ...,
        help="Path to a mapping JSON file, or a bundled mapping name (asn, icao)",
    ),
    dry_run: bool = typer.Option(False, help="Parse and normalise only — no DB writes"),
) -> None:
    """
    Ingest from any tabular CSV source using a column mapping.

    Bundled mappings: asn, icao
    Custom mapping:   pass a path to your own JSON mapping file.

    Example:
        atlas ingest generic-csv asn_export.csv --mapping asn
        atlas ingest generic-csv custom.csv --mapping ./my_mapping.json --dry-run
    """
    from atlas.ingestion.generic_csv_adapter import SourceMapping, load_bundled_mapping
    from atlas.ingestion.pipeline import IngestionPipeline

    # Resolve mapping: bundled name or file path
    mapping_path = Path(mapping)
    if mapping_path.exists():
        src_mapping = SourceMapping.from_file(mapping_path)
    else:
        try:
            src_mapping = load_bundled_mapping(mapping)
        except FileNotFoundError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1) from e

    console.print(
        f"[blue]{'DRY RUN: ' if dry_run else ''}Ingesting {filepath.name} "
        f"via source {src_mapping.source_id}[/blue]"
    )
    result = asyncio.run(IngestionPipeline().run_generic_csv(
        str(filepath), src_mapping, dry_run=dry_run,
    ))

    t = Table(title=f"{'Dry run — ' if dry_run else ''}Ingestion result")
    t.add_column("Metric"); t.add_column("Value")
    t.add_row("Source",         src_mapping.source_id)
    t.add_row("Records read",   str(result.records_fetched))
    t.add_row("New snapshots",  str(result.snapshots_new))
    t.add_row("Skipped (dedup)",str(result.snapshots_skipped))
    t.add_row("Events created", str(result.events_created))
    t.add_row("Claims written", str(result.claims_written))
    t.add_row("Errors",         str(len(result.errors)))
    console.print(t)
    for e in result.errors[:10]:
        console.print(f"[red]{e}[/red]")


@app.command()
def reproject(event_id: str | None = typer.Option(None)) -> None:
    """Rebuild accident_records from claim store."""
    from atlas.claims.projection import ProjectionService
    from atlas.db.engine import direct_session

    async def _run() -> None:
        async with direct_session() as session:
            svc = ProjectionService(session)
            if event_id:
                await svc.rebuild_event(event_id)
                console.print(f"[green]✓ Rebuilt {event_id}[/green]")
            else:
                rebuilt, failed = await svc.rebuild_all()
                if failed:
                    console.print(
                        f"[yellow]⚠ Rebuilt {rebuilt} projections; {failed} failed "
                        f"(see logs above for details)[/yellow]"
                    )
                    raise SystemExit(1)
                console.print(f"[green]✓ Rebuilt {rebuilt} projections[/green]")

    asyncio.run(_run())


@app.command("check-links")
def check_links(
    limit: int = typer.Option(100),
    allow_domains: str = typer.Option(
        "",
        help=(
            "Comma-separated list of allowed domains. When provided, only URLs "
            "from these domains are checked; others are skipped. "
            "Example: ntsb.gov,aviation-safety.net,icao.int"
        ),
    ),
) -> None:
    """Verify source document URLs. Uses HEAD with GET fallback for servers that block HEAD."""
    from urllib.parse import urlparse

    import httpx
    from sqlalchemy import select, update

    from atlas.db.engine import direct_session
    from atlas.models.orm import SourceDocument

    # ── SSRF allowlist ──────────────────────────────────────────────────────
    # Only public, known-safe domains may be fetched.  An empty allowlist
    # means "check all domains" (safe for local dev; not for production).
    # Never fetch private IP ranges, localhost, or internal hostnames.
    _PRIVATE_PREFIXES = (
        "localhost", "127.", "10.", "172.16.", "192.168.", "0.",
        "169.254.",  # link-local
        "::1", "fc", "fd",  # IPv6 loopback / ULA
    )

    allowed_domains: set[str] = set()
    if allow_domains.strip():
        allowed_domains = {d.strip().lower() for d in allow_domains.split(",") if d.strip()}

    def is_safe_url(url: str) -> tuple[bool, str]:
        """Return (safe, reason). Rejects private IPs and (optionally) non-allowed domains."""
        try:
            parsed = urlparse(url)
        except Exception:
            return False, "unparseable URL"
        if parsed.scheme not in ("http", "https"):
            return False, f"disallowed scheme {parsed.scheme!r}"
        host = (parsed.hostname or "").lower()
        for prefix in _PRIVATE_PREFIXES:
            if host == prefix or host.startswith(prefix):
                return False, f"private/loopback address: {host}"
        if allowed_domains:
            # Accept if host matches or is a subdomain of an allowed domain
            if not any(host == d or host.endswith(f".{d}") for d in allowed_domains):
                return False, f"domain {host!r} not in allowlist"
        return True, ""

    async def _run() -> None:
        async with direct_session() as session:
            rows = (await session.execute(
                select(SourceDocument.id, SourceDocument.url)
                .order_by(SourceDocument.last_checked_at.asc().nullsfirst())
                .limit(limit)
            )).all()

        results: list[dict] = []
        ok = broken = skipped = 0
        now = datetime.now(tz=UTC)
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            for doc_id, url in rows:
                safe, reason = is_safe_url(url)
                if not safe:
                    console.print(f"[dim]SKIPPED (SSRF guard — {reason}): {url}[/dim]")
                    skipped += 1
                    continue

                status_code: int | None = None
                failure_reason: str | None = None
                check_method = "HEAD"
                try:
                    r = await client.head(url)
                    status_code = r.status_code
                    if r.status_code in (403, 405, 501):
                        check_method = "GET"
                        try:
                            rg = await client.get(url, headers={"Range": "bytes=0-0"})
                            status_code = rg.status_code
                        except Exception as e:
                            failure_reason = f"GET fallback failed: {e}"
                    available = status_code < 400
                except Exception as e:
                    available = False
                    failure_reason = str(e)

                results.append({
                    "id": doc_id, "available": available,
                    "status_code": status_code, "failure_reason": failure_reason,
                    "check_method": check_method,
                })
                if available:
                    ok += 1
                else:
                    broken += 1
                    detail = f" (HTTP {status_code})" if status_code else f" ({failure_reason})"
                    console.print(f"[yellow]BROKEN: {url}{detail}[/yellow]")

        async with direct_session() as session:
            for res in results:
                await session.execute(
                    update(SourceDocument)
                    .where(SourceDocument.id == res["id"])
                    .values(
                        is_available=res["available"],
                        url_verified=res["available"],
                        last_checked_at=now,
                        last_http_status=res["status_code"],
                        last_check_error=res["failure_reason"],
                        last_check_method=res["check_method"],
                    )
                )

        console.print(
            f"[green]{ok} ok[/green] [red]{broken} broken[/red] "
            f"[dim]{skipped} skipped (SSRF guard)[/dim]"
        )

    asyncio.run(_run())



@db_app.command("migrate")
def db_migrate() -> None:
    """Run Alembic migrations."""
    import subprocess
    r = subprocess.run(["alembic", "upgrade", "head"], capture_output=True, text=True)
    console.print(r.stdout)
    if r.returncode:
        console.print(f"[red]{r.stderr}[/red]")
        raise typer.Exit(r.returncode)


@db_app.command("seed")
def db_seed() -> None:
    """Seed the source registry."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from atlas.db.engine import direct_session
    from atlas.models.orm import Source

    SOURCES = [
        dict(id="src-ntsb-001", short_name="NTSB",
             display_name="National Transportation Safety Board",
             tier=1, license_type="public_domain",
             base_url="https://www.ntsb.gov",
             description="Primary US authority. 1962-present. Public domain.",
             ingestion_enabled=True),
        dict(id="src-asn-001", short_name="ASN",
             display_name="Aviation Safety Network",
             tier=2, license_type="licensed",
             base_url="https://aviation-safety.net",
             description="Global records since 1919. Contact ASN before ingesting.",
             ingestion_enabled=False),
        dict(id="src-icao-001", short_name="ICAO",
             display_name="ICAO e-Library",
             tier=3, license_type="public_reports",
             base_url="https://www.icao.int",
             description="Final reports as public PDFs. Link only — no bulk copy.",
             ingestion_enabled=False),
        dict(id="src-baaa-001", short_name="BAAA",
             display_name="Bureau of Aircraft Accidents Archives",
             tier=4, license_type="commercial_license",
             base_url="https://www.baaa-acro.com",
             description="Commercial license required. Do not use without agreement.",
             ingestion_enabled=False),
    ]

    async def _run() -> None:
        async with direct_session() as session:
            for src in SOURCES:
                stmt = pg_insert(Source.__table__).values(**src).on_conflict_do_nothing(
                    index_elements=["id"]
                )
                await session.execute(stmt)
        console.print(f"[green]✓ Seeded {len(SOURCES)} sources[/green]")

    asyncio.run(_run())


@archive_app.command("plan")
def archive_plan_cmd(
    cutoff_days: int = typer.Option(730, help="Archive rows older than this many days"),
    tables: str = typer.Option("", help="Comma-separated subset of archiveable tables"),
) -> None:
    """Show how many rows would be archived without writing files or deleting data."""
    from atlas.db.engine import direct_session
    from atlas.retention.archive import archive_plan

    table_list = [t.strip() for t in tables.split(",") if t.strip()] or None

    async def _run() -> dict:
        async with direct_session() as session:
            return await archive_plan(session, cutoff_days=cutoff_days, tables=table_list)

    result = asyncio.run(_run())
    t = Table(title=f"Archive plan (cutoff {result['cutoff_at']})")
    t.add_column("Table"); t.add_column("Rows")
    for table_name, count in result["counts"].items():
        t.add_row(table_name, str(count))
    console.print(t)


@archive_app.command("run")
def archive_run_cmd(
    output_dir: Path = typer.Option(Path(".generated/archive"), help="Directory for JSONL archive files"),
    cutoff_days: int = typer.Option(730, help="Archive rows older than this many days"),
    execute: bool = typer.Option(False, help="Actually delete exported rows after writing archive files"),
    tables: str = typer.Option("", help="Comma-separated subset of archiveable tables"),
    created_by: str = typer.Option("cli", help="Operator identity for the archive manifest"),
) -> None:
    """Export retention-eligible rows to JSONL and optionally delete them."""
    from atlas.db.engine import direct_session
    from atlas.retention.archive import archive_old_rows

    table_list = [t.strip() for t in tables.split(",") if t.strip()] or None

    async def _run():
        async with direct_session() as session:
            return await archive_old_rows(
                session,
                output_dir=output_dir,
                cutoff_days=cutoff_days,
                execute=execute,
                created_by=created_by,
                tables=table_list,
            )

    result = asyncio.run(_run())
    t = Table(title=f"Archive {'EXECUTE' if execute else 'DRY RUN'}")
    t.add_column("Table"); t.add_column("Exported"); t.add_column("Deleted"); t.add_column("File")
    for row in result.tables:
        t.add_row(row.table, str(row.exported), str(row.deleted), row.file or "")
    console.print(t)
    console.print(f"[green]Manifest ID:[/green] {result.manifest_id}")


@archive_app.command("verify")
def archive_verify_cmd(
    manifest_path: Path = typer.Argument(..., help="Path to manifest-<id>.json"),
) -> None:
    """Verify archive checksums and optional manifest signature."""
    from atlas.retention.archive import verify_archive_manifest

    result = verify_archive_manifest(manifest_path)
    if result["ok"]:
        console.print(f"[green]✓ Archive manifest verified:[/green] {result.get('manifest_id')}")
    else:
        console.print(f"[red]✗ Archive manifest failed verification:[/red] {result.get('manifest_id')}")
        for err in result["errors"]:
            console.print(f"[red]- {err}[/red]")
        raise typer.Exit(1)


@archive_app.command("restore")
def archive_restore_cmd(
    manifest_path: Path = typer.Argument(..., help="Path to manifest-<id>.json"),
    execute: bool = typer.Option(False, help="Actually merge rows into the database; default is dry-run count only"),
) -> None:
    """Restore archived JSONL rows from a manifest into the current database."""
    from atlas.db.engine import direct_session
    from atlas.retention.archive import restore_archive

    async def _run() -> dict:
        async with direct_session() as session:
            return await restore_archive(session, manifest_path=manifest_path, dry_run=not execute)

    result = asyncio.run(_run())
    t = Table(title=f"Archive restore {'EXECUTE' if execute else 'DRY RUN'}")
    t.add_column("Table"); t.add_column("Rows")
    for table_name, count in result["restored"].items():
        t.add_row(table_name, str(count))
    console.print(t)
    console.print(f"[green]Manifest ID:[/green] {result.get('manifest_id')}")


@keys_app.command("create")
def keys_create(
    operator_id: str = typer.Argument(..., help="Username or email for the operator"),
    role: str = typer.Option("reviewer", help="Role: reviewer or admin"),
    description: str = typer.Option("", help="Optional note about this key"),
) -> None:
    """
    Generate a new reviewer API key and register it in the database.

    The raw key is shown ONCE and never stored — copy it immediately.
    The database stores only the SHA-256 hash.

    Example:
        atlas keys create reviewer@airline.com --role reviewer
        atlas keys create admin@ops.team --role admin --description "CI deployment key"
    """
    import hashlib
    import secrets

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from atlas.db.engine import direct_session
    from atlas.models.orm import ApiKey

    if role not in ("reviewer", "admin"):
        console.print("[red]role must be 'reviewer' or 'admin'[/red]")
        raise typer.Exit(1)

    raw_key = f"atlas_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    key_id = str(uuid.uuid4())

    async def _run() -> None:
        async with direct_session() as session:
            stmt = pg_insert(ApiKey.__table__).values(
                id=key_id,
                key_hash=key_hash,
                operator_id=operator_id,
                role=role,
                is_active=True,
                description=description or None,
            ).on_conflict_do_nothing(index_elements=["key_hash"])
            await session.execute(stmt)

    asyncio.run(_run())

    console.print(f"\n[bold green]✓ API key created for {operator_id} (role: {role})[/bold green]")
    console.print("\n[bold yellow]Raw key (copy now — shown only once):[/bold yellow]")
    console.print(f"\n  [cyan]{raw_key}[/cyan]\n")
    console.print(f"[dim]Key ID: {key_id}[/dim]")
    console.print(
        f"\nPass as header:  [bold]X-API-Key: {raw_key}[/bold]\n"
        f"or set env var:  [bold]ATLAS_API_KEY={raw_key}[/bold]"
    )


@keys_app.command("list")
def keys_list() -> None:
    """List all registered API keys (hashes and metadata, not raw keys)."""
    from sqlalchemy import select

    from atlas.db.engine import direct_session
    from atlas.models.orm import ApiKey

    async def _run() -> list:
        async with direct_session() as session:
            rows = (await session.execute(select(ApiKey).order_by(ApiKey.created_at))).scalars().all()
            return list(rows)

    keys = asyncio.run(_run())
    t = Table(title="API Keys")
    t.add_column("ID"); t.add_column("Operator"); t.add_column("Role")
    t.add_column("Active"); t.add_column("Last used"); t.add_column("Description")
    for k in keys:
        t.add_row(
            k.id[:8] + "…",
            k.operator_id,
            k.role,
            "✓" if k.is_active else "✗",
            str(k.last_used_at.date()) if k.last_used_at else "never",
            k.description or "",
        )
    console.print(t)


@keys_app.command("revoke")
def keys_revoke(key_id: str = typer.Argument(..., help="Key ID prefix or full ID")) -> None:
    """Revoke an API key by setting is_active=False."""
    from sqlalchemy import update

    from atlas.db.engine import direct_session
    from atlas.models.orm import ApiKey

    async def _run() -> int:
        async with direct_session() as session:
            result = await session.execute(
                update(ApiKey)
                .where(ApiKey.id.startswith(key_id))
                .values(is_active=False)
                .returning(ApiKey.id)
            )
            return len(result.all())

    n = asyncio.run(_run())
    if n:
        console.print(f"[green]✓ Revoked {n} key(s)[/green]")
    else:
        console.print("[red]No matching key found[/red]")


if __name__ == "__main__":
    app()
