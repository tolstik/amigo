from __future__ import annotations

from datetime import date
from decimal import Decimal
from hashlib import sha256
from io import BytesIO

import fitz
from PIL import Image
import pytest

from app.lab_contracts import ExtractedLabReport, ExtractedLabResult, LabExtraction
from app.auth_models import UserProfile
from app.config import Settings
from app.lab_models import LabDocument, LabReferenceRange, LabResult, StoredFile
from app.lab_parser import MAX_COORDINATE_BLOCKS, ParserError, _ocr, parse_document
from app.labs_api import ResultCreate, ResultPatch, create_result, patch_result
from app.labs import (
    LAB_EXTRACTION_CHUNK_CHARS,
    LAB_RETRIEVAL_CHUNK_CHARS,
    LabFileError,
    backfill_stored_files,
    bounded_page_chunks,
    calculate_status,
    detect_media_type,
    enqueue_document,
    original_bytes,
    persist_extraction,
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
    assert len(retrieval) > len(extraction)
    assert all(len(text) <= LAB_EXTRACTION_CHUNK_CHARS for _, _, text in extraction)
    assert all(len(text) <= LAB_RETRIEVAL_CHUNK_CHARS for _, _, text in retrieval)
    assert all((page_from, page_to) == (7, 7) for page_from, page_to, _ in extraction)


def test_status_is_deterministic_and_comparators_remain_indeterminate():
    assert calculate_status(Decimal("5.1"), None, None, Decimal("3.9"), Decimal("5.6"), None) == "within_reference"
    assert calculate_status(Decimal("3.1"), None, "=", Decimal("3.9"), Decimal("5.6"), None) == "below_reference"
    assert calculate_status(Decimal("7.0"), None, None, Decimal("3.9"), Decimal("5.6"), None) == "above_reference"
    assert calculate_status(Decimal("3.0"), None, "<", Decimal("3.9"), Decimal("5.6"), None) == "indeterminate"
    assert calculate_status(None, "negative", None, None, None, "not detected") == "within_reference"


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
