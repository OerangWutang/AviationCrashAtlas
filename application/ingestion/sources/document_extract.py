"""PDF/docket document → Atlas ingestion mapping.

This module is the **pure, side-effect-free core** of the document ingestion
adapter.  It mirrors the NTSB eADMS importer's architecture exactly:

* No I/O here.  Reading PDF bytes and calling the use case live in
  ``infrastructure`` and the router respectively.
* Conservative extraction strategy.  Legal users live in PDFs, but wrong
  auto-extracted claims are worse than no claims.  MVP = preserve the full
  raw text, create at most a handful of high-confidence claims from obviously
  structured fields, and leave everything else for manual claim creation.
* Raw preservation.  The full extracted text is stored verbatim in
  ``raw_payload`` so nothing is lost and the audit chain is defensible.
* Epistemic framing.  Extracted claims are RAW — they carry no synthetic
  probability and make no causal assertions.  The reliability tier is
  caller-determined (typically lower than tier-1 official records).

Design notes
------------
* ``DocumentExtractResult`` is the output contract.  It maps directly to the
  ``IngestionRequest`` the use case already expects — callers only need to
  supply a ``source_id`` and POST to ``/ingestion/sources/{source_id}``.
* Idempotency key is SHA-256 of ``(source_id_str + content_sha256)`` so
  re-uploading the same file is deterministic.
* The ``claims`` list is intentionally short.  Do not add AI-based extraction
  here; any LLM layer must sit above this with a separate grounding guard.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from atlas.application.dto import IngestionClaimDTO

# ── Output contract ───────────────────────────────────────────────────────────

@dataclass
class DocumentExtractResult:
    """Extracted content ready for submission to IngestSourceData.

    ``raw_payload`` preserves the full text for audit.  ``claims`` contains
    only high-confidence, clearly-structured fields.  Everything else stays in
    raw_payload for manual claim creation by reviewers.
    """

    raw_payload: dict[str, Any]
    claims: list[IngestionClaimDTO]
    idempotency_key: str
    source_record_id: str | None = None
    captured_at: datetime = field(default_factory=lambda: datetime.now(UTC))


# ── Conservative field extractors ────────────────────────────────────────────
#
# Each extractor is a simple regex over the full document text.  They are
# intentionally narrow: only match when the field label is explicit and
# unambiguous.  False positives in a litigation tool are worse than gaps.

# NTSB accident number format: LAX12FA123, DFW20MA001, etc.
_NTSB_ACCESSION_RE = re.compile(
    r"\b([A-Z]{2,3}\d{2}[A-Z]{2}\d{3,4}[A-Z]?)\b"
)

# ISO date or common US date formats following explicit labels
_DATE_LABEL_RE = re.compile(
    r"""(?:date|occurred?|accident\s+date|incident\s+date)[:\s]+
        (\d{1,2}[/-]\d{1,2}[/-]\d{2,4}
        |\d{4}-\d{2}-\d{2}
        |(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4})
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Airport ICAO/IATA code following explicit label
_AIRPORT_RE = re.compile(
    r"""(?:airport|aerodrome|near|vicinity\s+of)[:\s]+
        ([A-Z]{3,4})
        (?:\s*[-(/]?\s*([A-Z]{3,4}))?
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Registration/tail number following explicit label
_REGISTRATION_RE = re.compile(
    r"""(?:registration|tail\s+number|aircraft\s+reg(?:istration)?)[:\s]+
        ([A-Z0-9]{2}-[A-Z0-9]{2,6}|N[0-9]{1,5}[A-Z]{0,2})
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _extract_high_confidence_claims(text: str) -> list[IngestionClaimDTO]:
    """Return only the claims we are confident about from plain document text.

    Deliberately narrow.  Each claim emitted here must have been clearly and
    unambiguously stated in the source document.  When in doubt, omit.
    """
    claims: list[IngestionClaimDTO] = []

    # NTSB accession number — highly structured, unambiguous
    ntsb_match = _NTSB_ACCESSION_RE.search(text)
    if ntsb_match:
        claims.append(
            IngestionClaimDTO(
                field_name="ntsb_accession_number",
                field_value=ntsb_match.group(1).upper(),
            )
        )

    # Occurrence date — only when an explicit label is present
    date_match = _DATE_LABEL_RE.search(text)
    if date_match:
        raw_date = date_match.group(1).strip()
        claims.append(
            IngestionClaimDTO(
                field_name="event_date_raw",
                field_value=raw_date,
            )
        )

    # Aircraft registration
    reg_match = _REGISTRATION_RE.search(text)
    if reg_match:
        claims.append(
            IngestionClaimDTO(
                field_name="registration",
                field_value=reg_match.group(1).upper(),
            )
        )

    return claims


# ── Public API ────────────────────────────────────────────────────────────────

def build_extract_result(
    *,
    text: str,
    filename: str,
    content_sha256: str,
    source_id_str: str,
    page_count: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> DocumentExtractResult:
    """Convert extracted document text into an ingestion submission.

    Parameters
    ----------
    text:
        Plain text extracted from the PDF by ``pdf_reader.py``.
    filename:
        Original filename — preserved in raw_payload for audit.
    content_sha256:
        SHA-256 hex digest of the raw file bytes.
    source_id_str:
        String form of the Atlas Source UUID — used to derive a stable
        idempotency key so the same file can be safely re-ingested.
    page_count:
        Page count from the PDF reader; stored in raw_payload.
    metadata:
        Any additional metadata from the PDF reader (author, title, etc.).
    """
    raw_payload: dict[str, Any] = {
        "filename": filename,
        "content_sha256": content_sha256,
        "page_count": page_count,
        "extracted_text": text,
        "metadata": metadata or {},
        "extraction_strategy": "pypdf_conservative_v1",
    }

    claims = _extract_high_confidence_claims(text)

    # Idempotency key: deterministic for (source, file content).
    # Same file re-uploaded → same key → idempotent replay.
    idempotency_key = hashlib.sha256(
        f"{source_id_str}:{content_sha256}".encode()
    ).hexdigest()

    # Source record ID: use NTSB accession number when found, else the
    # content hash (stable identifier for this exact document version).
    ntsb_claim = next(
        (c for c in claims if c.field_name == "ntsb_accession_number"), None
    )
    source_record_id = (
        str(ntsb_claim.field_value) if ntsb_claim else f"sha256:{content_sha256[:16]}"
    )

    return DocumentExtractResult(
        raw_payload=raw_payload,
        claims=claims,
        idempotency_key=idempotency_key,
        source_record_id=source_record_id,
    )
