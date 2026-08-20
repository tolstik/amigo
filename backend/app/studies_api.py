from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from .ai_snapshot import enqueue_current_analysis
from .background_wait import wait_for_queue_event
from .auth import AuthContext, require_csrf
from .config import Settings, get_settings
from .db import SessionLocal, get_db
from .lab_models import LabDocument, StoredFile, StudyDocument, StudyProcessingJob
from .labs import LabFileError, preview_bytes, safe_filename, store_upload
from .studies import STUDY_MODALITIES, enqueue_study


router = APIRouter(prefix="/api/v1/studies", tags=["studies"])


class StudyPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    modality: Literal["ultrasound", "mri", "ct", "xray", "ecg", "other"] | None = None
    title: Annotated[str | None, StringConstraints(max_length=240)] = None
    observed_on: date | None = None
    findings: list[Annotated[str, StringConstraints(min_length=1, max_length=1200)]] | None = None
    conclusion: Annotated[str | None, StringConstraints(max_length=8000)] = None


def _study(row: StudyDocument, queue_position: int | None = None, detail: bool = False) -> dict:
    payload = {
        "id": row.id,
        "filename": row.original_filename,
        "media_type": row.media_type,
        "size_bytes": row.size_bytes,
        "modality": row.modality,
        "title": row.title,
        "observed_on": row.observed_on,
        "status": row.status,
        "processing_stage": row.processing_stage,
        "progress_percent": row.progress_percent,
        "queue_position": queue_position,
        "verified": row.verified,
        "page_count": row.page_count,
        "error_code": row.error_code,
        "created_at": row.created_at,
        "completed_at": row.completed_at,
        "findings": row.findings or [],
        "conclusion": row.conclusion,
    }
    if detail:
        payload["extracted_text"] = row.extracted_text
    return payload


def _content(db: Session, row: StudyDocument) -> bytes:
    stored = db.get(StoredFile, row.stored_file_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="original_not_found")
    data = bytes(stored.content)
    if (
        stored.file_sha256 != row.file_sha256
        or stored.size_bytes != row.size_bytes
        or len(data) != row.size_bytes
        or sha256(data).hexdigest() != row.file_sha256
    ):
        raise HTTPException(status_code=404, detail="original_changed")
    return data


@router.post("/documents", status_code=status.HTTP_202_ACCEPTED, include_in_schema=False)
@router.post("/uploads", status_code=status.HTTP_202_ACCEPTED)
def upload_study(
    file: UploadFile = File(...),
    modality: str = Form(...),
    title: str | None = Form(default=None),
    observed_on: date | None = Form(default=None),
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    if modality not in STUDY_MODALITIES:
        raise HTTPException(status_code=422, detail="invalid_modality")
    filename = safe_filename(file.filename)
    try:
        path, storage_key, media_type, size_bytes, digest, content = store_upload(
            file.file, filename, settings.lab_storage_dir
        )
    except LabFileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    existing = db.scalar(select(StudyDocument).where(StudyDocument.file_sha256 == digest))
    if existing is not None:
        path.unlink(missing_ok=True)
        return _study(existing)
    try:
        row = enqueue_study(
            db,
            storage_key=storage_key,
            filename=filename,
            file_sha256=digest,
            media_type=media_type,
            size_bytes=size_bytes,
            content=content,
            modality=modality,
            title=title,
            observed_on=observed_on,
        )
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return _study(row)


@router.get("/documents")
def list_studies(db: Session = Depends(get_db)) -> dict:
    rows = list(db.scalars(select(StudyDocument).order_by(StudyDocument.created_at.desc())))
    jobs = list(
        db.scalars(
            select(StudyProcessingJob)
            .where(StudyProcessingJob.status.in_(["pending", "processing"]))
            .order_by(StudyProcessingJob.id)
        )
    )
    positions = {job.document_id: index + 1 for index, job in enumerate(jobs)}
    return {"items": [_study(row, positions.get(row.id)) for row in rows]}


@router.get("/events")
async def study_events(settings: Settings = Depends(get_settings)) -> StreamingResponse:
    """Push compact study queue changes without periodic browser polling."""

    async def stream():
        previous = ""
        deadline = datetime.now(timezone.utc) + timedelta(hours=1)
        while datetime.now(timezone.utc) < deadline:
            with SessionLocal() as event_db:
                rows = list(
                    event_db.execute(
                        select(
                            StudyDocument.id,
                            StudyDocument.status,
                            StudyDocument.processing_stage,
                            StudyDocument.progress_percent,
                            StudyDocument.updated_at,
                        ).order_by(StudyDocument.updated_at.desc()).limit(100)
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
def study_detail(document_id: str, db: Session = Depends(get_db)) -> dict:
    row = db.get(StudyDocument, document_id)
    if row is None:
        raise HTTPException(status_code=404, detail="study_not_found")
    return _study(row, detail=True)


@router.get("/documents/{document_id}/view")
def view_study(document_id: str, db: Session = Depends(get_db)) -> StreamingResponse:
    row = db.get(StudyDocument, document_id)
    if row is None:
        raise HTTPException(status_code=404, detail="study_not_found")
    data = _content(db, row)
    data, preview_type = preview_bytes(data, row.media_type)
    return StreamingResponse(
        iter([data]),
        media_type=preview_type,
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(row.original_filename)}",
            "Content-Length": str(len(data)),
        },
    )


@router.get("/documents/{document_id}/download")
def download_study(document_id: str, db: Session = Depends(get_db)) -> StreamingResponse:
    row = db.get(StudyDocument, document_id)
    if row is None:
        raise HTTPException(status_code=404, detail="study_not_found")
    data = _content(db, row)
    return StreamingResponse(
        iter([data]),
        media_type=row.media_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(row.original_filename)}",
            "Content-Length": str(len(data)),
        },
    )


@router.patch("/documents/{document_id}")
def patch_study(
    document_id: str,
    payload: StudyPatch,
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    row = db.get(StudyDocument, document_id)
    if row is None:
        raise HTTPException(status_code=404, detail="study_not_found")
    for field in payload.model_fields_set:
        setattr(row, field, getattr(payload, field))
    row.verified = False
    db.commit()
    enqueue_current_analysis(db, settings, trigger="manual", debounce_seconds=0)
    return _study(row, detail=True)


@router.post("/documents/{document_id}/confirm")
def confirm_study(
    document_id: str,
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    row = db.get(StudyDocument, document_id)
    if row is None:
        raise HTTPException(status_code=404, detail="study_not_found")
    if row.status != "complete":
        raise HTTPException(status_code=409, detail="study_not_ready")
    row.verified = True
    db.commit()
    enqueue_current_analysis(db, settings, trigger="manual", debounce_seconds=0)
    return _study(row, detail=True)


@router.post("/documents/{document_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_study(
    document_id: str,
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    row = db.get(StudyDocument, document_id)
    if row is None:
        raise HTTPException(status_code=404, detail="study_not_found")
    job = db.scalar(select(StudyProcessingJob).where(StudyProcessingJob.document_id == row.id))
    if job is None:
        job = StudyProcessingJob(document_id=row.id, status="pending", attempts=0)
        db.add(job)
    elif job.status not in {"pending", "processing"}:
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
    return _study(row)


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_study(
    document_id: str,
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    row = db.get(StudyDocument, document_id)
    if row is None:
        raise HTTPException(status_code=404, detail="study_not_found")
    stored_id = row.stored_file_id
    storage_key = row.storage_key
    db.delete(row)
    db.commit()
    in_studies = db.scalar(select(StudyDocument.id).where(StudyDocument.stored_file_id == stored_id))
    in_labs = db.scalar(select(LabDocument.id).where(LabDocument.stored_file_id == stored_id))
    if in_studies is None and in_labs is None:
        stored = db.get(StoredFile, stored_id)
        if stored is not None:
            db.delete(stored)
            db.commit()
    (settings.lab_storage_dir / storage_key).unlink(missing_ok=True)
    enqueue_current_analysis(db, settings, trigger="manual", debounce_seconds=0)
    return Response(status_code=204)
