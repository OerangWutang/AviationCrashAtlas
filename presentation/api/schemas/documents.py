"""Request/response schemas for the documents API."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from typing import Any

from pydantic import BaseModel


class DocumentUploadResponse(BaseModel):
    """Response after a successful file upload."""

    document_id: UUID
    filename: str
    size_bytes: int
    content_sha256: str
    evidence_signature: str | None = None
    page_count: int | None = None
    parse_status: str
    parse_note: str | None = None
    source_id: UUID | None = None
    event_id: UUID | None = None
    created_at: datetime


class DocumentListItem(BaseModel):
    """Summary row for the document list endpoint."""

    document_id: UUID
    filename: str
    size_bytes: int
    parse_status: str
    page_count: int | None = None
    source_id: UUID | None = None
    event_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class DocumentListResponse(BaseModel):
    documents: list[DocumentListItem]
    total: int


class DocumentTextResponse(BaseModel):
    """Extracted plain text from a document, for manual review before ingestion."""

    document_id: UUID
    filename: str
    parse_status: str
    text: str | None = None
    parse_note: str | None = None


class DocumentIngestResponse(BaseModel):
    """Response after triggering ingestion of an already-uploaded document."""

    document_id: UUID
    filename: str
    parse_status: str
    ingestion_result: dict[str, Any] | None = None
    error: str | None = None


class DocumentReceiptResponse(BaseModel):
    """Verifiable evidence receipt for an uploaded document."""

    document_id: UUID
    filename: str
    content_sha256: str
    evidence_signature: str
    created_at: datetime


class DocumentVerifyResponse(BaseModel):
    """Result of verifying an evidence signature."""

    document_id: UUID
    filename: str
    content_sha256: str
    signature_valid: bool
