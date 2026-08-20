from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from .lab_models import StoredFile, StudyDocument, StudyProcessingJob
from .labs import safe_filename


STUDY_MODALITIES = frozenset({"ultrasound", "mri", "ct", "xray", "ecg", "other"})
_IDENTIFIER_LINE = re.compile(
    r"(?i)^\s*(?:фио|пациент|дата\s+рождения|год\s+рождения|снилс|полис|"
    r"адрес|телефон|номер\s+(?:карты|истории|заказа)|направил|врач)\s*[:.-]"
)


def enqueue_study(
    db: Session,
    *,
    storage_key: str,
    filename: str,
    file_sha256: str,
    media_type: str,
    size_bytes: int,
    content: bytes,
    modality: str,
    title: str | None,
    observed_on,
) -> StudyDocument:
    existing = db.scalar(
        select(StudyDocument).where(StudyDocument.file_sha256 == file_sha256)
    )
    if existing is not None:
        return existing
    if modality not in STUDY_MODALITIES:
        raise ValueError("invalid_modality")
    now = datetime.now(timezone.utc)
    stored = db.scalar(select(StoredFile).where(StoredFile.file_sha256 == file_sha256))
    if stored is None:
        stored = StoredFile(
            id=str(uuid4()),
            file_sha256=file_sha256,
            original_filename=safe_filename(filename),
            media_type=media_type,
            size_bytes=size_bytes,
            content=content,
            created_at=now,
        )
        db.add(stored)
        db.flush()
    document = StudyDocument(
        id=str(uuid4()),
        storage_key=storage_key,
        stored_file_id=stored.id,
        original_filename=safe_filename(filename),
        file_sha256=file_sha256,
        media_type=media_type,
        size_bytes=size_bytes,
        modality=modality,
        title=(title or "").strip()[:240] or None,
        observed_on=observed_on,
        status="queued",
        processing_stage="queued",
        progress_percent=0,
        verified=False,
        findings=[],
        created_at=now,
        updated_at=now,
    )
    db.add(document)
    db.flush()
    db.add(
        StudyProcessingJob(
            document_id=document.id,
            status="pending",
            attempts=0,
            available_at=now,
            created_at=now,
        )
    )
    db.commit()
    db.refresh(document)
    return document


def claim_study_job(
    db: Session, now: datetime, lease_seconds: int = 300
) -> StudyProcessingJob | None:
    expired = list(
        db.scalars(
            select(StudyProcessingJob).where(
                StudyProcessingJob.status == "processing",
                StudyProcessingJob.lease_until < now,
            )
        )
    )
    for job in expired:
        job.status = "pending" if job.attempts < 3 else "failed"
        job.available_at = now
        job.lease_until = None
        job.error_code = "lease_expired"
        if job.status == "failed":
            job.finished_at = now
    db.flush()
    job = db.scalar(
        select(StudyProcessingJob)
        .where(
            StudyProcessingJob.status == "pending",
            StudyProcessingJob.available_at <= now,
        )
        .order_by(StudyProcessingJob.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        db.commit()
        return None
    job.status = "processing"
    job.attempts += 1
    job.lease_until = now + timedelta(seconds=lease_seconds)
    document = db.get(StudyDocument, job.document_id)
    if document is not None:
        document.status = "processing"
        document.processing_stage = "reading"
        document.progress_percent = 10
        document.error_code = None
    db.commit()
    return job


def structure_study_text(text: str) -> tuple[list[str], str | None]:
    """Extract report sections deterministically; never manufacture an interpretation."""

    normalized = re.sub(r"\r\n?", "\n", text).strip()
    if not normalized:
        return [], None
    normalized = "\n".join(
        line for line in normalized.splitlines() if not _IDENTIFIER_LINE.search(line)
    ).strip()
    conclusion_match = re.search(
        r"(?ims)^\s*(?:заключение|выводы?|impression|conclusion)\s*[:.-]?\s*(.+?)(?=\n\s*[А-ЯA-Z][^\n]{0,60}:|\Z)",
        normalized,
    )
    conclusion = conclusion_match.group(1).strip()[:8000] if conclusion_match else None
    findings_source = normalized
    if conclusion_match:
        findings_source = normalized[: conclusion_match.start()]
    findings: list[str] = []
    for paragraph in re.split(r"\n\s*\n|(?<=\.)\s+(?=[А-ЯA-Z])", findings_source):
        value = re.sub(r"\s+", " ", paragraph).strip(" :-\t")
        if len(value) < 8:
            continue
        findings.append(value[:1200])
        if len(findings) >= 40:
            break
    return findings, conclusion
