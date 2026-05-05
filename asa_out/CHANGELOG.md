
## v28.7 CI hardening update

- Split CI into fast unit/lint checks and real PostGIS + Redis integration checks.
- Added Redis service coverage to GitHub Actions and Docker Compose smoke testing.
- Added Alembic downgrade/upgrade verification to the integration CI path.
- Added `scripts/ci_full_stack.sh` and `make ci-full` for local reproduction of the full CI chain.
- Added frontend lint to CI/smoke in addition to TypeScript type-check.
- Documented CI guarantees in `docs/ci.md`.

# Changelog

## v28.7 large-data performance fixtures

- Added deterministic synthetic large-data fixture generator in `scripts/generate_large_data_fixture.py`.
- Added performance profiles for smoke, local 10k, and nightly 100k fixture generation.
- Added `scripts/performance_smoke.py` for lightweight endpoint timing checks against a running API.
- Added `docs/performance-fixtures.md` with generation, ingestion, smoke-test, and CI/nightly guidance.
- Added Makefile targets for fixture generation and performance smoke runs.

## v28.7 monitoring docs update

- Added Prometheus alert rules in `monitoring/prometheus/atlas-alerts.yml`.
- Added Grafana overview dashboard JSON in `monitoring/grafana/aviation-safety-atlas-overview.json`.
- Added `docs/alerts.md` with alert explanations, tuning notes, readiness blackbox guidance, and runbook pointers.
- Added `docs/dashboards.md` with dashboard panel definitions and interpretation guidance.
- Linked monitoring docs from README and production deployment guide.



## v28.7 retention policy update

- Added `docs/data-retention.md` with table-by-table hot retention windows,
  archive object format, archive manifest requirements, restore expectations,
  partitioning guidance, metrics/alerts, and legal/licensing notes.
- Linked the retention policy from README and production deployment docs.
- Documented that archive automation is not yet enabled and must require dry-run,
  manifest, checksum, restore, and metrics safeguards before destructive purge.


All notable user-facing and architectural changes to Aviation Safety Atlas.
The earliest entries (v20) are preserved as the original "honesty pass"
context that motivates many of the product invariants you see today.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com).
Releases are tagged from the migration counter — migration 0011 ⇒ "v0.11"
in spirit, even though there is no semantic-version Python release yet.

---

## v0.27 — Field-level resolution model + reviewer queue

**Resolution correctness**
- New `REJECTED` claim type — claims explicitly discarded during conflict
  resolution are moved here, no longer left as `DISPUTED`.  The projection
  layer never restores a `REJECTED` claim regardless of resolution decisions.
- `ConflictResolutionService` (extracted from the route) owns every
  invariant: validation, optimistic locking via `SELECT FOR UPDATE` and the
  new `claim_conflicts.version` counter, REJECTED-marking, finalisation,
  and projection rebuild.
- `claim_rejected` resolutions auto-derive the surviving claim as the
  accepted winner — "B is wrong" ⇒ "so A wins".
- Contradiction detection: a claim accepted in one conflict but rejected
  in another keeps the field withheld with a structured warning log.

**Authentication**
- API key auth (`X-API-Key` header) with reviewer/admin roles.  `auth.py`
  enforces 401/403 and never trusts client-provided `resolved_by`.
- `atlas keys create | list | revoke` CLI.  Raw keys shown once, only
  SHA-256 hashes stored.  Optional `expires_at` per key.
- Successful API-key authentication now persists `api_keys.last_used_at`
  through a dedicated auth session instead of mutating an uncommitted
  read-only session.
- Disabled by default for local dev (`API_AUTH_ENABLED=false`).  When
  disabled, reviewer write endpoints remain unauthenticated for local/CI
  compatibility, but admin override endpoints are disabled instead of accepting
  a synthetic operator. The API logs a prominent warning at startup so
  production deployments cannot accidentally ship without auth.

**Reviewer experience**
- Global `/conflicts` queue page with field-filter sidebar, status
  counters, and oldest-first ordering.
- Conflict queue "Review" links now deep-link to `/?selected=...&tab=technical`,
  and the search/detail page consumes that tab request so reviewers land on
  the technical provenance panel instead of an empty/default overview state.
- Reviewer API keys can now be entered in the web UI, stored browser-locally,
  shared between the conflict queue and detail page, and forwarded as `X-API-Key`
  to conflict-resolution requests.
- Frontend claim typings now include backend `ClaimType.REJECTED`; rejected
  claims render with a strikethrough badge instead of falling through as an
  unknown/invalid claim type. The API schema now exposes claim types as a
  Literal/OpenAPI enum, and the frontend uses a single `CLAIM_TYPES` tuple plus
  exhaustive badge/label mappings to prevent future backend/frontend drift.
- Projection explanations no longer treat source tier as proof of finality.
  `selected_official_final` now requires a verified, available final-report
  `SourceDocument` from the selected source; tier-1 records without such
  evidence are labelled as latest official source instead.
- `GET /api/v1/conflicts` and `GET /api/v1/conflicts/stats` endpoints.
- `POST /api/v1/admin/events/{event_id}/force-resolve-field` admin
  override for contradicted-deadlock fields. It now depends on explicit
  admin auth and refuses auth-disabled mode.
- `ErrorBoundary` wrapping for `ProvenancePanel` and `ConflictReviewPanel`
  so a single malformed API response cannot blank the page.

**Multi-source ingestion**
- `atlas ingest generic-csv` with column-mapping JSON.  Bundled mappings
  for ASN and ICAO; bring-your-own mapping supported.  `--dry-run`
  validates without DB writes.
- Generic CSV normalisation now returns Python-typed canonical values, matching
  the NTSB normaliser. `ClaimWriter` is the single claim-value encoding
  boundary and rejects pre-encoded envelopes, preventing nested/double-encoded
  claim values in JSONB.

**Search and pagination**
- `pg_trgm` GIN expression indexes matching the actual case-insensitive
  search predicate: `lower(location_text)`, `lower(aircraft_make)`,
  `lower(aircraft_model)`, `lower(operator_name)`, and
  `lower(probable_cause)`. This replaces full-table substring scans.
- Cursor (keyset) pagination via `?cursor=` token in `/accidents`.
  Stable under concurrent inserts; old `?page=` clients still work.

**Operational**
- SSRF guard in `atlas check-links` — rejects private/loopback IPs and
  optional domain allowlist.

---

## v0.20 — Honesty pass

The v20 release closed a class of "looks more certain than it is" bugs
in the user-facing surfaces.  None were data-corruption bugs; they were
presentation bugs that overstated what the data actually supported.

- **Open disputes no longer fabricate a winner.**  When the projection
  withholds a field because its claims are in conflict, the UI shows
  *No projected value while this dispute is open.* — not a confident
  *"Displayed value: X"* line followed by an invented justification.
- **Selection rationale is backend-supplied.**  `ProjectionService`
  emits a structured `selection_reason` per projected field
  (`only_active_claim`, `selected_higher_tier`, `withheld_open_dispute`,
  `approximate_nearest_city_only`, …).  The frontend humanises these
  but never invents them.
- **Source documents are extracted, but conservatively.**  The
  ingestion pipeline creates `SourceDocument` rows from real URL fields
  in the NTSB raw payload plus a single deterministic CAROL search URL.
  URL verification remains a separate `atlas check-links` step.
  Documents are labelled *Linked, unverified* until that step runs.
- **Field status reflects evidence, not investigation maturity.**
  Final-report status no longer promotes every field to *Confirmed*.
  Fields without a winning claim are shown as *Unverified* or
  *Source not loaded* depending on whether provenance was loaded.
- **Real timeline.**  The pipeline writes `event_revisions` rows for
  *first-seen*, *snapshot-changed*, *snapshot-unchanged*,
  *field-added*, *field-removed*, *document-linked*, and
  *projection-rebuilt* events.  The Provenance panel's "How this record
  evolved" section reads these rather than synthesising entries.
- **Symmetric ingestion run tracking.**  Both `run_ntsb_csv` and
  `run_ntsb_api` create an `IngestionRun` row at start and finalise it
  as `completed` / `failed` on exit, sharing `_create_run_record` /
  `_finalize_run_record` helpers.  v19 only persisted CSV runs.
- **One-active-claim DB invariant.**  A partial unique index on
  `claims(event_id, source_id, field_name) WHERE claim_type <>
  'superseded'` makes the application invariant Postgres-enforced.

---

## Earlier

The first 19 iterations established the claim/conflict/projection model,
the NTSB ingestion adapter, and the FastAPI/Next.js layer.  See the git
history and the migration chain (`migrations/versions/0001_*` onward) for
the schema evolution.

## v28.7 operational workflow expansion

- Added duplicate-candidate review storage and reviewer APIs.
- Added event external ID table for source-specific record identifiers.
- Added data-quality issue storage and crew/passenger split consistency warnings.
- Added retention archive CLI automation backed by archive manifest rows.
- Added database-backed Prometheus gauges for ingestion freshness, conflict backlog, duplicate candidates, and data-quality issues.
- Added ingestion performance timing script for generated large-data fixtures.

## v28.7 production feature pass

- Added operator/reviewer UI pages for duplicates, data-quality issues, archive manifests, audit log, ingestion runs, source freshness, API-key handling, and source-document review.
- Added admin API key CRUD endpoints and source-document final-report review endpoint.
- Added reversible duplicate merge operation audit table and undo endpoint for newly confirmed duplicate merges.
- Added ASN CSV adapter/CLI for licensed second-source ingestion.
- Added structured search filters, public transparency endpoint, data export endpoints, archive integrity verification, monitoring-contract script, and map EXPLAIN performance helper.
- Added Playwright browser workflow smoke test skeleton.
