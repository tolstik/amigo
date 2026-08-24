from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from .ai_snapshot import enqueue_current_analysis
from .background_wait import wait_for_queue_event
from .auth import AI_DATA_CONSENT_VERSION, AuthContext, require_csrf
from .auth_models import UserProfile
from .config import Settings, get_settings
from .db import SessionLocal, get_db
from .lab_models import (
    LabDocument,
    LabProcessingJob,
    LabReferenceRange,
    LabResult,
    LabResultEdit,
    StoredFile,
    StudyDocument,
)
from .labs import (
    LabFileError,
    analyte_guide,
    calculate_status,
    canonical_analyte,
    enqueue_document,
    resolve_catalog_range,
    safe_filename,
    original_bytes,
    preview_bytes,
    store_upload,
)


router = APIRouter(prefix="/api/v1/labs", tags=["labs"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ResultPatch(StrictModel):
    analyte_name: Annotated[str | None, StringConstraints(min_length=1, max_length=240)] = None
    value_numeric: Decimal | None = None
    value_text: Annotated[str | None, StringConstraints(max_length=240)] = None
    comparator: Literal["<", "<=", "=", ">=", ">"] | None = None
    unit: Annotated[str | None, StringConstraints(max_length=80)] = None
    observed_on: date | None = None
    specimen: Annotated[str | None, StringConstraints(max_length=120)] = None
    method: Annotated[str | None, StringConstraints(max_length=240)] = None
    reference_low: Decimal | None = None
    reference_high: Decimal | None = None
    reference_text: Annotated[str | None, StringConstraints(max_length=240)] = None
    deleted: bool | None = None


class ResultCreate(StrictModel):
    analyte_name: Annotated[str, StringConstraints(min_length=1, max_length=240)]
    value_numeric: Decimal | None = None
    value_text: Annotated[str | None, StringConstraints(max_length=240)] = None
    comparator: Literal["<", "<=", "=", ">=", ">"] | None = None
    unit: Annotated[str | None, StringConstraints(max_length=80)] = None
    observed_on: date | None = None
    specimen: Annotated[str | None, StringConstraints(max_length=120)] = None
    method: Annotated[str | None, StringConstraints(max_length=240)] = None
    reference_low: Decimal | None = None
    reference_high: Decimal | None = None
    reference_text: Annotated[str | None, StringConstraints(max_length=240)] = None
    source_page: int | None = Field(default=None, ge=1, le=50)

    @model_validator(mode="after")
    def has_value_and_valid_range(self) -> "ResultCreate":
        if self.value_numeric is None and not self.value_text:
            raise ValueError("a numeric or textual value is required")
        if (
            self.reference_low is not None
            and self.reference_high is not None
            and self.reference_low > self.reference_high
        ):
            raise ValueError("reference_low must not exceed reference_high")
        return self


class LabCompareRequest(StrictModel):
    document_ids: list[str] = Field(min_length=2, max_length=3)

    @model_validator(mode="after")
    def unique_documents(self) -> "LabCompareRequest":
        if len(set(self.document_ids)) != len(self.document_ids):
            raise ValueError("document_ids must be unique")
        return self


def _consent_required(db: Session) -> None:
    profile = db.get(UserProfile, 1)
    if profile is None or profile.ai_data_consent_version != AI_DATA_CONSENT_VERSION:
        raise HTTPException(status_code=409, detail="ai_data_consent_required")


def _result(row: LabResult) -> dict:
    return {
        "id": row.id,
        "document_id": row.document_id,
        "analyte_id": row.analyte_id,
        "analyte_name": row.analyte_name,
        "value_numeric": float(row.value_numeric) if row.value_numeric is not None else None,
        "value_text": row.value_text,
        "comparator": row.comparator,
        "unit": row.unit,
        "observed_on": row.observed_on,
        "specimen": row.specimen,
        "method": row.method,
        "reference_low": float(row.reference_low) if row.reference_low is not None else None,
        "reference_high": float(row.reference_high) if row.reference_high is not None else None,
        "reference_text": row.reference_text,
        "reference_source": row.reference_source,
        "laboratory_flag": row.laboratory_flag,
        "status": row.status,
        "verification_status": row.verification_status,
        "source_page": row.source_page,
        "deleted": row.deleted,
    }


def _document(row: LabDocument, detail: bool = False, queue_position: int | None = None) -> dict:
    payload = {
        "id": row.id,
        "filename": row.original_filename,
        "media_type": row.media_type,
        "size_bytes": row.size_bytes,
        "status": row.status,
        "processing_stage": row.processing_stage,
        "progress_percent": row.progress_percent,
        "queue_position": queue_position,
        "verified": row.verified,
        "page_count": row.page_count,
        "error_code": row.error_code,
        "created_at": row.created_at,
        "completed_at": row.completed_at,
        "result_count": len([item for item in row.results if not item.deleted]),
    }
    if detail:
        payload["extracted_text"] = row.extracted_text
        payload["pages"] = row.parser_pages
        payload["results"] = [_result(item) for item in sorted(row.results, key=lambda item: item.source_index)]
    return payload


@router.post("/documents", status_code=status.HTTP_202_ACCEPTED, include_in_schema=False)
@router.post("/uploads", status_code=status.HTTP_202_ACCEPTED)
def upload_document(
    file: UploadFile = File(...),
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    _consent_required(db)
    filename = safe_filename(file.filename)
    try:
        path, storage_key, media_type, size_bytes, digest, content = store_upload(
            file.file, filename, settings.lab_storage_dir
        )
    except LabFileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    existing = db.scalar(select(LabDocument).where(LabDocument.file_sha256 == digest))
    if existing is not None:
        path.unlink(missing_ok=True)
        return _document(existing)
    try:
        document = enqueue_document(
            db,
            storage_key=storage_key,
            filename=filename,
            file_sha256=digest,
            media_type=media_type,
            size_bytes=size_bytes,
            content=content,
        )
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return _document(document)


@router.get("/documents")
def list_documents(db: Session = Depends(get_db)) -> dict:
    rows = list(
        db.scalars(
            select(LabDocument)
            .options(selectinload(LabDocument.results))
            .order_by(LabDocument.created_at.desc())
        )
    )
    active_jobs = list(
        db.scalars(
            select(LabProcessingJob)
            .where(LabProcessingJob.status.in_(["pending", "processing"]))
            .order_by(LabProcessingJob.id)
        )
    )
    positions = {job.document_id: index + 1 for index, job in enumerate(active_jobs)}
    return {"items": [_document(row, queue_position=positions.get(row.id)) for row in rows]}


@router.get("/events")
async def document_events(settings: Settings = Depends(get_settings)) -> StreamingResponse:
    """Emit compact queue changes without spending the upload rate-limit budget."""

    async def stream():
        previous = ""
        deadline = datetime.now(timezone.utc) + timedelta(hours=1)
        while datetime.now(timezone.utc) < deadline:
            with SessionLocal() as event_db:
                rows = list(
                    event_db.execute(
                        select(
                            LabDocument.id,
                            LabDocument.status,
                            LabDocument.processing_stage,
                            LabDocument.progress_percent,
                            LabDocument.updated_at,
                        ).order_by(LabDocument.updated_at.desc()).limit(100)
                    )
                )
            payload = json.dumps(
                [
                    [row.id, row.status, row.processing_stage, row.progress_percent, row.updated_at.isoformat()]
                    for row in rows
                ],
                separators=(",", ":"),
            )
            if payload != previous:
                yield f"event: queue\ndata: {payload}\n\n"
                previous = payload
            else:
                yield ": heartbeat\n\n"
            await asyncio.to_thread(
                wait_for_queue_event,
                settings.database_url,
                30,
            )

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/documents/{document_id}")
def document_detail(document_id: str, db: Session = Depends(get_db)) -> dict:
    row = db.scalar(
        select(LabDocument)
        .options(selectinload(LabDocument.results))
        .where(LabDocument.id == document_id)
    )
    if row is None:
        raise HTTPException(status_code=404, detail="document_not_found")
    return _document(row, detail=True)


@router.get("/documents/{document_id}/download")
def download_document(
    document_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    row = db.get(LabDocument, document_id)
    if row is None:
        raise HTTPException(status_code=404, detail="document_not_found")
    try:
        data = original_bytes(db, row, settings.lab_storage_dir)
    except LabFileError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    disposition = f"attachment; filename*=UTF-8''{quote(row.original_filename)}"
    return StreamingResponse(
        iter([data]),
        media_type=row.media_type,
        headers={"Content-Disposition": disposition, "Content-Length": str(len(data))},
    )


@router.get("/documents/{document_id}/view")
def view_document(
    document_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    row = db.get(LabDocument, document_id)
    if row is None:
        raise HTTPException(status_code=404, detail="document_not_found")
    try:
        data = original_bytes(db, row, settings.lab_storage_dir)
        data, preview_type = preview_bytes(data, row.media_type)
    except LabFileError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    disposition = f"inline; filename*=UTF-8''{quote(row.original_filename)}"
    return StreamingResponse(
        iter([data]),
        media_type=preview_type,
        headers={"Content-Disposition": disposition, "Content-Length": str(len(data))},
    )


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: str,
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    row = db.get(LabDocument, document_id)
    if row is None:
        raise HTTPException(status_code=404, detail="document_not_found")
    path = settings.lab_storage_dir / row.storage_key
    stored_file_id = row.stored_file_id
    db.delete(row)
    db.commit()
    path.unlink(missing_ok=True)
    if stored_file_id:
        in_labs = db.scalar(select(LabDocument.id).where(LabDocument.stored_file_id == stored_file_id))
        in_studies = db.scalar(select(StudyDocument.id).where(StudyDocument.stored_file_id == stored_file_id))
        if in_labs is None and in_studies is None:
            stored = db.get(StoredFile, stored_file_id)
            if stored is not None:
                db.delete(stored)
                db.commit()
    enqueue_current_analysis(db, settings, trigger="manual", debounce_seconds=0)
    return Response(status_code=204)


@router.post("/documents/{document_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_document(
    document_id: str,
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    row = db.get(LabDocument, document_id)
    if row is None:
        raise HTTPException(status_code=404, detail="document_not_found")
    job = db.scalar(select(LabProcessingJob).where(LabProcessingJob.document_id == document_id))
    if job is None:
        job = LabProcessingJob(document_id=document_id, status="pending", attempts=0)
        db.add(job)
    elif job.status in {"pending", "processing"}:
        return _document(row)
    else:
        job.status = "pending"
        job.attempts = 0
        job.available_at = datetime.now(timezone.utc)
        job.lease_until = None
        job.error_code = None
        job.finished_at = None
    row.status = "queued"
    row.processing_stage = "queued"
    row.progress_percent = 0
    row.error_code = None
    db.commit()
    return _document(row)


@router.post("/documents/{document_id}/confirm")
def confirm_document(
    document_id: str,
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    row = db.scalar(
        select(LabDocument).options(selectinload(LabDocument.results)).where(LabDocument.id == document_id)
    )
    if row is None:
        raise HTTPException(status_code=404, detail="document_not_found")
    if row.status != "complete":
        raise HTTPException(status_code=409, detail="document_not_ready")
    row.verified = True
    for result in row.results:
        if not result.deleted:
            result.verification_status = "verified"
    db.commit()
    enqueue_current_analysis(db, settings, trigger="manual", debounce_seconds=0)
    return _document(row, detail=True)


def _audit_value(row: LabResult) -> dict:
    payload = _result(row)
    if isinstance(payload.get("observed_on"), date):
        payload["observed_on"] = payload["observed_on"].isoformat()
    return payload


def _apply_catalog_reference(db: Session, row: LabResult) -> None:
    if row.reference_source != "none":
        return
    reference = resolve_catalog_range(
        db,
        row.analyte_id or "",
        row.specimen,
        row.unit,
        row.observed_on,
    )
    if reference is not None:
        row.reference_low, row.reference_high = reference.low, reference.high
        row.reference_text, row.reference_source = reference.reference_text, "catalog"


@router.post("/documents/{document_id}/results", status_code=status.HTTP_201_CREATED)
def create_result(
    document_id: str,
    payload: ResultCreate,
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    document = db.scalar(
        select(LabDocument)
        .where(LabDocument.id == document_id)
        .with_for_update()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="document_not_found")
    if document.status != "complete":
        raise HTTPException(status_code=409, detail="document_not_ready")
    analyte = canonical_analyte(db, payload.analyte_name)
    latest_source_index = db.scalar(
        select(func.max(LabResult.source_index)).where(
            LabResult.document_id == document_id
        )
    )
    source_index = (latest_source_index if latest_source_index is not None else -1) + 1
    reference_source = "user" if any(
        value is not None
        for value in (
            payload.reference_low,
            payload.reference_high,
            payload.reference_text,
        )
    ) else "none"
    row = LabResult(
        id=str(uuid4()),
        document_id=document.id,
        report_id=None,
        analyte_id=analyte.id,
        source_index=source_index,
        analyte_name=payload.analyte_name,
        value_numeric=payload.value_numeric,
        value_text=payload.value_text,
        comparator=payload.comparator,
        unit=payload.unit,
        observed_on=payload.observed_on,
        specimen=payload.specimen,
        method=payload.method,
        reference_low=payload.reference_low,
        reference_high=payload.reference_high,
        reference_text=payload.reference_text,
        reference_source=reference_source,
        laboratory_flag=None,
        status="indeterminate",
        verification_status="corrected",
        source_page=payload.source_page,
        deleted=False,
    )
    _apply_catalog_reference(db, row)
    row.status = calculate_status(
        row.value_numeric,
        row.value_text,
        row.comparator,
        row.reference_low,
        row.reference_high,
        row.reference_text,
    )
    document.verified = False
    db.add(row)
    db.flush()
    db.add(LabResultEdit(result_id=row.id, before={}, after=_audit_value(row)))
    db.commit()
    enqueue_current_analysis(db, settings, trigger="manual", debounce_seconds=0)
    return _result(row)


@router.patch("/results/{result_id}")
def patch_result(
    result_id: str,
    payload: ResultPatch,
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    row = db.get(LabResult, result_id)
    if row is None:
        raise HTTPException(status_code=404, detail="result_not_found")
    before = _audit_value(row)
    fields = {
        "analyte_name", "value_numeric", "value_text", "comparator", "unit", "observed_on",
        "specimen", "method", "reference_low", "reference_high", "reference_text", "deleted",
    }
    catalog_inputs_changed = bool(
        {"analyte_name", "specimen", "unit", "observed_on"}
        & payload.model_fields_set
    )
    explicit_reference_changed = bool(
        {"reference_low", "reference_high", "reference_text"}
        & payload.model_fields_set
    )
    for name in fields & payload.model_fields_set:
        setattr(row, name, getattr(payload, name))
    if "analyte_name" in payload.model_fields_set:
        row.analyte_id = canonical_analyte(db, row.analyte_name).id
    if explicit_reference_changed:
        row.reference_source = "user" if any(
            value is not None for value in (row.reference_low, row.reference_high, row.reference_text)
        ) else "none"
    elif catalog_inputs_changed and row.reference_source == "catalog":
        row.reference_low = row.reference_high = row.reference_text = None
        row.reference_source = "none"
    if (
        row.reference_low is not None
        and row.reference_high is not None
        and row.reference_low > row.reference_high
    ):
        raise HTTPException(status_code=422, detail="invalid_reference_range")
    _apply_catalog_reference(db, row)
    row.status = calculate_status(
        row.value_numeric, row.value_text, row.comparator,
        row.reference_low, row.reference_high, row.reference_text,
    )
    row.verification_status = "corrected"
    row.document.verified = False
    db.flush()
    db.add(LabResultEdit(result_id=row.id, before=before, after=_audit_value(row)))
    db.commit()
    enqueue_current_analysis(db, settings, trigger="manual", debounce_seconds=0)
    return _result(row)


@router.get("/summary")
def lab_summary(db: Session = Depends(get_db)) -> dict:
    rows = list(
        db.scalars(
            select(LabResult)
            .where(LabResult.deleted.is_(False), LabResult.observed_on.is_not(None))
            .order_by(LabResult.observed_on.desc(), LabResult.created_at.desc())
        )
    )
    latest: dict[str, LabResult] = {}
    for row in rows:
        if row.analyte_id and row.analyte_id not in latest:
            latest[row.analyte_id] = row
    counts = {key: 0 for key in ("within_reference", "below_reference", "above_reference", "outside_reference", "indeterminate")}
    for row in latest.values():
        counts[row.status] = counts.get(row.status, 0) + 1
    return {"items": [_result(row) for row in latest.values()], "counts": counts}


@router.get("/analytes")
def analyte_list(db: Session = Depends(get_db)) -> dict:
    rows = db.execute(
        select(LabResult.analyte_id, LabResult.analyte_name, func.count(LabResult.id), func.max(LabResult.observed_on))
        .where(LabResult.deleted.is_(False), LabResult.analyte_id.is_not(None))
        .group_by(LabResult.analyte_id, LabResult.analyte_name)
        .order_by(LabResult.analyte_name)
    ).all()
    return {"items": [
        {"id": analyte_id, "name": name, "result_count": count, "latest_on": latest_on}
        for analyte_id, name, count, latest_on in rows
    ]}


@router.get("/analytes/{analyte_id}/history")
def analyte_history(analyte_id: str, db: Session = Depends(get_db)) -> dict:
    rows = list(
        db.scalars(
            select(LabResult)
            .where(
                LabResult.analyte_id == analyte_id,
                LabResult.deleted.is_(False),
                LabResult.observed_on.is_not(None),
            )
            .order_by(LabResult.observed_on, LabResult.created_at)
        )
    )
    return {
        "analyte_id": analyte_id,
        "guide": analyte_guide(db, analyte_id),
        "items": [_result(row) for row in rows],
    }


@router.get("/reference-catalog")
def reference_catalog(db: Session = Depends(get_db)) -> dict:
    rows = list(db.scalars(select(LabReferenceRange).order_by(LabReferenceRange.analyte_id, LabReferenceRange.id)))
    return {"items": [
        {
            "analyte_id": row.analyte_id,
            "specimen": row.specimen,
            "unit": row.unit,
            "reference_sex": row.reference_sex,
            "min_age_years": row.min_age_years,
            "max_age_years": row.max_age_years,
            "low": float(row.low) if row.low is not None else None,
            "high": float(row.high) if row.high is not None else None,
            "reference_text": row.reference_text,
            "source": row.source,
            "reviewed_on": row.reviewed_on,
            "version": row.catalog_version,
        } for row in rows
    ]}


@router.post("/compare")
def compare_lab_documents(
    payload: LabCompareRequest,
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    documents = list(
        db.scalars(
            select(LabDocument)
            .where(LabDocument.id.in_(payload.document_ids))
            .options(
                selectinload(LabDocument.results),
                selectinload(LabDocument.reports),
            )
        )
    )
    by_id = {row.id: row for row in documents}
    if any(document_id not in by_id for document_id in payload.document_ids):
        raise HTTPException(status_code=404, detail="lab_document_not_found")
    ordered = [by_id[document_id] for document_id in payload.document_ids]
    if any(row.status != "complete" for row in ordered):
        raise HTTPException(status_code=409, detail="lab_document_not_complete")

    panels = []
    for row in ordered:
        dates = sorted(
            {
                *(
                    report.observed_on
                    for report in row.reports
                    if report.observed_on is not None
                ),
                *(
                    result.observed_on
                    for result in row.results
                    if not result.deleted and result.observed_on is not None
                ),
            }
        )
        panels.append(
            {
                "document_id": row.id,
                "observed_on": dates[-1] if dates else None,
                "verified": row.verified,
                "result_count": len([item for item in row.results if not item.deleted]),
            }
        )

    grouped: dict[str, dict] = {}
    for panel_index, document in enumerate(ordered):
        for result in sorted(document.results, key=lambda item: item.source_index):
            if result.deleted:
                continue
            group_key = (
                f"analyte:{result.analyte_id}"
                if result.analyte_id is not None
                else f"unmatched:{document.id}:{result.id}"
            )
            group = grouped.setdefault(
                group_key,
                {
                    "analyte_id": result.analyte_id,
                    "analyte_name": result.analyte_name,
                    "cells": [[] for _ in ordered],
                },
            )
            group["cells"][panel_index].append(_result(result))

    rows = []
    for group in grouped.values():
        cells: list[list[dict]] = group["cells"]
        reason: str | None = None
        singles = [cell[0] for cell in cells if len(cell) == 1]
        if any(len(cell) == 0 for cell in cells):
            reason = "missing_result"
        elif any(len(cell) != 1 for cell in cells):
            reason = "multiple_results"
        elif any(item.get("value_numeric") is None for item in singles):
            reason = "non_numeric_value"
        elif any(item.get("comparator") not in (None, "=") for item in singles):
            reason = "qualified_value"
        elif len({item.get("unit") for item in singles}) != 1:
            reason = "different_unit"
        elif len({item.get("specimen") for item in singles}) != 1:
            reason = "different_specimen"
        elif len({item.get("method") for item in singles}) != 1:
            reason = "different_method"

        deltas = []
        if reason is None:
            baseline = float(singles[0]["value_numeric"])
            for panel_index, item in enumerate(singles[1:], start=1):
                current = float(item["value_numeric"])
                deltas.append(
                    {
                        "from_document_id": ordered[0].id,
                        "to_document_id": ordered[panel_index].id,
                        "absolute": round(current - baseline, 6),
                        "percent": round((current - baseline) / abs(baseline) * 100, 2)
                        if baseline != 0
                        else None,
                    }
                )
        statuses = [item.get("status") for item in singles]
        rows.append(
            {
                "analyte_id": group["analyte_id"],
                "analyte_name": group["analyte_name"],
                "cells": cells,
                "comparable": reason is None,
                "incompatibility": reason,
                "deltas": deltas,
                "missing": any(not cell for cell in cells),
                "status_changed": len(set(statuses)) > 1 if len(statuses) > 1 else False,
                "value_changed": any(item["absolute"] != 0 for item in deltas),
            }
        )
    rows.sort(key=lambda item: (str(item["analyte_name"]).casefold(), item["analyte_id"] or ""))
    return {"panels": panels, "rows": rows}
