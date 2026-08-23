from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from functools import lru_cache
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
import secrets
import unicodedata
from uuid import uuid4

from PIL import Image
from pillow_heif import register_heif_opener

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from .auth_models import UserProfile
from .lab_contracts import (
    GatewayAnalyteGuideResponse,
    LAB_ANALYTE_GUIDE_PROMPT_VERSION,
    LabExtraction,
)
from .lab_models import (
    LabAnalyte,
    LabAnalyteGuide,
    LabAnalyteGuideJob,
    LabDocument,
    LabExtraction as StoredExtraction,
    LabProcessingJob,
    LabReferenceRange,
    LabReport,
    LabResult,
    LabTextChunk,
    StoredFile,
)


MAX_LAB_FILE_BYTES = 20 * 1024 * 1024
MAX_LAB_PAGES = 50
MAX_IMAGE_PIXELS = 40_000_000
LAB_EXTRACTION_CHUNK_CHARS = 80_000
LAB_RETRIEVAL_CHUNK_CHARS = 36_000
CATALOG_PATH = Path(__file__).parent / "data" / "lab_reference_catalog.v1.json"
ANALYTE_GUIDE_PATH = Path(__file__).parent / "data" / "lab_analyte_guides.v1.json"
register_heif_opener()


_DATE_VALUE_PATTERN = (
    r"(?:\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}|"
    r"\d{1,2}[.\-/]\d{1,2}[.\-/]\d{4})"
)
_OBSERVED_DATE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        rf"(?:дата(?:\s+и\s+время)?\s+(?:исследования|анализа|"
        rf"выполнения(?:\s+исследования)?|"
        rf"взятия(?:\s+(?:биоматериала|материала|образца|крови))?|"
        rf"забора(?:\s+(?:биоматериала|материала|образца|крови))?|"
        rf"получения(?:\s+(?:биоматериала|материала|образца))?)|"
        rf"(?:collection|collected|specimen|sampling|test|analysis)\s+date"
        rf"(?:\s+and\s+time)?)"
        rf"\s*(?:[:=\-]\s*)?(?P<value>{_DATE_VALUE_PATTERN})",
        rf"(?<![\w])дата(?!\s*(?:рожд(?:ения)?|birth))"
        rf"\s*(?:[:=\-]\s*)?(?P<value>{_DATE_VALUE_PATTERN})",
        rf"(?:дата\s+(?:отч[её]та|выдачи|готовности|заказа|регистрации)|"
        rf"(?:report|result|issued|order)\s+date)"
        rf"\s*(?:[:=\-]\s*)?(?P<value>{_DATE_VALUE_PATTERN})",
    )
)


class LabFileError(ValueError):
    pass


def _parse_observed_date(value: str, *, today: date) -> date | None:
    parts = re.split(r"[.\-/]", value)
    if len(parts) != 3:
        return None
    try:
        if len(parts[0]) == 4:
            year, month, day = (int(part) for part in parts)
        else:
            day, month, year = (int(part) for part in parts)
        parsed = date(year, month, day)
    except ValueError:
        return None
    if parsed.year < 1900 or parsed.year > today.year + 1:
        return None
    return parsed


def labeled_observed_date(text: str | None, *, today: date | None = None) -> date | None:
    """Return one unambiguous, explicitly labelled measurement date from OCR text."""

    current = today or date.today()
    normalized = unicodedata.normalize("NFKC", text or "").replace("ё", "е")
    for pattern in _OBSERVED_DATE_PATTERNS:
        candidates = {
            parsed
            for match in pattern.finditer(normalized)
            if (parsed := _parse_observed_date(match.group("value"), today=current)) is not None
        }
        if len(candidates) == 1:
            return next(iter(candidates))
        if candidates:
            return None
    return None


def _unique_non_birth_date(text: str | None, *, today: date) -> date | None:
    candidates: set[date] = set()
    for line in unicodedata.normalize("NFKC", text or "").splitlines():
        folded = line.casefold().replace("ё", "е")
        if re.search(r"(?:дата\s+рожд|д\.?\s*р\.?\s*[:=]|birth\s+date)", folded):
            continue
        for match in re.finditer(_DATE_VALUE_PATTERN, line):
            parsed = _parse_observed_date(match.group(0), today=today)
            if parsed is not None:
                candidates.add(parsed)
    return next(iter(candidates)) if len(candidates) == 1 else None


@lru_cache(maxsize=1)
def _analyte_guides() -> dict:
    payload = json.loads(ANALYTE_GUIDE_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload.get("entries"), dict):
        raise RuntimeError("invalid analyte guide catalog")
    return payload


def _catalog_analyte_guide(analyte_id: str) -> dict[str, str] | None:
    payload = _analyte_guides()
    entry = payload["entries"].get(analyte_id)
    if not isinstance(entry, dict):
        return None
    return {
        "summary": str(entry["summary"]),
        "why_tested": str(entry["why_tested"]),
        "low_meaning": str(entry["low_meaning"]),
        "high_meaning": str(entry["high_meaning"]),
        "version": str(payload["version"]),
        "reviewed_on": str(payload["reviewed_on"]),
        "source": "catalog",
    }


def analyte_guide(db: Session, analyte_id: str) -> dict[str, str]:
    catalog = _catalog_analyte_guide(analyte_id)
    if catalog is not None:
        return catalog
    generated = db.get(LabAnalyteGuide, analyte_id)
    if generated is not None:
        return {
            "summary": generated.summary,
            "why_tested": generated.why_tested,
            "low_meaning": generated.low_meaning,
            "high_meaning": generated.high_meaning,
            "version": generated.contract_version,
            "reviewed_on": generated.updated_at.date().isoformat(),
            "source": "ai_generated",
        }
    return {
        "summary": "Статья для этого показателя формируется локальным AI-контуром Amigo.",
        "why_tested": "После подготовки статьи здесь появятся назначение исследования и контекст интерпретации.",
        "low_meaning": "Раздел о значениях ниже референса готовится.",
        "high_meaning": "Раздел о значениях выше референса готовится.",
        "version": "pending",
        "reviewed_on": date.today().isoformat(),
        "source": "pending",
    }


def has_analyte_guide(db: Session, analyte_id: str) -> bool:
    return _catalog_analyte_guide(analyte_id) is not None or db.get(
        LabAnalyteGuide, analyte_id
    ) is not None


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


def store_upload(stream, filename: str, storage_dir: Path) -> tuple[Path, str, str, int, str, bytes]:
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
        content = target.read_bytes()
        return target, storage_key, media_type, total, digest.hexdigest(), content
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
    content: bytes,
) -> LabDocument:
    existing = db.scalar(select(LabDocument).where(LabDocument.file_sha256 == file_sha256))
    if existing is not None:
        return existing
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
    document = LabDocument(
        id=str(uuid4()),
        stored_file_id=stored.id,
        storage_key=storage_key,
        original_filename=safe_filename(filename),
        file_sha256=file_sha256,
        media_type=media_type,
        size_bytes=size_bytes,
        status="queued",
        processing_stage="queued",
        progress_percent=0,
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


def original_bytes(db: Session, document: LabDocument, storage_dir: Path) -> bytes:
    """Load the database original, with a temporary legacy-file fallback."""

    if document.stored_file_id:
        stored = db.get(StoredFile, document.stored_file_id)
        if stored is not None:
            data = bytes(stored.content)
            if (
                stored.file_sha256 != document.file_sha256
                or stored.size_bytes != document.size_bytes
                or len(data) != document.size_bytes
                or sha256(data).hexdigest() != document.file_sha256
            ):
                raise LabFileError("original_changed")
            return data
    path = storage_dir / document.storage_key
    if not path.is_file():
        raise LabFileError("original_missing")
    data = path.read_bytes()
    if len(data) != document.size_bytes or sha256(data).hexdigest() != document.file_sha256:
        raise LabFileError("original_changed")
    return data


def preview_bytes(content: bytes, media_type: str) -> tuple[bytes, str]:
    if media_type != "image/heic":
        return content, media_type
    try:
        with Image.open(BytesIO(content)) as image:
            output = BytesIO()
            image.convert("RGB").save(output, format="JPEG", quality=92, optimize=True)
            return output.getvalue(), "image/jpeg"
    except Exception as exc:
        raise LabFileError("preview_unavailable") from exc


def backfill_stored_files(db: Session, storage_dir: Path) -> tuple[int, int]:
    """Copy and verify legacy originals into PostgreSQL without deleting rollback files."""

    copied = 0
    missing = 0
    rows = list(db.scalars(select(LabDocument).order_by(LabDocument.created_at, LabDocument.id)))
    for document in rows:
        if document.stored_file_id:
            existing_stored = db.get(StoredFile, document.stored_file_id)
            if existing_stored is not None:
                existing_content = bytes(existing_stored.content)
                if (
                    len(existing_content) != existing_stored.size_bytes
                    or existing_stored.size_bytes != document.size_bytes
                    or existing_stored.file_sha256 != document.file_sha256
                    or sha256(existing_content).hexdigest() != document.file_sha256
                ):
                    raise LabFileError("original_changed")
                db.expunge(existing_stored)
                continue
        path = storage_dir / document.storage_key
        if not path.is_file():
            missing += 1
            continue
        content = path.read_bytes()
        if len(content) != document.size_bytes or sha256(content).hexdigest() != document.file_sha256:
            raise LabFileError("original_changed")
        stored = db.scalar(
            select(StoredFile).where(StoredFile.file_sha256 == document.file_sha256)
        )
        if stored is None:
            stored = StoredFile(
                id=str(uuid4()),
                file_sha256=document.file_sha256,
                original_filename=document.original_filename,
                media_type=document.media_type,
                size_bytes=document.size_bytes,
                content=content,
            )
            db.add(stored)
            db.flush()
        document.stored_file_id = stored.id
        copied += 1
    stored_rows = select(StoredFile).order_by(StoredFile.id).execution_options(yield_per=1)
    for stored in db.scalars(stored_rows):
        content = bytes(stored.content)
        if (
            len(content) != stored.size_bytes
            or sha256(content).hexdigest() != stored.file_sha256
        ):
            raise LabFileError("original_changed")
        db.expunge(stored)
    db.commit()
    return copied, missing


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


def missing_analyte_guides(
    db: Session,
    *,
    document_id: str | None = None,
) -> list[LabAnalyte]:
    statement = select(LabAnalyte).order_by(LabAnalyte.id)
    if document_id is not None:
        statement = statement.where(
            select(LabResult.id)
            .where(
                LabResult.analyte_id == LabAnalyte.id,
                LabResult.document_id == document_id,
                LabResult.deleted.is_(False),
            )
            .exists()
        )
    return [
        analyte
        for analyte in db.scalars(statement)
        if _catalog_analyte_guide(analyte.id) is None
        and db.get(LabAnalyteGuide, analyte.id) is None
    ]


def requeue_analyte_guide_regression_documents(
    db: Session,
    storage_dir: Path,
    *,
    now: datetime | None = None,
) -> tuple[int, int, int]:
    """Retry only documents with the exact TD-001 terminal failure signature."""

    current = now or datetime.now(timezone.utc)
    jobs = list(
        db.scalars(
            select(LabProcessingJob)
            .join(LabDocument, LabDocument.id == LabProcessingJob.document_id)
            .where(
                LabDocument.status == "failed",
                LabDocument.processing_stage == "failed",
                LabDocument.progress_percent == 85,
                LabDocument.error_code == "internal",
                LabProcessingJob.status == "failed",
                LabProcessingJob.attempts == 3,
                LabProcessingJob.error_code == "internal",
            )
            .order_by(LabProcessingJob.id)
        )
    )
    requeued = skipped = 0
    for job in jobs:
        document = db.get(LabDocument, job.document_id)
        if document is None:
            skipped += 1
            continue
        try:
            original_bytes(db, document, storage_dir)
        except LabFileError:
            skipped += 1
            continue
        job.status = "pending"
        job.attempts = 0
        job.available_at = current
        job.lease_until = None
        job.error_code = None
        job.finished_at = None
        document.status = "queued"
        document.processing_stage = "queued"
        document.progress_percent = 0
        document.error_code = None
        document.completed_at = None
        document.updated_at = current
        requeued += 1
    db.commit()
    return len(jobs), requeued, skipped


def enqueue_missing_analyte_guide_jobs(db: Session) -> int:
    queued = 0
    now = datetime.now(timezone.utc)
    for analyte in missing_analyte_guides(db):
        existing = db.scalar(
            select(LabAnalyteGuideJob).where(
                LabAnalyteGuideJob.analyte_id == analyte.id
            )
        )
        if existing is not None and existing.contract_version == LAB_ANALYTE_GUIDE_PROMPT_VERSION:
            continue
        if existing is None:
            db.add(
                LabAnalyteGuideJob(
                    analyte_id=analyte.id,
                    status="pending",
                    attempts=0,
                    contract_version=LAB_ANALYTE_GUIDE_PROMPT_VERSION,
                )
            )
        else:
            existing.status = "pending"
            existing.attempts = 0
            existing.available_at = now
            existing.lease_until = None
            existing.error_code = None
            existing.finished_at = None
            existing.contract_version = LAB_ANALYTE_GUIDE_PROMPT_VERSION
        queued += 1
    db.commit()
    return queued


def persist_analyte_guides(
    db: Session,
    response: GatewayAnalyteGuideResponse,
    *,
    now: datetime | None = None,
) -> None:
    current = now or datetime.now(timezone.utc)
    for item in response.guides:
        guide = db.get(LabAnalyteGuide, item.analyte_id)
        if guide is None:
            guide = LabAnalyteGuide(
                analyte_id=item.analyte_id,
                summary=item.summary,
                why_tested=item.why_tested,
                low_meaning=item.low_meaning,
                high_meaning=item.high_meaning,
                contract_version=response.contract_version,
                model=response.model,
                created_at=current,
                updated_at=current,
            )
            db.add(guide)
        else:
            guide.summary = item.summary
            guide.why_tested = item.why_tested
            guide.low_meaning = item.low_meaning
            guide.high_meaning = item.high_meaning
            guide.contract_version = response.contract_version
            guide.model = response.model
            guide.updated_at = current
        job = db.scalar(
            select(LabAnalyteGuideJob).where(
                LabAnalyteGuideJob.analyte_id == item.analyte_id
            )
        )
        if job is not None:
            job.status = "success"
            job.lease_until = None
            job.error_code = None
            job.finished_at = current
    db.flush()


def claim_analyte_guide_jobs(
    db: Session,
    now: datetime,
    lease_seconds: int = 180,
    limit: int = 5,
) -> list[LabAnalyteGuideJob]:
    expired = list(
        db.scalars(
            select(LabAnalyteGuideJob).where(
                LabAnalyteGuideJob.status == "processing",
                LabAnalyteGuideJob.lease_until < now,
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
    jobs = list(db.scalars(
        select(LabAnalyteGuideJob)
        .where(
            LabAnalyteGuideJob.status == "pending",
            LabAnalyteGuideJob.available_at <= now,
            LabAnalyteGuideJob.contract_version == LAB_ANALYTE_GUIDE_PROMPT_VERSION,
        )
        .order_by(LabAnalyteGuideJob.id.desc())
        .with_for_update(skip_locked=True)
        .limit(limit)
    ))
    if not jobs:
        db.commit()
        return []
    for job in jobs:
        job.status = "processing"
        job.attempts += 1
        job.lease_until = now + timedelta(seconds=lease_seconds)
        job.error_code = None
    db.commit()
    return jobs


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
    source_text: str | None = None,
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
    current = date.today()
    extracted_report_date = extraction.report.observed_on
    trusted_report_date = labeled_observed_date(source_text, today=current)
    report_date = trusted_report_date or (
        extracted_report_date
        if extracted_report_date is not None and 1900 <= extracted_report_date.year <= current.year + 1
        else None
    )
    report = LabReport(
        id=str(uuid4()),
        document_id=document.id,
        observed_on=report_date,
        laboratory=extraction.report.laboratory,
        specimen=extraction.report.specimen,
    )
    db.add(report)
    db.flush()
    created = 0
    for index, item in enumerate(extraction.results):
        analyte = canonical_analyte(db, item.analyte_name, item.canonical_hint)
        observed = item.observed_on
        observed_is_plausible = observed is not None and 1900 <= observed.year <= current.year + 1
        if trusted_report_date is not None and (
            observed is None
            or observed == extracted_report_date
            or not observed_is_plausible
        ):
            observed = trusted_report_date
        elif not observed_is_plausible:
            observed = report.observed_on
        if observed is None:
            observed = report.observed_on
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


def repair_lab_observed_dates(
    db: Session,
    *,
    today: date | None = None,
) -> tuple[int, int, int]:
    """Repair model-produced dates from labelled OCR without exposing document text."""

    current = today or date.today()
    documents_changed = 0
    reports_changed = 0
    results_changed = 0
    documents = list(
        db.scalars(
            select(LabDocument)
            .where(LabDocument.extracted_text.is_not(None))
            .order_by(LabDocument.created_at, LabDocument.id)
        )
    )
    for document in documents:
        document_date = labeled_observed_date(document.extracted_text, today=current)
        reports = list(
            db.scalars(
                select(LabReport)
                .where(LabReport.document_id == document.id)
                .order_by(LabReport.created_at, LabReport.id)
            )
        )
        if not reports:
            continue
        source_texts: list[str | None] = [None] * len(reports)
        if isinstance(document.parser_pages, list):
            chunks = bounded_page_chunks(document.parser_pages, LAB_EXTRACTION_CHUNK_CHARS)
            if len(chunks) == len(reports):
                source_texts = [text for _page_from, _page_to, text in chunks]

        changed_document = False
        results = list(
            db.scalars(
                select(LabResult)
                .where(LabResult.document_id == document.id)
                .order_by(LabResult.source_index, LabResult.id)
            )
        )
        results_by_report: dict[str, list[LabResult]] = {}
        for result in results:
            if result.report_id is not None:
                results_by_report.setdefault(result.report_id, []).append(result)

        for report, source_text in zip(reports, source_texts, strict=True):
            trusted_date = labeled_observed_date(source_text, today=current) or document_date
            report_is_plausible = (
                report.observed_on is not None
                and 1900 <= report.observed_on.year <= current.year + 1
            )
            if trusted_date is None and not report_is_plausible:
                trusted_date = (
                    _unique_non_birth_date(source_text, today=current)
                    or _unique_non_birth_date(document.extracted_text, today=current)
                )
            if trusted_date is None:
                continue
            previous_date = report.observed_on
            if previous_date != trusted_date:
                report.observed_on = trusted_date
                reports_changed += 1
                changed_document = True
            for result in results_by_report.get(report.id, []):
                result_is_plausible = (
                    result.observed_on is not None
                    and 1900 <= result.observed_on.year <= current.year + 1
                )
                if result.verification_status == "corrected":
                    continue
                if (
                    result.observed_on is None
                    or result.observed_on == previous_date
                    or not result_is_plausible
                ) and result.observed_on != trusted_date:
                    result.observed_on = trusted_date
                    results_changed += 1
                    changed_document = True
        if changed_document:
            documents_changed += 1
    db.commit()
    return documents_changed, reports_changed, results_changed


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
        document.processing_stage = "reading"
        document.progress_percent = 10
        document.error_code = None
    db.commit()
    return job
