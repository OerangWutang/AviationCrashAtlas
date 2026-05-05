# ASN second-source integration

Atlas supports Aviation Safety Network (ASN) as the first non-NTSB source path via a **licensed local CSV export**.

Atlas does not scrape ASN. Operators must obtain data under terms that allow ingestion, then run:

```bash
PYTHONPATH=src atlas ingest asn-csv /secure/path/asn_export.csv --dry-run
PYTHONPATH=src atlas ingest asn-csv /secure/path/asn_export.csv
```

What this integration does:

- uses the bundled ASN field mapping (`src/atlas/ingestion/source_mappings/asn_mapping.json`)
- preserves source record IDs in `event_external_ids`
- runs the cross-source matcher against active projected events
- auto-attaches only high-confidence unambiguous matches
- creates `duplicate_candidates` for uncertain matches
- creates conflicts when ASN/NTSB claims disagree on the same event

Licensing checklist:

1. Confirm ASN export terms permit local processing in Atlas.
2. Record the license/permission in deployment documentation.
3. Keep raw export files in access-controlled storage.
4. Do not publish bulk ASN-derived payloads unless your license permits it.
5. Prefer public provenance links/short source labels over redistributing copyrighted text.

Validation checklist:

```bash
PYTHONPATH=src atlas ingest asn-csv sample.csv --dry-run
PYTHONPATH=src atlas ingest asn-csv sample.csv
PYTHONPATH=src atlas reproject
```

Then verify:

- `/api/v1/ops/source-status` shows ASN ingestion freshness
- `/api/v1/duplicates` contains uncertain matches
- overlapping NTSB/ASN records have one event where confidence is high
- conflicting fatality/aircraft/operator values create open conflicts
