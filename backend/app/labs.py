from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re
import secrets
import unicodedata
from uuid import uuid4

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from .auth_models import UserProfile
from .lab_contracts import LabExtraction
from .lab_models import (
    LabAnalyte,
    LabDocument,
    LabExtraction as StoredExtraction,
    LabProcessingJob,
    LabReferenceRange,
    LabReport,
    LabResult,
    LabTextChunk,
)


MAX_LAB_FILE_BYTES = 20 * 1024 * 1024
MAX_LAB_PAGES = 50
MAX_IMAGE_PIXELS = 40_000_000
LAB_EXTRACTION_CHUNK_CHARS = 80_000
LAB_RETRIEVAL_CHUNK_CHARS = 36_000
CATALOG_PATH = Path(__file__).parent / "data" / "lab_reference_catalog.v1.json"


class LabFileError(ValueError):
    pass


def detect_media_type(header: bytes, filename: str) -> str:
    suffix = Path(filename).suffix.casefold()
    if header.startswith(b"%PDF-"):
        media, allowed = "application/pdf", {".pdf"}
    elif header.startswith(b"\x89PNG\r\n\x1a\n"):
        media, allowed = "image/png", {".png"}
    elif header.startswith(b"\xff\xd8\xff"):
        media, allowed = "image/jpeg", {".jpg", ".jpeg"}
    elif len(header) >= 12 and header[4:8] == b"ftyp" and header[8:12].lower() in {
        b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"
    }:
        media, allowed = "image/heic", {".heic", ".heif"}
    else:
        raise LabFileError("unsupported_file_type")
    if suffix not in allowed:
        raise LabFileError("file_type_mismatch")
    return media


def safe_filename(value: str | None) -> str:
    name = Path(value or "analysis").name.strip().replace("\x00", "")
    return name[:240] or "analysis"


def store_upload(stream, filename: str, storage_dir: Path) -> tuple[Path, str, str, int, str]:
    storage_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    storage_dir.chmod(0o700)
    storage_key = f"{secrets.token_hex(24)}.bin"
    target = storage_dir / storage_key
    digest = sha256()
    total = 0
    header = b""
    descriptor = target.open("xb", buffering=0)
    try:
        while chunk := stream.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_LAB_FILE_BYTES:
                raise LabFileError("file_too_large")
            if len(header) < 64:
                header = (header + chunk)[:64]
            digest.update(chunk)
            descriptor.write(chunk)
        if total == 0:
            raise LabFileError("empty_file")
        descriptor.close()
        target.chmod(0o600)
        media_type = detect_media_type(header, filename)
        return target, storage_key, media_type, total, digest.hexdigest()
    except Exception:
        descriptor.close()
        target.unlink(missing_ok=True)
        raise


def enqueue_document(
    db: Session,
    *,
    storage_key: str,
    filename: str,
    file_sha256: str,
    media_type: str,
    size_bytes: int,
) -> LabDocument:
    existing = db.scalar(select(LabDocument).where(LabDocument.file_sha256 == file_sha256))
    if existing is not None:
        return existing
    now = datetime.now(timezone.utc)
    document = LabDocument(
        id=str(uuid4()),
        storage_key=storage_key,
        original_filename=safe_filename(filename),
        file_sha256=file_sha256,
        media_type=media_type,
        size_bytes=size_bytes,
        status="queued",
        verified=False,
        created_at=now,
        updated_at=now,
    )
    db.add(document)
    db.flush()
    db.add(
        LabProcessingJob(
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


def _fold(value: str | None) -> str:
    raw = unicodedata.normalize("NFKC", value or "").casefold().replace("ё", "е")
    return re.sub(r"[^a-zа-я0-9%]+", "", raw)


def seed_reference_catalog(db: Session) -> None:
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    analytes: dict[str, LabAnalyte] = {}
    for item in payload["entries"]:
        analyte = analytes.get(item["id"])
        if analyte is None:
            analyte = db.get(LabAnalyte, item["id"])
            if analyte is None:
                analyte = LabAnalyte(
                    id=item["id"],
                    display_name=item["name"],
                    aliases=item.get("aliases", []),
                )
                db.add(analyte)
            analytes[item["id"]] = analyte
    db.flush()
    if not payload.get("reference_ranges_enabled", False):
        return
    if db.scalar(
        select(func.count())
        .select_from(LabReferenceRange)
        .where(LabReferenceRange.catalog_version == payload["version"])
    ):
        return
    for item in payload["entries"]:
        db.add(
            LabReferenceRange(
                catalog_version=payload["version"],
                analyte_id=item["id"],
                specimen=item["specimen"],
                unit=item["unit"],
                reference_sex=item.get("sex", "any"),
                min_age_years=item.get("min_age_years"),
                max_age_years=item.get("max_age_years"),
                low=item.get("low"),
                high=item.get("high"),
                reference_text=item.get("reference_text"),
                source=payload["source"],
                reviewed_on=date.fromisoformat(payload["reviewed_on"]),
            )
        )
    db.flush()


def canonical_analyte(db: Session, name: str, hint: str | None = None) -> LabAnalyte:
    seed_reference_catalog(db)
    wanted = {_fold(name), _fold(hint)} - {""}
    for analyte in db.scalars(select(LabAnalyte).order_by(LabAnalyte.id)):
        aliases = {_fold(analyte.id), _fold(analyte.display_name)}
        aliases.update(_fold(alias) for alias in (analyte.aliases or []))
        if wanted & aliases:
            return analyte
    base_source = hint or name
    base = re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", base_source).encode("ascii", "ignore").decode().casefold()).strip("-")
    identifier = base[:80] or f"custom-{sha256(name.encode()).hexdigest()[:16]}"
    existing = db.get(LabAnalyte, identifier)
    if existing is not None:
        return existing
    analyte = LabAnalyte(id=identifier, display_name=name[:240], aliases=[])
    db.add(analyte)
    db.flush()
    return analyte


def _age_on(birth_date: date | None, observed_on: date | None) -> int | None:
    if birth_date is None or observed_on is None or observed_on < birth_date:
        return None
    return observed_on.year - birth_date.year - ((observed_on.month, observed_on.day) < (birth_date.month, birth_date.day))


def resolve_catalog_range(
    db: Session,
    analyte_id: str,
    specimen: str | None,
    unit: str | None,
    observed_on: date | None,
) -> LabReferenceRange | None:
    if not specimen or not unit:
        return None
    profile = db.get(UserProfile, 1)
    sex = profile.reference_sex if profile and profile.reference_sex in {"male", "female"} else "any"
    age = _age_on(profile.birth_date if profile else None, observed_on)
    candidates = list(
        db.scalars(
            select(LabReferenceRange).where(
                LabReferenceRange.analyte_id == analyte_id,
                func.lower(LabReferenceRange.specimen) == specimen.casefold(),
                func.lower(LabReferenceRange.unit) == unit.casefold(),
                LabReferenceRange.reference_sex.in_([sex, "any"]),
            )
        )
    )
    for item in sorted(candidates, key=lambda row: row.reference_sex != sex):
        if item.min_age_years is not None and (age is None or age < item.min_age_years):
            continue
        if item.max_age_years is not None and (age is None or age > item.max_age_years):
            continue
        return item
    return None


def calculate_status(
    value_numeric: Decimal | None,
    value_text: str | None,
    comparator: str | None,
    low: Decimal | None,
    high: Decimal | None,
    reference_text: str | None,
) -> str:
    if value_numeric is not None and comparator in {None, "="}:
        if low is not None and value_numeric < low:
            return "below_reference"
        if high is not None and value_numeric > high:
            return "above_reference"
        if low is not None or high is not None:
            return "within_reference"
    if value_text and reference_text:
        value_folded, reference_folded = _fold(value_text), _fold(reference_text)
        negatives = {"negative", "отрицательно", "необнаружено", "notdetected"}
        if value_folded == reference_folded or value_folded in negatives and reference_folded in negatives:
            return "within_reference"
        return "outside_reference"
    return "indeterminate"


def persist_extraction(
    db: Session,
    document: LabDocument,
    extraction: LabExtraction,
    *,
    chunk_index: int,
    model: str,
    contract_version: str,
    source_offset: int,
) -> int:
    db.add(
        StoredExtraction(
            document_id=document.id,
            contract_version=contract_version,
            model=model,
            chunk_index=chunk_index,
            raw_result=extraction.model_dump(mode="json"),
        )
    )
    report = LabReport(
        id=str(uuid4()),
        document_id=document.id,
        observed_on=extraction.report.observed_on,
        laboratory=extraction.report.laboratory,
        specimen=extraction.report.specimen,
    )
    db.add(report)
    db.flush()
    created = 0
    for index, item in enumerate(extraction.results):
        analyte = canonical_analyte(db, item.analyte_name, item.canonical_hint)
        observed = item.observed_on or report.observed_on
        specimen = item.specimen or report.specimen
        low, high, reference_text = item.reference_low, item.reference_high, item.reference_text
        reference_source = "laboratory" if low is not None or high is not None or reference_text else "none"
        if reference_source == "none":
            reference = resolve_catalog_range(db, analyte.id, specimen, item.unit, observed)
            if reference is not None:
                low, high, reference_text = reference.low, reference.high, reference.reference_text
                reference_source = "catalog"
        result = LabResult(
            id=str(uuid4()),
            document_id=document.id,
            report_id=report.id,
            analyte_id=analyte.id,
            source_index=source_offset + index,
            analyte_name=item.analyte_name,
            value_numeric=item.value_numeric,
            value_text=item.value_text,
            comparator=item.comparator,
            unit=item.unit,
            observed_on=observed,
            specimen=specimen,
            method=item.method,
            reference_low=low,
            reference_high=high,
            reference_text=reference_text,
            reference_source=reference_source,
            laboratory_flag=item.laboratory_flag,
            status=calculate_status(item.value_numeric, item.value_text, item.comparator, low, high, reference_text),
            verification_status="unverified",
            source_page=item.source_page,
            deleted=False,
        )
        db.add(result)
        created += 1
    return created


def _split_text(value: str, max_chars: int) -> list[str]:
    text = value.strip()
    pieces: list[str] = []
    while len(text) > max_chars:
        split_at = text.rfind("\n", 0, max_chars + 1)
        if split_at < max_chars // 2:
            split_at = text.rfind(" ", 0, max_chars + 1)
        if split_at < max_chars // 2:
            split_at = max_chars
        piece = text[:split_at].strip()
        if piece:
            pieces.append(piece)
        text = text[split_at:].strip()
    if text:
        pieces.append(text)
    return pieces


def bounded_page_chunks(pages: list[dict], max_chars: int) -> list[tuple[int, int, str]]:
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    chunks: list[tuple[int, int, str]] = []
    current: list[str] = []
    page_from: int | None = None
    page_to: int | None = None
    size = 0

    def flush() -> None:
        nonlocal current, page_from, page_to, size
        if current and page_from is not None and page_to is not None:
            chunks.append((page_from, page_to, "\n\n".join(current)))
        current, page_from, page_to, size = [], None, None, 0

    for page in pages:
        text = str(page.get("text") or "").strip()
        page_number = int(page.get("page") or 1)
        if not text:
            continue
        for piece in _split_text(text, max_chars):
            separator_size = 2 if current else 0
            if current and size + separator_size + len(piece) > max_chars:
                flush()
            if page_from is None:
                page_from = page_number
            page_to = page_number
            current.append(piece)
            size += (2 if len(current) > 1 else 0) + len(piece)
    flush()
    return chunks


def replace_text_chunks(db: Session, document: LabDocument, pages: list[dict]) -> list[LabTextChunk]:
    db.execute(delete(LabTextChunk).where(LabTextChunk.document_id == document.id))
    chunks = [
        LabTextChunk(
            document_id=document.id,
            chunk_index=index,
            page_from=page_from,
            page_to=page_to,
            content=content,
        )
        for index, (page_from, page_to, content) in enumerate(
            bounded_page_chunks(pages, LAB_RETRIEVAL_CHUNK_CHARS)
        )
    ]
    for chunk in chunks:
        db.add(chunk)
    return chunks


def claim_lab_job(db: Session, now: datetime, lease_seconds: int = 300) -> LabProcessingJob | None:
    expired = list(db.scalars(select(LabProcessingJob).where(LabProcessingJob.status == "processing", LabProcessingJob.lease_until < now)))
    for job in expired:
        job.status = "pending" if job.attempts < 3 else "failed"
        job.available_at = now
        job.lease_until = None
        job.error_code = "lease_expired"
        if job.status == "failed":
            job.finished_at = now
    db.flush()
    job = db.scalar(
        select(LabProcessingJob)
        .where(LabProcessingJob.status == "pending", LabProcessingJob.available_at <= now)
        .order_by(LabProcessingJob.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        db.commit()
        return None
    job.status = "processing"
    job.attempts += 1
    job.lease_until = now + timedelta(seconds=lease_seconds)
    document = db.get(LabDocument, job.document_id)
    if document:
        document.status = "processing"
        document.error_code = None
    db.commit()
    return job
