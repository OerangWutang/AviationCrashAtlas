"""
Source-document extractor for NTSB raw records.

Conservative on purpose: this module only creates SourceDocument candidates
from URL-shaped values that actually appear in the raw payload, plus a
small set of deterministic NTSB CAROL search URLs derived from a
known-good EventId.  It does NOT fabricate per-record investigation page
URLs that may or may not exist on ntsb.gov, because a polished UI over a
broken URL is exactly the false-authority pattern the v20 prompt warns
about.

Each candidate carries:
  - document_type   (e.g. "investigation_page", "docket", "preliminary",
                     "final", "probable_cause", "report_pdf", "external_link")
  - url             (verbatim from source or deterministic CAROL pattern)
  - title           (best-effort short label)

The URL is NOT verified here.  url_verified is left False; the
`atlas check-links` command (already implemented) is what actually
performs HTTP HEAD/GET and updates url_verified, is_available, and the
verification metadata columns.

Returns an empty list when the raw record contains nothing usable, which
the v20 frontend correctly displays as "Documents: None linked".
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

# Permissive http(s) URL detector — we only use this to *find* URLs in
# raw payload values, not to validate them.  Verification is a separate
# concern handled by `atlas check-links`.
_URL_RE = re.compile(r"^https?://[^\s'\"<>]+$")


@dataclass(frozen=True)
class DocumentCandidate:
    document_type: str
    url: str
    title: str | None
    published_at: str | None = None  # ISO date string when known


# Mapping from the NTSB CSV / API field names that occasionally carry
# URLs to a stable document_type.  These are conservative — we only use
# fields that NTSB uses for genuine document links.  Any unmapped URL
# in the raw payload becomes "external_link" so it still appears, but
# without a misleading specific type.
_KNOWN_URL_FIELDS: dict[str, str] = {
    "ReportUrl": "investigation_page",
    "ReportURL": "investigation_page",
    "DocketUrl": "docket",
    "DocketURL": "docket",
    "PreliminaryReportUrl": "preliminary",
    "FinalReportUrl": "final",
    "ProbableCauseUrl": "probable_cause",
    "ReportPdfUrl": "report_pdf",
    "ReportPDFUrl": "report_pdf",
    "Url": "external_link",
    "URL": "external_link",
}


def extract_documents_from_ntsb(
    raw: dict[str, Any],
) -> list[DocumentCandidate]:
    """
    Return a list of DocumentCandidate for the given NTSB raw record.

    Rules:
      1. Walk known URL fields first; emit DocumentCandidate only when the
         value parses as an http(s) URL.
      2. Then walk all other fields and emit "external_link" candidates
         for any unmapped URL-shaped string.
      3. If we have a non-empty EventId, also emit a deterministic CAROL
         search URL.  This pattern is stable — it points at the CAROL
         public search UI scoped to that EventId — and unlike per-record
         investigation page URLs it does not 404 when an investigation
         is incomplete.

    Duplicate URLs (case-insensitive) are de-duplicated, with the first
    occurrence's document_type and title kept.
    """
    candidates: list[DocumentCandidate] = []
    seen_urls: set[str] = set()

    def _maybe_add(doc_type: str, url: str, title: str | None) -> None:
        u = url.strip()
        if not _URL_RE.match(u):
            return
        key = u.lower()
        if key in seen_urls:
            return
        seen_urls.add(key)
        candidates.append(DocumentCandidate(
            document_type=doc_type,
            url=u,
            title=title,
        ))

    # Pass 1 — known URL fields with stable document types
    for key, doc_type in _KNOWN_URL_FIELDS.items():
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            title_field = raw.get(f"{key}Title") or raw.get(f"{key}_title")
            title = (
                title_field.strip()
                if isinstance(title_field, str) and title_field.strip()
                else None
            )
            _maybe_add(doc_type, val, title)

    # Pass 2 — any other URL-shaped value not already captured
    for key, val in raw.items():
        if key in _KNOWN_URL_FIELDS:
            continue
        if isinstance(val, str) and _URL_RE.match(val.strip()):
            _maybe_add("external_link", val, None)

    # Pass 3 — deterministic CAROL search URL for known EventIds.
    # The CAROL public-search URL pattern is stable and resolves to a
    # search results page scoped to a single accident.  It is deliberately
    # the *search* URL, not a fabricated per-event detail URL — the
    # detail URL pattern depends on the report number and varies between
    # preliminary and final stages, and we should not invent it.
    event_id = (raw.get("EventId") or "").strip()
    if event_id:
        carol_url = (
            f"https://data.ntsb.gov/carol-main-public/basic-search?"
            f"EventID={event_id}"
        )
        _maybe_add(
            doc_type="investigation_page",
            url=carol_url,
            title=f"NTSB CAROL search: {event_id}",
        )

    return candidates


def deduplicate(
    candidates: Iterable[DocumentCandidate],
) -> list[DocumentCandidate]:
    """Public alias for callers that want to merge candidates from
    multiple sources before persistence."""
    seen: set[str] = set()
    out: list[DocumentCandidate] = []
    for c in candidates:
        key = c.url.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out
