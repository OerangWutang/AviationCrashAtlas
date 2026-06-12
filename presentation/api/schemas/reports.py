"""Request/response schemas for the reports API."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ReportPreviewResponse(BaseModel):
    """HTML preview of a report — not persisted, not versioned."""
    event_id: UUID
    projection_version: int
    html: str
    warnings: list[str]


class ReportSummary(BaseModel):
    """Summary row for the report list endpoint."""
    report_id: UUID
    event_id: UUID
    version: int
    projection_version: int
    content_sha256: str
    generated_at: datetime
    generated_by: str | None = None
    download_url: str


class ReportListResponse(BaseModel):
    reports: list[ReportSummary]
    total: int


class GenerateReportResponse(BaseModel):
    """Response after successfully generating a versioned report."""
    report_id: UUID
    event_id: UUID
    version: int
    projection_version: int
    content_sha256: str
    generated_at: datetime
    download_url: str
    html_preview: str | None = None
    export_manifest: dict[str, object] | None = None
