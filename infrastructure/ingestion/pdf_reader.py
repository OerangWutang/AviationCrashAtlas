"""PDF infrastructure reader — the only code that touches pypdf.

Responsibilities (all the *impure* parts the mapping core deliberately avoids):

1. Accept raw PDF bytes.
2. Extract plain text page-by-page using pypdf.
3. Extract document-level metadata (author, title, subject, creator).
4. Return a ``PdfReadResult`` with the combined text, page count, and metadata.
5. Handle failures gracefully: encrypted PDFs, scanned PDFs with no text layer,
   corrupt files.  Always return a result — never raise to the caller.  Parse
   failures are surfaced via ``parse_status`` on the result, not exceptions.

The design mirrors the NTSB infrastructure reader:
- This module is the single I/O boundary for PDF parsing.
- The application layer (``document_extract.py``) does the claim mapping.
- The router does the DB writes and object-storage operations.

No LLM, no OCR, no complex NLP here.  MVP = plain text extraction.  OCR
(ocrmypdf/Tesseract) is a later enhancement for scanned documents.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# pypdf is the only external dependency this module introduces.
# It is pure-Python and has no native extensions.
try:
    import pypdf
    _PYPDF_AVAILABLE = True
except ImportError:
    _PYPDF_AVAILABLE = False
    logger.warning(
        "pypdf is not installed; PDF text extraction will not be available. "
        "Install with: pip install pypdf"
    )


@dataclass
class PdfReadResult:
    """Output of reading one PDF file."""

    text: str
    """Combined plain text from all pages, joined by double newline."""

    page_count: int
    """Number of pages in the PDF.  0 if the file could not be opened."""

    metadata: dict[str, Any] = field(default_factory=dict)
    """Document-level metadata: author, title, subject, creator, etc."""

    parse_ok: bool = True
    """False when the PDF could not be fully parsed (encrypted, corrupt, etc.)."""

    parse_note: str = ""
    """Human-readable explanation when parse_ok is False."""


_METADATA_KEYS = ("/Author", "/Title", "/Subject", "/Creator", "/Producer", "/Keywords")
_MAX_PDF_PAGES = 500


def _extract_metadata(reader: pypdf.PdfReader) -> dict[str, Any]:
    """Extract document-level metadata, tolerating missing or malformed values."""
    meta: dict[str, Any] = {}
    try:
        info = reader.metadata
        if info is None:
            return meta
        for key in _METADATA_KEYS:
            val = info.get(key)
            if val and isinstance(val, str):
                clean_key = key.lstrip("/").lower()
                meta[clean_key] = val.strip()
    except Exception:
        pass
    return meta


def read_pdf(content: bytes) -> PdfReadResult:
    """Extract plain text and metadata from PDF bytes.

    Never raises.  All failures produce a ``PdfReadResult`` with
    ``parse_ok=False`` and a descriptive ``parse_note``.

    Parameters
    ----------
    content:
        Raw PDF file bytes.
    """
    if not _PYPDF_AVAILABLE:
        return PdfReadResult(
            text="",
            page_count=0,
            parse_ok=False,
            parse_note="pypdf is not installed; install it to enable PDF extraction.",
        )

    if not content:
        return PdfReadResult(
            text="",
            page_count=0,
            parse_ok=False,
            parse_note="Empty file.",
        )

    try:
        reader = pypdf.PdfReader(io.BytesIO(content))
    except pypdf.errors.PdfReadError as exc:
        return PdfReadResult(
            text="",
            page_count=0,
            parse_ok=False,
            parse_note=f"PDF parse error: {exc}",
        )
    except Exception as exc:
        logger.warning("Unexpected error opening PDF: %s", exc)
        return PdfReadResult(
            text="",
            page_count=0,
            parse_ok=False,
            parse_note=f"Unexpected error: {type(exc).__name__}",
        )

    # Encrypted PDFs: pypdf can open them but cannot extract text without the key.
    if reader.is_encrypted:
        try:
            decrypted = reader.decrypt("")
            if decrypted == pypdf.PasswordType.NOT_DECRYPTED:
                return PdfReadResult(
                    text="",
                    page_count=0,
                    parse_ok=False,
                    parse_note="PDF is password-protected; cannot extract text.",
                )
        except Exception:
            return PdfReadResult(
                text="",
                page_count=0,
                parse_ok=False,
                parse_note="PDF is encrypted and could not be decrypted.",
            )

    page_count = len(reader.pages)
    if page_count > _MAX_PDF_PAGES:
        return PdfReadResult(
            text="",
            page_count=page_count,
            parse_ok=False,
            parse_note=f"PDF has {page_count} pages which exceeds the {_MAX_PDF_PAGES}-page limit.",
        )
    metadata = _extract_metadata(reader)

    # Extract text page by page.  pypdf raises on pages with no text layer
    # (scanned-only); we catch per-page and continue so partial extraction
    # is better than nothing.
    page_texts: list[str] = []
    failed_pages: list[int] = []

    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
            page_texts.append(text)
        except Exception as exc:
            logger.debug("Page %d extraction failed: %s", i + 1, exc)
            failed_pages.append(i + 1)
            page_texts.append("")

    combined_text = "\n\n".join(t for t in page_texts if t.strip())

    if not combined_text.strip():
        # All pages had no text layer — likely a scanned document.
        return PdfReadResult(
            text="",
            page_count=page_count,
            metadata=metadata,
            parse_ok=False,
            parse_note=(
                "No text layer found in PDF.  This may be a scanned document "
                "requiring OCR (not yet supported in this version)."
            ),
        )

    parse_note = ""
    if failed_pages:
        parse_note = f"Pages with extraction errors (text layer absent): {failed_pages}"

    return PdfReadResult(
        text=combined_text,
        page_count=page_count,
        metadata=metadata,
        parse_ok=not failed_pages or bool(combined_text.strip()),
        parse_note=parse_note,
    )
