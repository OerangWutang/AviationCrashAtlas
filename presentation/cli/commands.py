from __future__ import annotations

import asyncio
import json
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import typer

from atlas.application.dto import IngestionClaimDTO
from atlas.application.use_cases.audit_chain_verify import (
    CHAINED_TABLES,
    verify_all,
    verify_table,
)
from atlas.application.use_cases.ingest_source_data import IngestSourceData
from atlas.application.use_cases.query_conflict_history import QueryConflictHistory
from atlas.application.use_cases.rebuild_all_projections import RebuildAllProjections
from atlas.application.use_cases.reproject_event import ReProjectEvent
from atlas.application.use_cases.run_hermes_fetch_job import RunHermesFetchJob
from atlas.config import get_settings
from atlas.domain.entities import Source
from atlas.domain.enums import Role, SourceKind
from atlas.infrastructure.db.orm_models import ApiKeyModel
from atlas.infrastructure.db.session import async_session_factory
from atlas.infrastructure.db.unit_of_work import create_uow
from atlas.infrastructure.event_bus.outbox_worker import OutboxWorker
from atlas.logging_config import setup_logging
from atlas.security import hash_api_key

app = typer.Typer(no_args_is_help=True)


def _setup() -> None:
    """Call once per command entry-point, not at import time."""
    setup_logging()


@app.command("ingest")
def ingest(
    source_id: UUID,
    file: Path | None = typer.Option(None, help="JSON file with raw_payload and claims"),
    raw: str | None = typer.Option(None, help="Inline raw payload JSON"),
    claims: str | None = typer.Option(None, help="Inline claims JSON array"),
    event_id: UUID | None = typer.Option(None, help="Existing event ID to ingest into"),
):
    _setup()

    async def run() -> None:
        if file:
            data = json.loads(file.read_text())
            raw_payload = data.get("raw_payload", data.get("raw", data))
            claims_data = data.get("claims", [])
        else:
            raw_payload = json.loads(raw or "{}")
            claims_data = json.loads(claims or "[]")
        async with create_uow() as uow:
            eid = await IngestSourceData(uow).execute(
                source_id=source_id,
                raw_payload=raw_payload,
                ingestion_run_id=uuid4(),
                claims_data=[IngestionClaimDTO(**item) for item in claims_data],
                event_id=event_id,
            )
            typer.echo(f"Ingestion successful. Event ID: {eid}")

    asyncio.run(run())


@app.command("projections-rebuild")
def projections_rebuild(
    event_id: UUID | None = typer.Option(None),
    all: bool = typer.Option(False, "--all"),
):
    _setup()

    async def run() -> None:
        async with create_uow() as uow:
            if all:
                result = await RebuildAllProjections(uow).execute()
                typer.echo(f"Rebuilt {result.processed} projections ({result.skipped} skipped)")
            elif event_id:
                await ReProjectEvent(uow).execute(event_id)
                typer.echo(f"Rebuilt projection for {event_id}")
            else:
                raise typer.BadParameter("Use --event-id or --all")

    asyncio.run(run())


@app.command("outbox-process")
def outbox_process(limit: int = typer.Option(100)):
    _setup()

    async def run() -> None:
        processed = await OutboxWorker(worker_id="cli").process_batch(limit=limit)
        typer.echo(f"Processed {processed} outbox events")

    asyncio.run(run())


@app.command("outbox-worker")
def outbox_worker(sleep_seconds: float = typer.Option(5.0)):
    _setup()
    asyncio.run(OutboxWorker(worker_id="cli-worker").run_loop(sleep_seconds=sleep_seconds))


@app.command("hermes-worker")
def hermes_worker(
    sleep_seconds: float = typer.Option(5.0, help="Seconds to sleep when no jobs are due"),
    batch_limit: int = typer.Option(1, min=1, max=100, help="Jobs to claim per polling cycle"),
    lease_seconds: int = typer.Option(300, min=30, max=3600, help="Claim lease duration"),
    recover_limit: int = typer.Option(
        100, min=1, max=1000, help="Expired RUNNING jobs to recover per cycle"
    ),
    once: bool = typer.Option(False, "--once", help="Process one polling cycle and exit"),
):
    """Run the Hermes fetch queue worker.

    The worker first recovers expired RUNNING leases, then atomically claims due
    QUEUED jobs using claim_next_running().  Each job is finalized with lease
    fencing so stale workers cannot overwrite recovered claims.

    Recovery audit trail:  when a recovered job has exhausted its retry budget
    the worker emits a ``FETCH_FAILED`` ``HermesSourceChange`` so the failure
    surfaces in the target's change stream, not only in the job record.
    Requeued recoveries do not emit a change event because the next run will
    produce one if it also fails.
    """
    from atlas.domain.entities import HermesSourceChange
    from atlas.domain.enums import HermesChangeType
    from atlas.domain.enums import HermesFetchJobStatus as _Status

    _setup()
    settings = get_settings()
    settings.validate_hermes_worker_settings()
    _allowed_hosts = tuple(settings.hermes_allowed_hosts)

    async def run() -> None:
        worker_prefix = f"hermes-worker:{uuid4()}"
        while True:
            processed = 0

            async with create_uow() as uow:
                outcomes = await uow.hermes_fetch_jobs.recover_stale_running(
                    now=datetime.now(UTC),
                    limit=recover_limit,
                )
                if outcomes:
                    now = datetime.now(UTC)
                    terminal = [o for o in outcomes if o.final_status == _Status.FAILED]
                    for outcome in terminal:
                        # One FETCH_FAILED change per terminally-failed
                        # recovery; the job record's error_message still
                        # carries the lease-expiry reason.
                        await uow.hermes_source_changes.add(
                            HermesSourceChange(
                                target_id=outcome.target_id,
                                fetch_job_id=outcome.job_id,
                                change_type=HermesChangeType.FETCH_FAILED,
                                detected_at=now,
                            )
                        )
                    await uow.commit()
                    typer.echo(
                        f"Recovered {len(outcomes)} stale Hermes jobs "
                        f"({len(terminal)} terminal, {len(outcomes) - len(terminal)} requeued)"
                    )

            for _ in range(batch_limit):
                async with create_uow() as uow:
                    worker_id = f"{worker_prefix}:{uuid4()}"
                    job = await uow.hermes_fetch_jobs.claim_next_running(
                        worker_id=worker_id,
                        lease_expires_at=datetime.now(UTC) + timedelta(seconds=lease_seconds),
                    )
                    if job is None:
                        break
                    result = await RunHermesFetchJob(
                        uow,
                        worker_id_prefix=worker_prefix,
                        lease_seconds=lease_seconds,
                        allowed_hosts=_allowed_hosts,
                    ).execute_claimed(job)
                    processed += 1
                    typer.echo(f"Hermes job {result.job_id} -> {result.status.value}")

            if once:
                break
            if processed == 0:
                await asyncio.sleep(sleep_seconds)

    asyncio.run(run())


@app.command("conflicts-history")
def conflicts_history(conflict_id: UUID):
    _setup()

    async def run() -> None:
        async with create_uow() as uow:
            result = await QueryConflictHistory(uow).execute(conflict_id)
            typer.echo(json.dumps(result, default=str, indent=2))

    asyncio.run(run())


@app.command("bootstrap")
def bootstrap(
    role: str = typer.Option(
        "admin", help=f"Role for the generated API key. One of: {', '.join(Role.values())}"
    ),
    api_key: str | None = typer.Option(None, help="Optional plain API key to hash and store"),
):
    """Create the CuratorOverride source and a development API key.

    Safe to run multiple times: the source insert is idempotent (ON CONFLICT
    DO NOTHING) and the key is always a fresh UUID.
    """
    _setup()

    # Validate role before any async work so the error surfaces immediately
    # with a clear message rather than a constraint violation from Postgres.
    if role not in Role.values():
        typer.echo(
            f"Invalid role {role!r}. Must be one of: {', '.join(sorted(Role.values()))}",
            err=True,
        )
        raise typer.Exit(code=2)

    async def run() -> None:
        plain_key = api_key or secrets.token_urlsafe(32)
        key_hash = hash_api_key(plain_key)
        user_id = uuid4()

        # Step 1 - seed the CuratorOverride source (own transaction).
        async with create_uow() as uow:
            settings = get_settings()
            existing = await uow.sources.get(settings.curator_override_source_id)
            if not existing:
                await uow.sources.add(
                    Source(
                        id=settings.curator_override_source_id,
                        name=settings.curator_override_source_name,
                        kind=SourceKind.INTERNAL,
                        reliability_tier=1,
                    )
                )
            await uow.commit()

        # Step 2 - create the API key (separate transaction so a key failure
        # does not roll back the source seed).
        async with async_session_factory() as session:
            try:
                session.add(ApiKeyModel(id=uuid4(), key_hash=key_hash, user_id=user_id, role=role))
                await session.commit()
            except Exception as exc:
                await session.rollback()
                typer.echo(f"Failed to create API key: {exc}", err=True)
                raise typer.Exit(code=1) from exc

        typer.echo("Bootstrap complete.")
        typer.echo(f"User ID:  {user_id}")
        typer.echo(f"API key:  {plain_key}")
        typer.echo("Store this key securely; only its hash was saved.")

    asyncio.run(run())


@app.command("audit-chain-verify")
def audit_chain_verify(
    table: str | None = typer.Option(
        None,
        help=(f"Single table to verify. One of: {', '.join(CHAINED_TABLES)}. Omit to verify all."),
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON instead of text."
    ),
) -> None:
    """Recompute the audit hash chain and report tamper.

    Exits non-zero (3) if any protected table fails verification, so this
    command is safe to schedule as a cron / CI check.  Exit code 2 is
    reserved for invalid arguments (typer convention).

    AUDIT_CHAIN_SECRET must be set in the environment — the verifier
    function reads it from the same session GUC the trigger uses.
    """
    _setup()

    async def run() -> int:
        async with create_uow() as uow:
            if table is not None:
                results = [await verify_table(uow, table)]
            else:
                results = await verify_all(uow)

        any_bad = any(not r.ok for r in results)
        if json_output:
            payload = [
                {
                    "table": r.table_name,
                    "row_count": r.row_count,
                    "ok": r.ok,
                    "first_bad_row_id": (str(r.first_bad_row.row_id) if r.first_bad_row else None),
                    "first_bad_position": (
                        r.first_bad_row.row_position if r.first_bad_row else None
                    ),
                }
                for r in results
            ]
            typer.echo(json.dumps(payload, indent=2))
        else:
            for r in results:
                if r.ok:
                    typer.echo(f"OK   {r.table_name}: {r.row_count} row(s)")
                else:
                    bad = r.first_bad_row
                    assert bad is not None  # narrowed by r.ok
                    typer.echo(
                        f"FAIL {r.table_name}: row {bad.row_id} at "
                        f"position {bad.row_position} ({r.row_count} row(s) scanned)",
                        err=True,
                    )
        return 3 if any_bad else 0

    code = asyncio.run(run())
    if code != 0:
        raise typer.Exit(code=code)


# ── TICKET-009: MFA + key rotation ──────────────────────────────────────────


@app.command("evidence-verify")
def evidence_verify(
    document_id: UUID = typer.Option(..., help="UUID of the uploaded document to verify."),
    signature: str = typer.Option(..., help="The evidence signature from the upload receipt."),
) -> None:
    """Verify an evidence signature against a stored document hash.

    Uses the evidence signing secret to recompute the HMAC and compares
    it against the provided signature.  Exits non-zero if verification
    fails, so this command is safe to schedule as a cron / CI check.
    """
    _setup()

    async def run() -> bool:
        async with create_uow() as uow:
            session = uow._session  # type: ignore[attr-defined]
            from sqlalchemy import text as sa_text

            result = await session.execute(
                sa_text("SELECT content_sha256, filename FROM uploaded_documents WHERE id = :id"),
                {"id": str(document_id)},
            )
            row = result.one_or_none()
            if row is None:
                typer.echo(f"ERROR: Document {document_id} not found.", err=True)
                return False

            content_sha256, filename = row

        from atlas.security import sign_evidence_hash

        recomputed = sign_evidence_hash(content_sha256)
        valid = recomputed == signature

        if valid:
            typer.echo(f"OK   {filename}: evidence signature is valid")
            return True
        typer.echo(
            f"FAIL {filename}: evidence signature mismatch\n"
            f"  Stored hash: {content_sha256}\n"
            f"  Expected:    {recomputed}\n"
            f"  Received:    {signature}",
            err=True,
        )
        return False

    code = 0 if asyncio.run(run()) else 3
    if code != 0:
        raise typer.Exit(code=code)


@app.command("mfa-enroll")
def mfa_enroll(
    api_key_id: UUID = typer.Option(..., help="ID of the API key to enroll into TOTP MFA."),
    enrolled_by: UUID = typer.Option(
        ..., help="User UUID performing the enrollment (recorded for the audit trail)."
    ),
    account_label: str = typer.Option(
        "atlas-key",
        help="Label that appears alongside the issuer in the authenticator app.",
    ),
) -> None:
    """Generate a fresh TOTP seed, encrypt it, and attach it to an API key.

    Emits the seed and an ``otpauth://`` URI exactly once on stdout — Atlas
    only stores the AES-256-GCM ciphertext, so a lost seed cannot be
    recovered, only re-enrolled.  Requires ``MFA_KEK`` in the environment.
    """
    _setup()

    from sqlalchemy import select

    from atlas.security.mfa import (
        MfaConfigurationError,
        build_otpauth_uri,
        encrypt_seed,
        generate_seed,
    )

    async def run() -> None:
        try:
            seed = generate_seed()
            wrapped = encrypt_seed(seed)
        except MfaConfigurationError as exc:
            typer.echo(f"MFA not configured: {exc}", err=True)
            raise typer.Exit(code=2) from exc

        async with async_session_factory() as session:
            stmt = select(ApiKeyModel).where(ApiKeyModel.id == api_key_id)
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                typer.echo(f"No API key with id {api_key_id}", err=True)
                raise typer.Exit(code=1)
            row.mfa_required = True
            row.mfa_secret_encrypted = wrapped
            row.mfa_enrolled_at = datetime.now(UTC)
            row.mfa_enrolled_by = enrolled_by
            await session.commit()

        uri = build_otpauth_uri(account_label, seed)
        typer.echo("MFA enrollment complete.")
        typer.echo(f"API key id:  {api_key_id}")
        typer.echo(f"TOTP seed:   {seed}")
        typer.echo(f"otpauth URI: {uri}")
        typer.echo(
            "Scan the URI into an authenticator app now — the seed is not "
            "stored in plaintext and cannot be retrieved later."
        )

    asyncio.run(run())


@app.command("mfa-disable")
def mfa_disable(
    api_key_id: UUID = typer.Option(..., help="ID of the API key to remove MFA from."),
) -> None:
    """Clear MFA on an API key.

    Use this for break-glass when a TOTP secret is lost — destructive
    endpoints will fail closed for this key until re-enrollment.  The CHECK
    constraint requires the secret to be cleared alongside the flag.
    """
    _setup()

    from sqlalchemy import select

    async def run() -> None:
        async with async_session_factory() as session:
            stmt = select(ApiKeyModel).where(ApiKeyModel.id == api_key_id)
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                typer.echo(f"No API key with id {api_key_id}", err=True)
                raise typer.Exit(code=1)
            row.mfa_required = False
            row.mfa_secret_encrypted = None
            row.mfa_enrolled_at = None
            row.mfa_enrolled_by = None
            await session.commit()
        typer.echo(f"MFA disabled on api key {api_key_id}.")

    asyncio.run(run())


@app.command("key-rotate")
def key_rotate(
    api_key_id: UUID = typer.Option(..., help="ID of the API key to rotate."),
) -> None:
    """Rotate an API key's plaintext value while preserving identity.

    The "password reset" analog for the API-key surface: the row's
    ``id``/``user_id``/``role``/``tenant_*``/MFA enrollment are all
    preserved, so existing access-control bindings and the MFA seed stay
    intact, but every cached copy of the old key stops authenticating
    immediately (the key_hash changes, evicting it from any in-process
    auth cache).  Prints the new plaintext key exactly once.
    """
    _setup()

    from sqlalchemy import select

    async def run() -> None:
        new_plain = secrets.token_urlsafe(32)
        new_hash = hash_api_key(new_plain)

        async with async_session_factory() as session:
            stmt = select(ApiKeyModel).where(ApiKeyModel.id == api_key_id)
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                typer.echo(f"No API key with id {api_key_id}", err=True)
                raise typer.Exit(code=1)
            row.key_hash = new_hash
            row.is_active = True
            await session.commit()

        typer.echo("Key rotation complete.")
        typer.echo(f"API key id: {api_key_id}")
        typer.echo(f"New key:    {new_plain}")
        typer.echo("Store this key securely; only its hash was saved.")

    asyncio.run(run())


@app.command("retention-sweep")
def retention_sweep(
    batch_per_table: int = typer.Option(
        100,
        "--batch-per-table",
        help="Maximum rows soft-deleted per compliance table per invocation.",
        min=1,
    ),
    auth_attempt_max_age_minutes: int = typer.Option(
        24 * 60,
        "--auth-attempt-max-age-minutes",
        help="Drop api_key_attempts older than this many minutes (set 0 to skip).",
        min=0,
    ),
    reason: str = typer.Option(
        "automatic retention sweep",
        "--reason",
        help="Reason recorded in the compliance ledger for each swept row.",
    ),
) -> None:
    """Apply retention deadlines and prune brute-force tracking rows.

    Soft-deletes rows in the five compliance tables where
    ``retention_until <= now()`` and no legal hold is set, writing one
    ``DELETION_APPLIED`` entry per row to ``compliance_events`` so the
    audit chain captures the action.  Optionally prunes stale rows from
    ``api_key_attempts`` (migration 054).
    """
    _setup()

    from atlas.application.use_cases.retention_sweep import (
        RetentionSweep,
        prune_auth_attempts,
    )

    async def run() -> None:
        async with create_uow() as uow:
            result = await RetentionSweep(uow).execute(
                batch_per_table=batch_per_table,
                reason=reason,
            )
            pruned = 0
            if auth_attempt_max_age_minutes > 0:
                pruned = await prune_auth_attempts(
                    uow,
                    max_age_minutes=auth_attempt_max_age_minutes,
                )
        typer.echo(f"Retention sweep complete. Soft-deleted: {result.total_swept}")
        for entity_type in sorted(result.swept_per_table):
            typer.echo(f"  {entity_type}: {result.swept_per_table[entity_type]}")
        if auth_attempt_max_age_minutes > 0:
            typer.echo(f"Pruned api_key_attempts rows: {pruned}")

    asyncio.run(run())
