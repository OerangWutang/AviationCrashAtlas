"""ASN ingestion adapter.

Atlas deliberately does not scrape Aviation Safety Network. Operators must
provide a licensed/authorized ASN CSV export and ingest it through this adapter.
The adapter is source-specific (not a generic test fixture): it pins the bundled
ASN mapping, preserves ASN external IDs via the generic pipeline, and lets the
cross-source matcher attach rows to existing NTSB events when evidence is strong.
"""
from __future__ import annotations

from pathlib import Path

from atlas.ingestion.generic_csv_adapter import load_bundled_mapping
from atlas.ingestion.pipeline import IngestionPipeline, IngestionResult


class ASNCSVAdapter:
    source_name = "ASN"

    def __init__(self, pipeline: IngestionPipeline | None = None) -> None:
        self.pipeline = pipeline or IngestionPipeline()
        self.mapping = load_bundled_mapping("asn")

    async def ingest_csv(self, path: str | Path, *, dry_run: bool = False) -> IngestionResult:
        return await self.pipeline.run_generic_csv(str(path), self.mapping, dry_run=dry_run)
