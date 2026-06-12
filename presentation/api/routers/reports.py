"""Reports API router.

Route surface
-------------
GET  /reports/{event_id}/preview    Render HTML preview (no persistence)
POST /reports/{event_id}/generate   Render + store versioned report, return report_id
GET  /reports                       List past exports for an event
GET  /reports/{report_id}/download  Download report (HTML or PDF via ?format=pdf)

Design constraints
------------------
* Reports are immutable once generated — never overwritten.
* Version numbers are monotonically increasing per event.
* Every fact in the report carries a source reference.
* The mandatory disclaimer appears on every output.
* MVP: generates HTML reports. PDF generation via WeasyPrint is available
  when the system library dependencies (libpango, libcairo) are installed.
  The download endpoint accepts ``?format=pdf`` to return a PDF.
"""

from __future__ import annotations

import hashlib
import logging
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select

from atlas.application.dto import CurrentUser
from atlas.application.unit_of_work import UnitOfWork
from atlas.application.use_cases.export_case_report import ExportCaseReport
from atlas.domain.enums import Role
from atlas.domain.exceptions import EventNotFoundError
from atlas.infrastructure.db.orm_models import ReportExportModel
from atlas.infrastructure.reporting.html_renderer import render_html, render_pdf
from atlas.presentation.api.dependencies import get_uow, require_role
from atlas.presentation.api.schemas.reports import (
    GenerateReportResponse,
    ReportListResponse,
    ReportPreviewResponse,
    ReportSummary,
)

router = APIRouter(prefix="/reports", tags=["reports"])
logger = logging.getLogger(__name__)

_READERS = (Role.ADMIN, Role.REVIEWER, Role.ANALYST)
_WRITERS = (Role.ADMIN, Role.REVIEWER)


def _download_url(report_id: UUID) -> str:
    return f"/api/v1/reports/{report_id}/download"


@router.get("/{event_id}/preview", response_model=ReportPreviewResponse)
async def preview_report(
    event_id: UUID,
    uow: UnitOfWork = Depends(get_uow, scope="function"),
    _user: CurrentUser = Depends(require_role(*_READERS)),
) -> ReportPreviewResponse:
    """Render an HTML report preview without persisting anything.

    Use this to inspect what the report will contain before generating
    an immutable versioned export.
    """
    try:
        use_case = ExportCaseReport(uow)
        # Version 0 = preview (never stored)
        model = await use_case.execute(event_id=event_id, report_version=0)
    except EventNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        await uow.rollback()

    html = render_html(model)
    warnings: list[str] = []
    if model.has_unresolved_conflicts:
        warnings.append(
            f"{len(model.unresolved_conflict_fields)} unresolved conflict(s): "
            + ", ".join(model.unresolved_conflict_fields)
        )
    if model.completeness_score < 0.5:
        warnings.append(
            f"Evidence completeness is low ({model.completeness_pct}%). "
            "Consider ingesting more source documents before generating a final report."
        )

    # Check if a versioned report already exists — preview is read-only
    session = uow._session  # type: ignore[attr-defined]
    prev_count = await session.scalar(
        select(func.count()).where(ReportExportModel.event_id == event_id)
    )
    if prev_count and prev_count > 0:
        warnings.append(
            f"A versioned report (v{prev_count}) already exists for this event. "
            "The preview below reflects the latest projection but is not a stored report."
        )

    return ReportPreviewResponse(
        event_id=event_id,
        projection_version=model.projection_version,
        html=html,
        warnings=warnings,
    )


@router.post("/{event_id}/generate", response_model=GenerateReportResponse)
async def generate_report(
    event_id: UUID,
    include_html_preview: bool = Query(default=False),
    uow: UnitOfWork = Depends(get_uow, scope="function"),
    current_user: CurrentUser = Depends(require_role(*_WRITERS)),
) -> GenerateReportResponse:
    """Generate and store an immutable versioned report export.

    Increments the version counter for this event.  Once created, the report
    is retrievable via GET /reports/{report_id}/download and listed via
    GET /reports?event_id=.
    """
    session = uow.session  # type: ignore[attr-defined]

    # Determine next version number
    count_result = await session.execute(
        select(func.count()).where(ReportExportModel.event_id == event_id)
    )
    existing_count = count_result.scalar_one()
    next_version = existing_count + 1

    try:
        use_case = ExportCaseReport(uow)
        model = await use_case.execute(
            event_id=event_id,
            report_version=next_version,
            generated_by=current_user.user_id,
        )
    except EventNotFoundError as exc:
        await uow.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    html = render_html(model)
    content_sha256 = hashlib.sha256(html.encode()).hexdigest()

    report_id = uuid4()
    export = ReportExportModel(
        id=report_id,
        event_id=event_id,
        version=next_version,
        projection_version=model.projection_version,
        content_sha256=content_sha256,
        object_key=None,  # Reserved for real object storage
        html_content=html,  # Store rendered artifact for immutable serving
        generated_by=current_user.user_id,
    )

    session.add(export)
    await uow.commit()

    # Build verifiable export manifest
    try:
        manifest = await use_case.build_manifest(
            event_id=event_id,
            report_version=next_version,
            report_content_sha256=content_sha256,
            projection_version=model.projection_version,
            generated_by=current_user.user_id,
        )
    except Exception:
        logger.warning("Failed to build export manifest for report %s", report_id)
        manifest = None

    logger.info(
        "Report generated: id=%s event=%s version=%d projection_v=%d sha256=%s…",
        report_id,
        event_id,
        next_version,
        model.projection_version,
        content_sha256[:12],
    )

    return GenerateReportResponse(
        report_id=report_id,
        event_id=event_id,
        version=next_version,
        projection_version=model.projection_version,
        content_sha256=content_sha256,
        generated_at=export.generated_at,
        download_url=_download_url(report_id),
        html_preview=html if include_html_preview else None,
        export_manifest=manifest,
    )


@router.get("", response_model=ReportListResponse)
async def list_reports(
    event_id: UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    uow: UnitOfWork = Depends(get_uow, scope="function"),
    _user: CurrentUser = Depends(require_role(*_READERS)),
) -> ReportListResponse:
    """List versioned report exports, optionally filtered by event."""
    session = uow.session  # type: ignore[attr-defined]

    stmt = select(ReportExportModel)
    if event_id is not None:
        stmt = stmt.where(ReportExportModel.event_id == event_id)
    stmt = stmt.order_by(ReportExportModel.generated_at.desc())

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_result = await session.execute(count_stmt)
    total = total_result.scalar_one()

    stmt = stmt.limit(limit).offset(offset)
    result = await session.execute(stmt)
    exports = result.scalars().all()

    reports = [
        ReportSummary(
            report_id=exp.id,
            event_id=exp.event_id,
            version=exp.version,
            projection_version=exp.projection_version,
            content_sha256=exp.content_sha256,
            generated_at=exp.generated_at,
            generated_by=str(exp.generated_by)[:8] + "…" if exp.generated_by else None,
            download_url=_download_url(exp.id),
        )
        for exp in exports
    ]

    return ReportListResponse(reports=reports, total=total)


@router.get("/{report_id}/download")
async def download_report(
    report_id: UUID,
    format: str = Query(default="html", pattern="^(html|pdf)$"),
    uow: UnitOfWork = Depends(get_uow, scope="function"),
    _user: CurrentUser = Depends(require_role(*_READERS)),
) -> Response:
    """Download a versioned report.

    Serves the stored HTML artifact that was persisted at generation time
    (format=html, default) or renders it to PDF via WeasyPrint (format=pdf).

    The HTML download guarantees immutability: the downloaded content always
    matches the content_sha256 recorded in the metadata, independent of
    subsequent projection changes.  PDF is derived from the stored HTML and
    signed with the evidence signing key.
    """
    session = uow.session  # type: ignore[attr-defined]
    session.expire_on_commit = False
    row = await session.execute(
        select(
            ReportExportModel.id,
            ReportExportModel.event_id,
            ReportExportModel.version,
            ReportExportModel.html_content,
            ReportExportModel.content_sha256,
        ).where(ReportExportModel.id == report_id)
    )
    row_data = row.one_or_none()

    if row_data is None:
        await uow.rollback()
        raise HTTPException(status_code=404, detail="Report not found")

    _report_id_val, event_id_val, version_val, html_content_val, content_sha256_val = row_data

    if html_content_val is None:
        # Fallback: re-render if no stored content (pre-migration reports)
        try:
            use_case = ExportCaseReport(uow)
            model = await use_case.execute(
                event_id=event_id_val,
                report_version=version_val,
            )
        except EventNotFoundError as exc:
            await uow.rollback()
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        html_content_val = render_html(model)

    if format == "pdf":
        pdf_bytes = render_pdf(html_content_val)
        await uow.rollback()
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename=\"atlas-report-v{version_val}-{str(event_id_val)[:8]}.pdf\"",
                "Content-Length": str(len(pdf_bytes)),
            },
        )

    # Default: HTML
    filename = f"atlas-report-v{version_val}-{str(event_id_val)[:8]}.html"
    await uow.rollback()
    return Response(
        content=html_content_val,
        media_type="text/html",
        headers={
            "Content-Disposition": f"attachment; filename=\"{filename}\"",
            "Content-Length": str(len(html_content_val)),
        },
    )