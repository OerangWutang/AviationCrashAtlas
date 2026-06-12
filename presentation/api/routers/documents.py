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
* Request bodies are bounded before parsing so oversized uploads are rejected.
* Parse failures are stored, not raised.  The UI can show the failure and let
  a reviewer try again or flag for OCR.
* Object storage: MVP stores extracted text in the DB row and reserves the
  object_key column for a real S3 integration. Files are not stored long-term in
  this MVP; the extracted text and verifiable content hash are what matter.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select, text as sa_text

from atlas.application.dto import CurrentUser
from atlas.application.unit_of_work import UnitOfWork
from atlas.domain.entities import Source
from atlas.domain.enums import Role, SourceKind
from atlas.infrastructure.db.orm_models import UploadedDocumentModel
from atlas.infrastructure.ingestion.pdf_reader import read_pdf
from atlas.presentation.api.dependencies import get_uow, require_role
from atlas.presentation.api.schemas.documents import (
    DocumentIngestResponse,
    DocumentListItem,
    DocumentListResponse,
    DocumentReceiptResponse,
    DocumentTextResponse,
    DocumentUploadResponse,
    DocumentVerifyResponse,
)
from atlas.security import sign_evidence_hash

router = APIRouter(prefix="/documents", tags=["documents"])
logger = logging.getLogger(__name__)

_READERS = (Role.ADMIN, Role.REVIEWER, Role.ANALYST)
_WRITERS = (Role.ADMIN, Role.REVIEWER)

# Conservative file size limit: 50 MB.
_MAX_FILE_BYTES = 50 * 1024 * 1024
_MAX_FILENAME_LENGTH = 255
_SAFE_FILENAME_RE = re.compile(r"[^\w.\-]")


class _RequestUploadTooLarge(Exception):
    pass


def _sanitize_filename(raw: str | None) -> str:
    """Return a safe filename derived from the upload's reported filename."""
    if not raw:
        return "document.pdf"
    name = raw.replace("\\", "/").rsplit("/", 1)[-1]
    name = name.replace("\x00", "").replace("\r", "").replace("\n", "")
    name = _SAFE_FILENAME_RE.sub("_", name)
    name = name[:_MAX_FILENAME_LENGTH]
    return name or "document.pdf"


async def _read_upload_bounded(file: UploadFile) -> bytes:
    """Read an upload with a hard byte cap, independent of Content-Length."""
    if file.size and file.size > _MAX_FILE_BYTES:
        raise _RequestUploadTooLarge

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > _MAX_FILE_BYTES:
            raise _RequestUploadTooLarge
        chunks.append(chunk)
    return b"".join(chunks)


# Source name used when no explicit source is provided.  The BFF is expected
to resolve-or-create a proper source before calling; this is the fallback.
_DEFAULT_SOURCE_NAME = "Uploaded Document"
_DEFAULT_RELIABILITY_TIER = 3  # User-uploaded; lower trust than official records


async def _resolve_or_create_source(
    uow: UnitOfWork,
    source_name: str = _DEFAULT_SOURCE_NAME,
) -> Source:
    """Get or create a Source for uploaded documents without committing early."""
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
    """Upload a PDF/docket file, extract text, and store document metadata."""
    try:
        content = await _read_upload_bounded(file)
    except _RequestUploadTooLarge:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum is {_MAX_FILE_BYTES // (1024 * 1024)} MB.",
        ) from None

    if not content.startswith(b"%PDF"):
        raise HTTPException(
            status_code=422,
            detail="File does not appear to be a valid PDF (missing %PDF header).",
        )

    filename = _sanitize_filename(file.filename)
    content_sha256 = hashlib.sha256(content).hexdigest()
    evidence_signature = sign_evidence_hash(content_sha256)
    size_bytes = len(content)

    read_result = read_pdf(content)
    effective_source_name = source_name or _DEFAULT_SOURCE_NAME
    source = await _resolve_or_create_source(uow, effective_source_name)

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
        parse_note=read_result.parse_note or None,
        extracted_text=read_result.text or None,
        object_key=None,
        uploaded_by=current_user.user_id,
    )

    session = uow._session  # type: ignore[attr-defined]
    session.add(doc)
    await session.execute(
        sa_text(
            """INSERT INTO compliance_events
               (id, entity_type, entity_id, action, reason, actor_type, actor_id)
               VALUES (gen_random_uuid(), 'uploaded_document', :eid, 'UPLOADED', :reason, 'USER', :actor)"""
        ),
        {
            "eid": str(doc_id),
            "reason": f"Uploaded {filename} ({size_bytes} bytes)",
            "actor": str(current_user.user_id),
        },
    )
    await uow.commit()

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
    """Return extracted plain text for review before ingestion."""
    session = uow._session  # type: ignore[attr-defined]
    result = await session.execute(
        select(UploadedDocumentModel).where(UploadedDocumentModel.id == document_id)
    )
    doc = result.scalar_one_or_none()
    await uow.rollback()

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    return DocumentTextResponse(
        document_id=doc.id,
        filename=doc.filename,
        parse_status=doc.parse_status,
        text=doc.extracted_text,
        parse_note=doc.parse_note,
    )


@router.post("/{document_id}/ingest", response_model=DocumentIngestResponse)
async def ingest_document(
    document_id: UUID,
    event_id: UUID | None = Query(default=None),
    uow: UnitOfWork = Depends(get_uow, scope="function"),
    current_user: CurrentUser = Depends(require_role(*_WRITERS)),
) -> DocumentIngestResponse:
    """Trigger ingestion of an already-uploaded, successfully-parsed document.

    The MVP persists extracted text for review, but it does not yet include a
    document-to-claim mapper.  Keep this endpoint explicit so callers get a
    stable contract while the mapper is added.
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

    if doc.parse_status == "ingested":
        await uow.rollback()
        return DocumentIngestResponse(
            document_id=doc.id,
            filename=doc.filename,
            parse_status=doc.parse_status,
            ingestion_result=None,
            error="Document has already been ingested.",
        )

    if not doc.extracted_text:
        raise HTTPException(
            status_code=422,
            detail="Document has no extracted text to ingest.",
        )

    doc.event_id = event_id or doc.event_id
    doc.parse_status = "ingest_failed"
    await uow.commit()
    return DocumentIngestResponse(
        document_id=doc.id,
        filename=doc.filename,
        parse_status=doc.parse_status,
        ingestion_result=None,
        error="Document-to-claim mapping is not implemented yet. Review extracted text via GET /documents/{id}/text.",
    )


@router.get("/{document_id}/receipt", response_model=DocumentReceiptResponse)
async def get_document_receipt(
    document_id: UUID,
    uow: UnitOfWork = Depends(get_uow, scope="function"),
    _user: CurrentUser = Depends(require_role(*_READERS)),
) -> DocumentReceiptResponse:
    """Return the verifiable evidence receipt for an uploaded document."""
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
    """Verify that a document's stored content hash matches a claimed evidence signature."""
    session = uow._session  # type: ignore[attr-defined]
    result = await session.execute(
        select(UploadedDocumentModel).where(UploadedDocumentModel.id == document_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    recomputed = sign_evidence_hash(doc.content_sha256)
    signature_valid = secrets.compare_digest(recomputed, claimed_signature)

    await uow.rollback()
    return DocumentVerifyResponse(
        document_id=doc.id,
        filename=doc.filename,
        content_sha256=doc.content_sha256,
        signature_valid=signature_valid,
    )
