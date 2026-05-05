# Aviation Safety Atlas — Data Retention and Archival Policy

This policy defines how Aviation Safety Atlas stores, archives, and restores
source records, claims, projections, and audit history. It is intentionally
conservative: the platform should preserve provenance and auditability while
keeping the hot operational database small enough to query, back up, and
migrate reliably.

The core rule is: **never delete data that is required to explain a published
accident record unless the data has first been archived and its archive location
is recorded.**

## Scope

This policy covers these high-growth tables and data classes:

| Data class | Tables / examples | Why it grows |
|---|---|---|
| Raw source payloads | `raw_snapshots` | Every ingestion run stores immutable source payloads. |
| Field-level truth store | `claims`, `claim_history` | Each source value and each claim-type transition is tracked. |
| Projection audit trail | `event_revisions` | Every ingestion/projection action can create timeline entries. |
| Ingestion audit | `ingestion_runs` | Every run records status, counts, and diagnostics. |
| Source-document checks | `source_documents` and check metadata | Link checks update verification history over time. |
| Derived projections | `accident_records` | Current read model; derived from claims. |

The policy does **not** cover application logs, infrastructure logs, database
WAL archives, object-storage lifecycle rules, or backups. Those should be
managed by the deployment environment.

## Retention tiers

Aviation Safety Atlas uses three retention tiers.

| Tier | Purpose | Storage | Typical access pattern |
|---|---|---|---|
| Hot | Fast API queries, reviewer workflows, recent audit trail | Primary Postgres database | Online, low-latency |
| Warm archive | Auditable historical records that are rarely queried | Archive schema/table partitions or object storage | Admin restore/export |
| Cold backup | Disaster recovery and legal hold | Encrypted database/object backups | Restore whole environment or selected archive objects |

## Default retention schedule

| Table / data class | Hot retention | Archive behavior | Deletion behavior |
|---|---:|---|---|
| `accident_records` | Current projection indefinitely | Not archived separately; rebuildable from claims | Do not delete except when deleting the owning event. |
| `claims` | Indefinite while event exists | Do not archive away from hot DB until a claim partitioning strategy exists | Do not delete; claims are the truth store. |
| `claim_history` | 24 months hot | Archive entries older than 24 months by year/month partition | Delete from hot DB only after archive verification. |
| `event_revisions` | 24 months hot | Archive entries older than 24 months by year/month partition | Delete from hot DB only after archive verification. |
| `raw_snapshots` | 24 months hot | Archive payloads older than 24 months to compressed object storage or archive partitions | Delete hot payload only after checksum verification and archive pointer is recorded. |
| `ingestion_runs` | Summary rows indefinitely; verbose diagnostics 24 months | Archive verbose payloads/log details older than 24 months | Keep run summary rows; delete/archive only verbose blobs. |
| `source_documents` | Indefinite while referenced | Keep current document metadata hot | Do not delete while referenced by an event/claim. |
| Link-check metadata | 24 months hot | Archive older check history if stored separately | Keep latest status hot. |

These are defaults. A deployment may retain hot data longer, but should not
retain less without a documented legal/licensing reason and a tested restore
process.

## Non-negotiable invariants

1. `claims` are the truth store. Do not purge active claims to save space.
2. `accident_records` is derived. It can be rebuilt, but it must not become the
   only place a value exists.
3. Open conflicts must remain fully reviewable. Do not archive away claims,
   claim history, source documents, or revisions needed to resolve an open
   conflict.
4. Any archived raw payload must have a checksum and enough metadata to restore
   or audit it later.
5. Archive jobs must be idempotent. Re-running an archive job must not duplicate
   archive objects or corrupt archive manifests.
6. Archive and purge jobs must be disabled by default in local/dev.
7. Destructive hot-table purges must support dry-run mode.

## Archive object format

When archiving to object storage, use deterministic paths:

```text
s3://<bucket>/aviation-safety-atlas/archive/<table>/year=YYYY/month=MM/<table>-YYYY-MM-<batch>.jsonl.zst
```

Recommended formats:

| Data | Format |
|---|---|
| Structured rows | JSON Lines compressed with Zstandard (`.jsonl.zst`) |
| Raw source payloads | Original payload where possible, otherwise canonical JSON, compressed |
| Archive manifest | JSON with counts, checksums, DB revision, and query window |

Each archive batch should produce a manifest:

```json
{
  "table": "raw_snapshots",
  "from": "2024-01-01T00:00:00Z",
  "to": "2024-02-01T00:00:00Z",
  "row_count": 12345,
  "sha256": "...",
  "created_at": "2026-05-04T12:00:00Z",
  "database_revision": "0015_crew_passenger_injury_splits",
  "archive_uri": "s3://bucket/.../raw_snapshots-2024-01-0001.jsonl.zst"
}
```

## Archive metadata in Postgres

Before enabling automated archive jobs, add an archive manifest table:

```sql
CREATE TABLE archive_manifests (
  id uuid PRIMARY KEY,
  table_name text NOT NULL,
  archive_uri text NOT NULL,
  window_start timestamptz NOT NULL,
  window_end timestamptz NOT NULL,
  row_count integer NOT NULL,
  sha256 text NOT NULL,
  database_revision text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by text NOT NULL,
  restored_at timestamptz,
  restore_notes text
);
```

The first implementation may be documentation-only, but destructive purge jobs
must not ship without this or an equivalent manifest registry.

## Partitioning recommendation

For large deployments, partition high-growth audit tables by month or year:

| Table | Recommended partition key | Notes |
|---|---|---|
| `raw_snapshots` | `ingested_at` or `created_at` | Use monthly partitions once ingestion volume is high. |
| `claim_history` | `created_at` | Monthly or yearly depending on reviewer volume. |
| `event_revisions` | `created_at` | Monthly or yearly. |
| `ingestion_runs` | `started_at` | Yearly is usually enough; verbose details can be archived separately. |

Do not partition `claims` until query patterns and foreign-key behavior are
well understood. Claims are core online data.

## Restore expectations

A valid archive process must support three restore modes:

1. **Audit export** — retrieve archived rows for one event/source/date range
   without modifying production.
2. **Staging restore** — restore archived rows into a staging database for
   investigation.
3. **Hot restore** — restore rows into production only through a controlled
   admin procedure, with audit logging.

Hot restore must be rare and should require admin approval.

## Operational runbook

### Monthly archive dry run

```bash
atlas archive dry-run --older-than 24mo --tables raw_snapshots,claim_history,event_revisions
```

Expected output:

```text
raw_snapshots: 154321 rows eligible, estimated 8.2 GiB compressed
claim_history: 92143 rows eligible, estimated 430 MiB compressed
event_revisions: 33120 rows eligible, estimated 95 MiB compressed
No rows deleted in dry-run mode.
```

### Monthly archive execution

```bash
atlas archive run --older-than 24mo --tables raw_snapshots,claim_history,event_revisions
```

Execution must:

1. select a bounded time window;
2. write compressed archive objects;
3. write archive manifest rows;
4. verify row counts and checksums;
5. delete hot rows only after verification;
6. emit metrics and structured logs.

### Restore drill

At least once per quarter, restore one archived batch into staging and verify:

- manifest checksum matches;
- row count matches;
- source payloads can be decoded;
- related event provenance can be reconstructed;
- no production data is modified.

## Metrics and alerts to add with automation

When archive jobs are implemented, emit:

| Metric | Labels | Meaning |
|---|---|---|
| `atlas_archive_runs_total` | `table`, `status` | Archive job count by outcome. |
| `atlas_archive_rows_total` | `table`, `status` | Rows archived/deleted/restored. |
| `atlas_archive_bytes_total` | `table` | Compressed bytes written. |
| `atlas_archive_oldest_hot_row_age_seconds` | `table` | Age of oldest row still in hot storage. |
| `atlas_archive_duration_seconds` | `table` | Archive job duration. |

Alert on:

- archive job failure;
- oldest hot audit row exceeding policy by more than 30 days;
- checksum verification failure;
- restore drill not performed in the last quarter.

## Legal and licensing notes

- NTSB data is public domain, but non-NTSB sources require source-specific
  licensing review before ingestion and before archival/redistribution.
- If a non-NTSB license requires deletion, the deletion process must preserve an
  audit marker explaining that a source payload was removed for licensing/legal
  reasons without keeping prohibited content.
- Legal holds override normal purge windows.

## Current implementation status

This document is the policy baseline. As of this release:

- no automated archive/purge CLI is enabled;
- no hot-table destructive deletion should be run manually except through a
  reviewed operations procedure;
- `claims` remain hot indefinitely;
- archive automation requires a future migration for `archive_manifests` and a
  dry-run-first CLI.

## Implementation checklist for archive automation

Before enabling automated archiving, implement:

1. `archive_manifests` migration and ORM model;
2. `atlas archive dry-run`;
3. `atlas archive run` with bounded windows and checksums;
4. `atlas archive restore` for staging restore;
5. metrics listed above;
6. integration tests proving dry-run does not delete rows;
7. integration tests proving archive + restore round-trip;
8. production documentation for archive storage credentials;
9. quarterly restore drill runbook.

## Automation status

The repository now includes a conservative archival implementation:

```bash
PYTHONPATH=src atlas archive plan --cutoff-days 730
PYTHONPATH=src atlas archive run --output-dir .generated/archive --cutoff-days 730
PYTHONPATH=src atlas archive run --output-dir .generated/archive --cutoff-days 730 --execute
```

`archive run` writes JSONL files and an archive manifest. Without `--execute`,
it is a dry run and does not delete rows. With `--execute`, exported rows older
than the cutoff are deleted from supported high-growth tables and the manifest is
recorded in `archive_manifests`.

Supported tables in this first implementation:

- `ingestion_runs`
- `event_revisions`
- `claim_history`
- `raw_snapshots`

Before enabling destructive archival in production, run at least one restore
drill from the JSONL output and verify the manifest row matches the files in
cold storage.

## Restore command

Archive restore is available for supported JSONL archive tables:

```bash
PYTHONPATH=src atlas archive restore .generated/archive/manifest-<id>.json
PYTHONPATH=src atlas archive restore .generated/archive/manifest-<id>.json --execute
```

The default is a dry-run row count. `--execute` merges rows by primary key into
the current database. Always restore into staging first, compare counts, then run
production restore only with an approved incident/change ticket.
