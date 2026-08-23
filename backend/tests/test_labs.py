from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
from io import BytesIO

import fitz
from PIL import Image
import pytest
from sqlalchemy import select

from app.lab_contracts import (
    ExtractedLabReport,
    ExtractedLabResult,
    GatewayAnalyteGuideResponse,
    GatewayLabResponse,
    LAB_ANALYTE_GUIDE_PROMPT_VERSION,
    LabExtraction,
)
from app.auth_models import UserProfile
from app.config import Settings
from app.lab_models import (
    LabAnalyte,
    LabAnalyteGuide,
    LabAnalyteGuideJob,
    LabDocument,
    LabProcessingJob,
    LabReferenceRange,
    LabReport,
    LabResult,
    StoredFile,
)
from app.lab_parser import MAX_COORDINATE_BLOCKS, ParserError, _ocr, parse_document
from app.lab_assistant_worker import WorkError, process_lab_job
from app.labs_api import ResultCreate, ResultPatch, analyte_history, create_result, patch_result
from app.labs import (
    LAB_EXTRACTION_CHUNK_CHARS,
    LAB_RETRIEVAL_CHUNK_CHARS,
    LabFileError,
    backfill_stored_files,
    bounded_page_chunks,
    calculate_status,
    claim_analyte_guide_jobs,
    detect_media_type,
    enqueue_document,
    enqueue_missing_analyte_guide_jobs,
    missing_analyte_guides,
    original_bytes,
    persist_analyte_guides,
    persist_extraction,
    repair_lab_observed_dates,
    requeue_analyte_guide_regression_documents,
    requeue_extraction_timeout_documents,
    seed_reference_catalog,
)
from app.studies import enqueue_study, structure_study_text


def test_magic_detection_rejects_extension_spoofing():
    assert detect_media_type(b"%PDF-1.7\n", "report.pdf") == "application/pdf"
    assert detect_media_type(b"\x89PNG\r\n\x1a\nrest", "report.png") == "image/png"
    assert detect_media_type(b"\xff\xd8\xff\xe0rest", "report.jpeg") == "image/jpeg"
    assert detect_media_type(b"\x00\x00\x00\x18ftypheicrest", "report.heic") == "image/heic"
    try:
        detect_media_type(b"%PDF-1.7\n", "report.jpg")
    except LabFileError as exc:
        assert str(exc) == "file_type_mismatch"
    else:
        raise AssertionError("spoofed extension was accepted")


def test_originals_are_database_owned_and_deduplicated_across_document_types(db, tmp_path):
    content = b"%PDF-1.7\nverified fixture"
    digest = sha256(content).hexdigest()
    laboratory = enqueue_document(
        db,
        storage_key="laboratory.bin",
        filename="laboratory.pdf",
        file_sha256=digest,
        media_type="application/pdf",
        size_bytes=len(content),
        content=content,
    )
    study = enqueue_study(
        db,
        storage_key="study.bin",
        filename="study.pdf",
        file_sha256=digest,
        media_type="application/pdf",
        size_bytes=len(content),
        content=content,
        modality="mri",
        title="МРТ",
        observed_on=date(2026, 8, 20),
    )

    assert laboratory.stored_file_id == study.stored_file_id
    assert db.query(StoredFile).count() == 1
    assert original_bytes(db, laboratory, tmp_path) == content
    assert backfill_stored_files(db, tmp_path) == (0, 0)


def test_study_text_structure_preserves_report_facts_without_interpretation():
    findings, conclusion = structure_study_text(
        "Пациент: Иванов Иван.\n\nОписание: Размер без изменений.\n\nКонтуры ровные.\n\nЗаключение: Признаков патологии не выявлено."
    )

    assert findings == ["Описание: Размер без изменений.", "Контуры ровные."]
    assert conclusion == "Признаков патологии не выявлено."
    assert "Иванов" not in " ".join(findings)


def test_text_pdf_is_extracted_without_codex():
    document = fitz.open()
    page = document.new_page()
    page.insert_text(
        (72, 72),
        "Glucose 5.1 mmol/L reference interval 3.9-5.6 mmol/L",
    )
    data = document.tobytes()
    document.close()
    parsed = parse_document(data, "report.pdf")
    assert parsed["page_count"] == 1
    assert "Glucose 5.1" in parsed["text"]
    assert parsed["pages"][0]["blocks"]


def test_image_parser_uses_bounded_ocr(monkeypatch):
    image = Image.new("RGB", (200, 80), "white")
    output = BytesIO()
    image.save(output, format="PNG")
    monkeypatch.setattr("app.lab_parser._ocr", lambda _image: ("Hemoglobin 145 g/L", []))
    parsed = parse_document(output.getvalue(), "report.png")
    assert parsed["text"] == "Hemoglobin 145 g/L"


def test_ocr_rejects_large_dimensions_before_transpose(monkeypatch):
    class OversizedImage:
        width = 10_000
        height = 4_001

    monkeypatch.setattr(
        "app.lab_parser.ImageOps.exif_transpose",
        lambda _image: (_ for _ in ()).throw(AssertionError("transpose must not run")),
    )

    with pytest.raises(ParserError, match="image_too_large"):
        _ocr(OversizedImage())  # type: ignore[arg-type]


def test_image_decompression_bomb_has_stable_error(monkeypatch):
    monkeypatch.setattr(
        "app.lab_parser.Image.open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            Image.DecompressionBombError("private decoder detail")
        ),
    )

    with pytest.raises(ParserError, match="^image_too_large$"):
        parse_document(b"\x89PNG\r\n\x1a\nsynthetic", "large.png")


def test_pdf_raster_size_is_checked_before_pixmap_allocation():
    document = fitz.open()
    document.new_page(width=10_000, height=3_000)
    data = document.tobytes()
    document.close()

    with pytest.raises(ParserError, match="image_too_large"):
        parse_document(data, "large.pdf")


def test_ocr_coordinate_blocks_obey_request_limit(monkeypatch):
    image = Image.new("RGB", (20, 20), "white")
    monkeypatch.setattr("app.lab_parser.pytesseract.image_to_string", lambda *_args, **_kwargs: "text")
    monkeypatch.setattr(
        "app.lab_parser.pytesseract.image_to_data",
        lambda *_args, **_kwargs: {
            "text": ["one", "two", "three"],
            "conf": ["90", "90", "90"],
            "left": [0, 1, 2],
            "top": [0, 1, 2],
            "width": [1, 1, 1],
            "height": [1, 1, 1],
        },
    )

    _text, blocks = _ocr(image, block_limit=2)

    assert len(blocks) == 2
    assert len(blocks) <= MAX_COORDINATE_BLOCKS


def test_long_single_page_is_split_for_extraction_and_retrieval():
    pages = [{"page": 7, "text": "word " * 36_000}]

    extraction = bounded_page_chunks(pages, LAB_EXTRACTION_CHUNK_CHARS)
    retrieval = bounded_page_chunks(pages, LAB_RETRIEVAL_CHUNK_CHARS)

    assert len(extraction) > 1
    assert len(extraction) > len(retrieval)
    assert all(len(text) <= LAB_EXTRACTION_CHUNK_CHARS for _, _, text in extraction)
    assert all(len(text) <= LAB_RETRIEVAL_CHUNK_CHARS for _, _, text in retrieval)
    assert all((page_from, page_to) == (7, 7) for page_from, page_to, _ in extraction)


def test_status_is_deterministic_and_comparators_remain_indeterminate():
    assert calculate_status(Decimal("5.1"), None, None, Decimal("3.9"), Decimal("5.6"), None) == "within_reference"
    assert calculate_status(Decimal("3.1"), None, "=", Decimal("3.9"), Decimal("5.6"), None) == "below_reference"
    assert calculate_status(Decimal("7.0"), None, None, Decimal("3.9"), Decimal("5.6"), None) == "above_reference"
    assert calculate_status(Decimal("3.0"), None, "<", Decimal("3.9"), Decimal("5.6"), None) == "indeterminate"
    assert calculate_status(None, "negative", None, None, None, "not detected") == "within_reference"


def test_analyte_history_includes_catalog_and_generated_guides(db):
    known = analyte_history("leukocytes", db)
    db.add(LabAnalyte(id="custom-marker", display_name="Особый маркер", aliases=[]))
    db.flush()
    persist_analyte_guides(
        db,
        GatewayAnalyteGuideResponse(
            guides=[{
                "analyte_id": "custom-marker",
                "summary": "Особый маркер отражает лабораторно измеряемый биологический процесс.",
                "why_tested": "Исследование назначают для уточнения связанного биологического процесса.",
                "low_meaning": "Снижение сопоставляют с методом, материалом и другими результатами исследования.",
                "high_meaning": "Повышение сопоставляют с методом, материалом и другими результатами исследования.",
            }]
        ),
        now=datetime(2026, 8, 21, tzinfo=timezone.utc),
    )
    db.commit()
    custom = analyte_history("custom-marker", db)

    assert "иммунной системы" in known["guide"]["summary"]
    assert "инфекц" in known["guide"]["high_meaning"]
    assert known["guide"]["version"] == "2026.08-v1"
    assert known["guide"]["source"] == "catalog"
    assert "биологический процесс" in custom["guide"]["summary"]
    assert custom["guide"]["source"] == "ai_generated"


def test_document_worker_completes_known_and_unknown_analytes_with_one_guide(db, tmp_path):
    class Gateway:
        def __init__(self):
            self.guide_requests = []

        def parse(self, _document, _content):
            return {
                "text": "Глюкоза 5.1; Новый маркер 7; Новый маркер 8",
                "page_count": 1,
                "pages": [{"page": 1, "text": "laboratory facts", "blocks": []}],
            }

        def extract(self, _request):
            return GatewayLabResponse(
                extraction=LabExtraction(
                    report=ExtractedLabReport(observed_on=date(2026, 8, 21)),
                    results=[
                        ExtractedLabResult(
                            analyte_name="Глюкоза",
                            canonical_hint="glucose",
                            value_numeric=Decimal("5.1"),
                            unit="mmol/L",
                        ),
                        ExtractedLabResult(
                            analyte_name="Новый маркер",
                            canonical_hint="custom-flow-marker",
                            value_numeric=Decimal("7"),
                            unit="U/L",
                        ),
                        ExtractedLabResult(
                            analyte_name="Новый маркер",
                            canonical_hint="custom-flow-marker",
                            value_numeric=Decimal("8"),
                            unit="U/L",
                        ),
                    ],
                )
            )

        def guides(self, request):
            self.guide_requests.append(request)
            return GatewayAnalyteGuideResponse(
                guides=[
                    {
                        "analyte_id": item.analyte_id,
                        "summary": "Новый маркер отражает лабораторно измеряемый биологический процесс.",
                        "why_tested": "Исследование используют для уточнения связанного биологического процесса.",
                        "low_meaning": "Снижение сопоставляют с методом, материалом и другими результатами.",
                        "high_meaning": "Повышение сопоставляют с методом, материалом и другими результатами.",
                    }
                    for item in request.analytes
                ]
            )

    content = b"%PDF-1.7\nsynthetic laboratory fixture"
    document = enqueue_document(
        db,
        storage_key="worker-flow.bin",
        filename="worker-flow.pdf",
        file_sha256=sha256(content).hexdigest(),
        media_type="application/pdf",
        size_bytes=len(content),
        content=content,
    )
    gateway = Gateway()
    now = datetime.now(timezone.utc) + timedelta(minutes=1)

    assert process_lab_job(
        db,
        Settings(ai_enabled=False, lab_storage_dir=tmp_path),
        gateway,
        now,
    ) is True

    db.refresh(document)
    job = db.scalar(select(LabProcessingJob).where(LabProcessingJob.document_id == document.id))
    assert document.status == "complete"
    assert document.processing_stage == "complete"
    assert document.progress_percent == 100
    assert job.status == "success"
    assert len(gateway.guide_requests) == 1
    assert [item.analyte_id for item in gateway.guide_requests[0].analytes] == [
        "custom-flow-marker"
    ]
    assert [item.id for item in missing_analyte_guides(db, document_id=document.id)] == []
    guides = db.query(LabAnalyteGuide).all()
    assert [guide.analyte_id for guide in guides] == ["custom-flow-marker"]


def test_document_worker_splits_dense_extraction_and_bounds_timeout_recovery(db, tmp_path):
    class Gateway:
        def __init__(self):
            self.requests = []

        def parse(self, _document, _content):
            text = "\n".join(f"Marker {index} {index}.0 U/L" for index in range(400))
            return {
                "text": text,
                "page_count": 1,
                "pages": [{"page": 1, "text": text, "blocks": []}],
            }

        def extract(self, request):
            self.requests.append(request)
            if len(request.text) > 1_800:
                raise WorkError("timeout")
            return GatewayLabResponse(
                extraction=LabExtraction(
                    report=ExtractedLabReport(observed_on=date(2026, 8, 21)),
                    results=[],
                )
            )

        def guides(self, _request):
            raise AssertionError("empty extraction must not request guides")

    content = b"%PDF-1.7\ndense synthetic laboratory fixture"
    document = enqueue_document(
        db,
        storage_key="dense-worker-flow.bin",
        filename="dense-worker-flow.pdf",
        file_sha256=sha256(content).hexdigest(),
        media_type="application/pdf",
        size_bytes=len(content),
        content=content,
    )
    gateway = Gateway()

    assert process_lab_job(
        db,
        Settings(ai_enabled=False, lab_storage_dir=tmp_path),
        gateway,
        datetime.now(timezone.utc) + timedelta(minutes=1),
    ) is True

    db.refresh(document)
    job = db.scalar(select(LabProcessingJob).where(LabProcessingJob.document_id == document.id))
    assert document.status == "complete"
    assert job.status == "success"
    assert any(len(request.text) > 1_800 for request in gateway.requests)
    assert all(len(request.text) <= LAB_EXTRACTION_CHUNK_CHARS for request in gateway.requests)
    assert max(request.chunk_index for request in gateway.requests) < 8
    assert len(gateway.requests) < 16


def test_td001_retry_is_exact_and_requires_an_intact_original(db, tmp_path):
    def document(name: str, content: bytes):
        return enqueue_document(
            db,
            storage_key=f"{name}.bin",
            filename=f"{name}.pdf",
            file_sha256=sha256(content).hexdigest(),
            media_type="application/pdf",
            size_bytes=len(content),
            content=content,
        )

    affected = document("affected", b"%PDF-1.7\naffected")
    corrupted = document("corrupted", b"%PDF-1.7\ncorrupted")
    unrelated = document("unrelated", b"%PDF-1.7\nunrelated")
    for row in (affected, corrupted, unrelated):
        job = db.scalar(select(LabProcessingJob).where(LabProcessingJob.document_id == row.id))
        row.status = "failed"
        row.processing_stage = "failed"
        row.progress_percent = 85
        row.error_code = "internal"
        job.status = "failed"
        job.attempts = 3
        job.error_code = "internal"
    unrelated.progress_percent = 40
    corrupted.stored_file.content = b"changed"
    db.commit()

    assert requeue_analyte_guide_regression_documents(
        db,
        tmp_path,
        now=datetime(2026, 8, 23, 13, tzinfo=timezone.utc),
    ) == (2, 1, 1)

    db.refresh(affected)
    db.refresh(corrupted)
    db.refresh(unrelated)
    affected_job = db.scalar(
        select(LabProcessingJob).where(LabProcessingJob.document_id == affected.id)
    )
    assert (affected.status, affected.processing_stage, affected.progress_percent) == (
        "queued",
        "queued",
        0,
    )
    assert (affected_job.status, affected_job.attempts, affected_job.error_code) == (
        "pending",
        0,
        None,
    )
    assert corrupted.status == "failed"
    assert unrelated.status == "failed"


def test_extraction_timeout_retry_is_exact_and_requires_an_intact_original(db, tmp_path):
    def document(name: str, content: bytes):
        return enqueue_document(
            db,
            storage_key=f"{name}.bin",
            filename=f"{name}.pdf",
            file_sha256=sha256(content).hexdigest(),
            media_type="application/pdf",
            size_bytes=len(content),
            content=content,
        )

    affected = document("timeout-affected", b"%PDF-1.7\naffected")
    corrupted = document("timeout-corrupted", b"%PDF-1.7\ncorrupted")
    unrelated = document("timeout-unrelated", b"%PDF-1.7\nunrelated")
    for row in (affected, corrupted, unrelated):
        job = db.scalar(select(LabProcessingJob).where(LabProcessingJob.document_id == row.id))
        row.status = "failed"
        row.processing_stage = "failed"
        row.progress_percent = 40
        row.error_code = "timeout"
        job.status = "failed"
        job.attempts = 3
        job.error_code = "timeout"
    unrelated.progress_percent = 85
    corrupted.stored_file.content = b"changed"
    db.commit()

    assert requeue_extraction_timeout_documents(
        db,
        tmp_path,
        now=datetime(2026, 8, 23, 17, tzinfo=timezone.utc),
    ) == (2, 1, 1)

    db.refresh(affected)
    db.refresh(corrupted)
    db.refresh(unrelated)
    affected_job = db.scalar(
        select(LabProcessingJob).where(LabProcessingJob.document_id == affected.id)
    )
    assert (affected.status, affected.processing_stage, affected.progress_percent) == (
        "queued",
        "queued",
        0,
    )
    assert (affected_job.status, affected_job.attempts, affected_job.error_code) == (
        "pending",
        0,
        None,
    )
    assert corrupted.status == "failed"
    assert unrelated.status == "failed"


def test_existing_unknown_analytes_are_enqueued_once_for_bounded_backfill(db):
    db.add(LabAnalyte(id="custom-backfill", display_name="Новый маркер", aliases=[]))
    db.commit()

    assert enqueue_missing_analyte_guide_jobs(db) == 1
    assert enqueue_missing_analyte_guide_jobs(db) == 0

    job = db.query(LabAnalyteGuideJob).one()
    assert job.analyte_id == "custom-backfill"
    assert job.status == "pending"
    assert job.contract_version == LAB_ANALYTE_GUIDE_PROMPT_VERSION


def test_new_guide_contract_retries_a_terminal_old_job_only_once(db):
    db.add(LabAnalyte(id="custom-retry", display_name="Повторяемый маркер", aliases=[]))
    db.add(
        LabAnalyteGuideJob(
            analyte_id="custom-retry",
            status="failed",
            attempts=3,
            error_code="timeout",
            contract_version="amigo-lab-analyte-guide-v1",
        )
    )
    db.commit()

    assert enqueue_missing_analyte_guide_jobs(db) == 1
    job = db.query(LabAnalyteGuideJob).one()
    assert job.status == "pending"
    assert job.attempts == 0
    assert job.error_code is None
    assert job.contract_version == LAB_ANALYTE_GUIDE_PROMPT_VERSION

    job.status = "failed"
    job.attempts = 3
    job.error_code = "timeout"
    db.commit()

    assert enqueue_missing_analyte_guide_jobs(db) == 0
    db.refresh(job)
    assert job.status == "failed"
    assert job.attempts == 3


def test_guide_backfill_claims_at_most_five_newest_jobs(db):
    now = datetime(2026, 8, 21, 10, tzinfo=timezone.utc)
    for index in range(7):
        analyte_id = f"custom-queue-{index}"
        db.add(LabAnalyte(id=analyte_id, display_name=f"Маркер {index}", aliases=[]))
        db.add(
            LabAnalyteGuideJob(
                analyte_id=analyte_id,
                status="pending",
                attempts=0,
                available_at=now,
                contract_version=LAB_ANALYTE_GUIDE_PROMPT_VERSION,
            )
        )
    db.commit()

    claimed = claim_analyte_guide_jobs(db, now)

    assert len(claimed) == 5
    assert [job.analyte_id for job in claimed] == [
        "custom-queue-6",
        "custom-queue-5",
        "custom-queue-4",
        "custom-queue-3",
        "custom-queue-2",
    ]


def test_report_range_overrides_catalog_and_results_publish_unverified(db):
    seed_reference_catalog(db)
    db.add(UserProfile(id=1, birth_date=date(1990, 1, 1), reference_sex="male", height_cm=176))
    document = LabDocument(
        id="00000000-0000-0000-0000-000000000001",
        storage_key="test.bin",
        original_filename="report.pdf",
        file_sha256="a" * 64,
        media_type="application/pdf",
        size_bytes=100,
        status="processing",
        verified=False,
    )
    db.add(document)
    db.flush()
    extraction = LabExtraction(
        report=ExtractedLabReport(observed_on=date(2026, 8, 20), specimen="serum"),
        results=[
            ExtractedLabResult(
                analyte_name="Глюкоза",
                canonical_hint="glucose",
                value_numeric=Decimal("5.7"),
                unit="mmol/L",
                reference_low=Decimal("4.0"),
                reference_high=Decimal("6.0"),
                source_page=1,
            )
        ],
    )
    assert persist_extraction(
        db, document, extraction, chunk_index=0, model="gpt-5.6-sol",
        contract_version="amigo-lab-extraction-v1", source_offset=0,
    ) == 1
    db.commit()
    row = db.query(LabResult).one()
    assert row.analyte_id == "glucose"
    assert row.reference_source == "laboratory"
    assert row.status == "within_reference"
    assert row.verification_status == "unverified"


def test_labelled_ocr_date_overrides_implausible_model_date(db):
    document = LabDocument(
        id="00000000-0000-0000-0000-000000000011",
        storage_key="dated.bin",
        original_filename="dated.pdf",
        file_sha256="d" * 64,
        media_type="application/pdf",
        size_bytes=100,
        status="processing",
        verified=False,
    )
    db.add(document)
    db.flush()
    extraction = LabExtraction(
        report=ExtractedLabReport(observed_on=date(2904, 2, 25)),
        results=[ExtractedLabResult(analyte_name="Глюкоза", value_numeric=Decimal("5.1"))],
    )

    persist_extraction(
        db,
        document,
        extraction,
        chunk_index=0,
        model="gpt-5.6-sol",
        contract_version="amigo-lab-extraction-v1",
        source_offset=0,
        source_text="Аллергология\nДата выполнения исследования: 27.04.2025\nДата рождения: 01.01.1990",
    )
    db.commit()

    assert db.query(LabReport).one().observed_on == date(2025, 4, 27)
    assert db.query(LabResult).one().observed_on == date(2025, 4, 27)


def test_existing_lab_dates_are_repaired_for_all_unambiguous_documents(db):
    expected_dates = (date(2025, 4, 27), date(2024, 11, 8))
    for index, expected in enumerate(expected_dates, start=20):
        document = LabDocument(
            id=f"00000000-0000-0000-0000-{index:012d}",
            storage_key=f"dated-{index}.bin",
            original_filename=f"dated-{index}.pdf",
            file_sha256=f"{index:064x}",
            media_type="application/pdf",
            size_bytes=100,
            status="complete",
            verified=False,
            extracted_text=f"Дата исследования: {expected.strftime('%d.%m.%Y')}",
            parser_pages=[
                {"page": 1, "text": f"Дата исследования: {expected.strftime('%d.%m.%Y')}"}
            ],
        )
        db.add(document)
        db.flush()
        persist_extraction(
            db,
            document,
            LabExtraction(
                report=ExtractedLabReport(observed_on=date(2904, 2, 25)),
                results=[ExtractedLabResult(analyte_name=f"Показатель {index}", value_numeric=Decimal("1"))],
            ),
            chunk_index=0,
            model="gpt-5.6-sol",
            contract_version="amigo-lab-extraction-v1",
            source_offset=0,
        )
    db.commit()

    assert repair_lab_observed_dates(db, today=date(2026, 8, 21)) == (2, 2, 2)
    assert [row.observed_on for row in db.query(LabReport).order_by(LabReport.created_at)] == list(expected_dates)
    assert [row.observed_on for row in db.query(LabResult).order_by(LabResult.document_id)] == list(expected_dates)
    assert repair_lab_observed_dates(db, today=date(2026, 8, 21)) == (0, 0, 0)


def test_user_can_add_missing_result_and_analyte_edit_rebinds_history(db, monkeypatch):
    monkeypatch.setattr("app.labs_api.enqueue_current_analysis", lambda *_args, **_kwargs: None)
    document = LabDocument(
        id="00000000-0000-0000-0000-000000000003",
        storage_key="manual.bin",
        original_filename="manual.pdf",
        file_sha256="c" * 64,
        media_type="application/pdf",
        size_bytes=100,
        status="complete",
        verified=True,
    )
    db.add(document)
    db.commit()
    configured = Settings(ai_enabled=False)

    created = create_result(
        document.id,
        ResultCreate(
            analyte_name="Глюкоза",
            value_numeric=Decimal("5.1"),
            unit="mmol/L",
            observed_on=date(2026, 8, 20),
            specimen="serum",
            reference_low=Decimal("3.9"),
            reference_high=Decimal("5.6"),
        ),
        None,  # type: ignore[arg-type]
        db,
        configured,
    )
    row = db.get(LabResult, created["id"])
    assert row is not None
    assert row.analyte_id == "glucose"
    assert row.reference_source == "user"
    assert row.status == "within_reference"
    assert row.verification_status == "corrected"
    assert document.verified is False

    row.reference_source = "catalog"
    db.add(
        LabReferenceRange(
            catalog_version="test",
            analyte_id="glucose",
            specimen="serum",
            unit="mmol/L",
            reference_sex="any",
            low=Decimal("3.9"),
            high=Decimal("5.6"),
            source="Test-only verified fixture",
            reviewed_on=date(2026, 8, 20),
        )
    )
    db.commit()

    patched = patch_result(
        row.id,
        ResultPatch(analyte_name="Ферритин"),
        None,  # type: ignore[arg-type]
        db,
        configured,
    )

    assert patched["analyte_id"] == "ferritin"
    assert patched["reference_source"] == "none"
    assert patched["reference_low"] is None
    assert patched["reference_high"] is None
