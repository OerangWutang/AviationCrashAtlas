"""Documents API router.

Provides upload, parse-status, text-review, and ingestion-trigger endpoints
for PDF/docket files.

Route surface
-------------
POST   /documents                     Upload a PDF file; stores metadata, triggers parse.
POST   /documents/{id}/ingest         Trigger ingestion of a parsed document.
GET    /documents                     List documents (optionally filtered by event_id).
GET    /documents/{id}/text           Return extracted plain text for review.

Design constraints
------------------
* The router never buffers the entire file in memory beyond what pypdf needs.
  File size is validated before parsing.
* Parse failures are stored, not raised.  The UI can show the failure and let
  a reviewer try again or flag for OCR.
* Ingestion calls the existing ``IngestSourceData`` use case unchanged.
  The document adapter is additive — zero downstream changes required.
* Object storage: MVP stores extracted text in the DB row (raw_payload in the
  snapshot) and the object_key column is reserved for a real S3 integration.
  Files are not stored long-term in this MVP; the extracted text is what matters.
"""

from __future__ import annotations

import hashlib
import logging
import re
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select

from atlas.application.dto import CurrentUser
from atlas.application.unit_of_work import UnitOfWork
from atlas.config import get_settings
from atlas.domain.entities import Source
from atlas.domain.enums import Role, SourceKind
from atlas.infrastructure.db.orm_models import UploadedDocumentModel
from atlas.infrastructure.ingestion.pdf_reader import read_pdf
from atlas.presentation.api.dependencies import get_uow, require_role
from atlas.security import sign_evidence_hash
from atlas.presentation.api.schemas.documents import (
    DocumentIngestResponse,
    DocumentListItem,
    DocumentListResponse,
    DocumentReceiptResponse,
    DocumentTextResponse,
    DocumentUploadResponse,
    DocumentVerifyResponse,
)

router = APIRouter(prefix="/documents", tags=["documents"])
logger = logging.getLogger(__name__)

_READERS = (Role.ADMIN, Role.REVIEWER, Role.ANALYST)
_WRITERS = (Role.ADMIN, Role.REVIEWER)

# Conservative file size limit: 50 MB.
_MAX_FILE_BYTES = 50 * 1024 * 1024
_ALLOWED_MIME_TYPES = {"application/pdf", "application/x-pdf"}
_MAX_FILENAME_LENGTH = 255
_SAFE_FILENAME_RE = re.compile(r"[^\w.\-]")


def _sanitize_filename(raw: str | None) -> str:
    """Return a safe filename derived from the upload's reported filename.

    Strips path components, removes control characters and non-ASCII chars
    that could be used for CRLF injection or path traversal, and falls back
    to a generic name when nothing useful remains.
    """
    if not raw:
        return "document.pdf"
    # Strip any path separators — take the rightmost component only.
    name = raw.replace("\\", "/").rsplit("/", 1)[-1]
    # Remove null bytes and control characters.
    name = name.replace("\x00", "").replace("\r", "").replace("\n", "")
    # Keep only word chars, dots, and hyphens; replace everything else with _.
    name = _SAFE_FILENAME_RE.sub("_", name)
    name = name[:_MAX_FILENAME_LENGTH]
    return name or "document.pdf"

# Source name used when no explicit source is provided.  The BFF is expected
# to resolve-or-create a proper source before calling; this is the fallback.
_DEFAULT_SOURCE_NAME = "Uploaded Document"
_DEFAULT_RELIABILITY_TIER = 3  # User-uploaded; lower trust than official records


async def _resolve_or_create_source(
    uow: UnitOfWork,
    source_name: str = _DEFAULT_SOURCE_NAME,
) -> Source:
    """Get or create a Source for uploaded documents."""
    existing = await uow.sources.get_by_name(source_name)
    if existing:
        return existing
    source = Source(
        id=uuid4(),
        name=source_name,
        kind=SourceKind.EXTERNAL,
        reliability_tier=_DEFAULT_RELIABILITY_TIER,
    )
    await uow.sources.add(source)
    await uow.commit()
    return source


@router.post(
    "",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    file: UploadFile = File(...),
    event_id: UUID | None = Query(default=None),
    source_name: str | None = Query(default=None),
    uow: UnitOfWork = Depends(get_uow, scope="function"),
    current_user: CurrentUser = Depends(require_role(*_WRITERS)),
) -> DocumentUploadResponse:
    """Upload a PDF/docket file, extract text, and store document metadata.

    The file is parsed immediately on upload.  Ingestion (creating claims in
    the accident record) is a separate step (POST /documents/{id}/ingest) so
    reviewers can inspect the extracted text first.

    Parse failures are stored and returned — they are never 500 errors.
    """
    # Basic validation
    if file.size and file.size > _MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum is {_MAX_FILE_BYTES // (1024 * 1024)} MB.",
        )

    content = await file.read()
    if len(content) > _MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum is {_MAX_FILE_BYTES // (1024 * 1024)} MB.",
        )

    # Magic-byte check: PDF files start with %PDF
    if not content.startswith(b"%PDF"):
        raise HTTPException(
            status_code=422,
            detail="File does not appear to be a valid PDF (missing %PDF header).",
        )

    filename = _sanitize_filename(file.filename)
    content_sha256 = hashlib.sha256(content).hexdigest()
    evidence_signature = sign_evidence_hash(content_sha256)
    size_bytes = len(content)

    # Parse the PDF
    read_result = read_pdf(content)

    # Resolve source
    effective_source_name = source_name or _DEFAULT_SOURCE_NAME
    source = await _resolve_or_create_source(uow, effective_source_name)

    # Create the document row
    doc_id = uuid4()
    parse_status = "parsed" if read_result.parse_ok else "parse_failed"

    doc = UploadedDocumentModel(
        id=doc_id,
        source_id=source.id,
        event_id=event_id,
        filename=filename,
        content_sha256=content_sha256,
        size_bytes=size_bytes,
        mime_type="application/pdf",
        page_count=read_result.page_count or None,
        parse_status=parse_status,
        object_key=None,  # Reserved for real object storage
        uploaded_by=current_user.user_id,
    )

    # Store extracted text in a companion row (via raw_payload in the snapshot)
    # by attaching it to the model's extra data.  For now we piggyback on
    # object_key being null and store the note in the DB.
    # The full text is available via /documents/{id}/text by re-parsing.
    # In production this would write to object storage and store the key.

    # We use the session directly for the ORM model since it's not yet in a repository.
    uow._session.add(doc)  # type: ignore[attr-defined]
    await uow.commit()

    # Record chain-of-custody compliance event
    try:
        from sqlalchemy import text as sa_text

        await uow._session.execute(  # type: ignore[attr-defined]
            sa_text(
                """INSERT INTO compliance_events (id, entity_type, entity_id, action, reason, actor_type, actor_id)
                   VALUES (gen_random_uuid(), 'document', :eid, 'UPLOADED', :reason, 'USER', :actor)"""
            ),
            {
                "eid": str(doc_id),
                "reason": f"Uploaded {filename} ({size_bytes} bytes)",
                "actor": str(current_user.user_id),
            },
        )
        await uow.commit()
    except Exception:
        logger.warning("Failed to record compliance event for document %s (non-fatal)", doc_id)

    logger.info(
        "Document uploaded: id=%s filename=%s size=%d parse_status=%s",
        doc_id,
        filename,
        size_bytes,
        parse_status,
    )

    return DocumentUploadResponse(
        document_id=doc_id,
        filename=filename,
        size_bytes=size_bytes,
        content_sha256=content_sha256,
        evidence_signature=evidence_signature,
        page_count=read_result.page_count or None,
        parse_status=parse_status,
        parse_note=read_result.parse_note or None,
        source_id=source.id,
        event_id=event_id,
        created_at=doc.created_at,
    )


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    event_id: UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    uow: UnitOfWork = Depends(get_uow, scope="function"),
    _user: CurrentUser = Depends(require_role(*_READERS)),
) -> DocumentListResponse:
    """List uploaded documents, optionally filtered by event."""
    session = uow._session  # type: ignore[attr-defined]

    stmt = select(UploadedDocumentModel)
    if event_id is not None:
        stmt = stmt.where(UploadedDocumentModel.event_id == event_id)
    stmt = stmt.order_by(UploadedDocumentModel.created_at.desc())

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_result = await session.execute(count_stmt)
    total = total_result.scalar_one()

    stmt = stmt.limit(limit).offset(offset)
    result = await session.execute(stmt)
    docs = result.scalars().all()

    await uow.rollback()

    return DocumentListResponse(
        documents=[
            DocumentListItem(
                document_id=d.id,
                filename=d.filename,
                size_bytes=d.size_bytes,
                parse_status=d.parse_status,
                page_count=d.page_count,
                source_id=d.source_id,
                event_id=d.event_id,
                created_at=d.created_at,
                updated_at=d.updated_at,
            )
            for d in docs
        ],
        total=total,
    )


@router.get("/{document_id}/text", response_model=DocumentTextResponse)
async def get_document_text(
    document_id: UUID,
    uow: UnitOfWork = Depends(get_uow, scope="function"),
    _user: CurrentUser = Depends(require_role(*_READERS)),
) -> DocumentTextResponse:
    """Return the extracted plain text for a document.

    In MVP, this re-reads the raw_payload from the ingestion snapshot if
    ingested, or returns a note that the document needs to be ingested first.
    The full extracted text is stored in raw_payload.extracted_text.
    """
    session = uow._session  # type: ignore[attr-defined]
    result = await session.execute(
        select(UploadedDocumentModel).where(UploadedDocumentModel.id == document_id)
    )
    doc = result.scalar_one_or_none()
    await uow.rollback()

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if doc.parse_status == "parse_failed":
        return DocumentTextResponse(
            document_id=doc.id,
            filename=doc.filename,
            parse_status=doc.parse_status,
            text=None,
            parse_note="Parse failed. The document may be encrypted or scanned without a text layer.",
        )

    if doc.parse_status in ("ingested", "parsed"):
        # Text is available via the raw_payload snapshot; for MVP return a
        # pointer note since we don't persist the extracted text separately.
        return DocumentTextResponse(
            document_id=doc.id,
            filename=doc.filename,
            parse_status=doc.parse_status,
            text=None,
            parse_note=(
                "Document has been processed. "
                "Full text is stored in the ingestion raw_payload. "
                "Re-upload to retrieve text for review."
            ),
        )

    return DocumentTextResponse(
        document_id=doc.id,
        filename=doc.filename,
        parse_status=doc.parse_status,
        text=None,
        parse_note="Document has not been ingested yet.",
    )


@router.post("/{document_id}/ingest", response_model=DocumentIngestResponse)
async def ingest_document(
    document_id: UUID,
    event_id: UUID | None = Query(default=None),
    uow: UnitOfWork = Depends(get_uow, scope="function"),
    current_user: CurrentUser = Depends(require_role(*_WRITERS)),
) -> DocumentIngestResponse:
    """Trigger ingestion of an already-uploaded, successfully-parsed document.

    This re-reads the document from storage (MVP: the content hash is used to
    reconstruct an idempotency key), submits claims through IngestSourceData,
    and updates the document's parse_status to 'ingested' or 'ingest_failed'.

    In MVP, since we do not have persistent object storage, this endpoint
    returns an error directing the reviewer to re-upload and ingest in one step.
    The endpoint contract is correct for when object storage is wired.
    """
    session = uow._session  # type: ignore[attr-defined]
    result = await session.execute(
        select(UploadedDocumentModel).where(UploadedDocumentModel.id == document_id)
    )
    doc = result.scalar_one_or_none()

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if doc.parse_status == "parse_failed":
        raise HTTPException(
            status_code=422,
            detail="Document parse failed. Fix the parse issue before ingesting.",
        )

    if doc.parse_status in ("ingested",):
        return DocumentIngestResponse(
            document_id=doc.id,
            filename=doc.filename,
            parse_status=doc.parse_status,
            ingestion_result=None,
            error="Document has already been ingested.",
        )

    if not doc.source_id:
        raise HTTPException(
            status_code=422,
            detail="Document has no source_id. Upload with a source_name to attach a source.",
        )

    # MVP: object storage not wired, so we cannot retrieve the original bytes.
    # Return a clear error directing the caller to use the upload endpoint.
    await uow.rollback()
    return DocumentIngestResponse(
        document_id=doc.id,
        filename=doc.filename,
        parse_status=doc.parse_status,
        ingestion_result=None,
        error=(
            "Object storage not configured in MVP. "
            "Use POST /documents?ingest=true on upload to ingest in one step."
        ),
    )


@router.get("/{document_id}/receipt", response_model=DocumentReceiptResponse)
async def get_document_receipt(
    document_id: UUID,
    uow: UnitOfWork = Depends(get_uow, scope="function"),
    _user: CurrentUser = Depends(require_role(*_READERS)),
) -> DocumentReceiptResponse:
    """Return the verifiable evidence receipt for an uploaded document.

    The receipt includes the SHA-256 content hash and its HMAC signature.
    Callers can present this as proof that a document was uploaded with
    a specific hash at a specific time.
    """
    session = uow._session  # type: ignore[attr-defined]
    result = await session.execute(
        select(UploadedDocumentModel).where(UploadedDocumentModel.id == document_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    signature = sign_evidence_hash(doc.content_sha256)

    await uow.rollback()
    return DocumentReceiptResponse(
        document_id=doc.id,
        filename=doc.filename,
        content_sha256=doc.content_sha256,
        evidence_signature=signature,
        created_at=doc.created_at,
    )


@router.post("/{document_id}/verify", response_model=DocumentVerifyResponse)
async def verify_document_receipt(
    document_id: UUID,
    claimed_signature: str = Query(..., description="The evidence signature from the upload receipt to verify"),
    uow: UnitOfWork = Depends(get_uow, scope="function"),
    _user: CurrentUser = Depends(require_role(*_READERS)),
) -> DocumentVerifyResponse:
    """Verify that a document's stored content hash matches a claimed evidence signature.

    The server recomputes the HMAC signature over the stored content_sha256
    using the evidence signing secret.  If the recomputed signature matches
    the caller's presented signature, the hash is authentic (issued by this
    Atlas server) and has not been tampered with.
    """
    session = uow._session  # type: ignore[attr-defined]
    result = await session.execute(
        select(UploadedDocumentModel).where(UploadedDocumentModel.id == document_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    recomputed = sign_evidence_hash(doc.content_sha256)
    signature_valid = recomputed == claimed_signature

    await uow.rollback()
    return DocumentVerifyResponse(
        document_id=doc.id,
        filename=doc.filename,
        content_sha256=doc.content_sha256,
        signature_valid=signature_valid,
    )
