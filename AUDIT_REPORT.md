| Task | Files | Complexity | Status |
|------|-------|------------|--------|
| Structured JSON logging | `logging_config.py` | Low | ✅ Implemented (`JsonLogFormatter`) |
| Backup/restore runbook + automated testing | `ops/runbooks/backup-restore.md`, CI | Medium | ✅ Runbook at `ops/runbooks/backup-restore.md` |
| Zero-downtime migration procedure | `OPERATIONS.md` | Low | ✅ Documented in `OPERATIONS.md` (rolling Alembic upgrade, blue/green notes) |
| Production deployment checklist | `ops/runbooks/production-cutover-checklist.md` | Low | ✅ Full checklist at `ops/runbooks/production-cutover-checklist.md` |
| Alerting rules + runbooks | `ops/alerts/prometheus-atlas-rules.yml`, `ops/runbooks/` | Medium | ✅ Alert rules at `ops/alerts/prometheus-atlas-rules.yml`; supplementary runbooks in `ops/runbooks/` |
| OpenTelemetry tracing | `src/atlas/presentation/api/app.py` (`_configure_otel`) | Medium | ✅ Implemented — opt-in via `OTEL_EXPORTER_OTLP_ENDPOINT`; instruments FastAPI + SQLAlchemy |
| Dependency vulnerability scanning in CI | `.github/workflows/ci.yml` | Low | ✅ Trivy filesystem + image scans; Gitleaks secret scan on every push |
| Multi-process rate limiting | `src/atlas/presentation/api/middleware.py` | Medium | ⚠️ In-memory only — per-process, not shared across Gunicorn workers. Replace with Redis-backed limiter before high-traffic production deploy. |
| Repository hygiene — artifacts, `.gitignore` | `atlas-argus/.gitignore`, `bff-extracted/bff/.gitignore` | Low | ✅ Fixed (2026-06-09): `.venv/`, `data/`, `.codex/`, `uv.lock` added to `atlas-argus/.gitignore`; BFF `.gitignore` created covering `dist/`, `*.db*`, `tsconfig.tsbuildinfo`, `seed-now.ts` |
| Hardcoded version strings in `app.py` | `src/atlas/presentation/api/app.py` | Low | ✅ Fixed (2026-06-09): both `version=` and `service.version=` now read from `importlib.metadata.version("atlas-backend")` |