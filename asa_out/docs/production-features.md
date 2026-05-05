# Production feature checklist

This build adds concrete implementation coverage for the production-readiness backlog:

- reviewer/operator UI: `/operator`, `/duplicates`, `/data-quality`, `/admin`
- admin API-key management UI/API
- duplicate merge review, confirm/reject, and reversible undo for new merge operations
- data-quality issue review and waiver/resolution flow
- archive manifest listing, export, restore, checksum/signature verification, and dry-run/execute CLI
- ASN second-source CSV adapter for licensed exports
- source freshness and ingestion-run inspection endpoints
- final-report source-document review endpoint and UI
- public transparency endpoint for open disputes, data-quality warnings, documents, and projection reasons
- accident/conflict/data-quality/provenance/archive export endpoints
- structured search filters for registration, aircraft type, operator, source, disputed-only, and verified-final-report-only records
- browser-level Playwright smoke test skeleton for operator workflows
- monitoring contract script to assert dashboard/alert metric families are really emitted
- map EXPLAIN script to prove lat/lon index behavior on generated dense fixtures

Still external/operator-dependent:

- real ASN/ICAO/BEA/AAIB data requires licensing and source-specific operational validation
- SSO/OIDC is not wired; API key management is implemented as the production baseline for now
- object storage can be used via mounted/synced archive output paths; native S3 upload should be added if required by deployment
