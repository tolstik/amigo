from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json

import fitz
import pytest
from fastapi import HTTPException

from app.config import Settings
from app.feature_models import DoctorReportSnapshot
from app.lab_models import LabDocument, LabResult
from app.reports_api import (
    DoctorReportCreate,
    build_doctor_report_payload,
    create_doctor_report,
    download_doctor_report,
    get_doctor_report,
    render_doctor_report,
)
from app.worker import cleanup_doctor_reports


def test_report_payload_includes_only_confirmed_structured_labs(db):
    document = LabDocument(
        id="11111111-1111-4111-8111-111111111111",
        storage_key="root-only-random-key",
        original_filename="patient-private-name.pdf",
        file_sha256="a" * 64,
        media_type="application/pdf",
        size_bytes=100,
        status="complete",
        processing_stage="complete",
        progress_percent=100,
        verified=True,
    )
    db.add(document)
    db.flush()
    db.add_all(
        [
            LabResult(
                id="verified-result",
                document_id=document.id,
                source_index=0,
                analyte_name="Ферритин",
                value_numeric=Decimal("45"),
                unit="нг/мл",
                observed_on=date(2026, 8, 24),
                status="within_reference",
                verification_status="verified",
                reference_source="laboratory",
                deleted=False,
            ),
            LabResult(
                id="unverified-result",
                document_id=document.id,
                source_index=1,
                analyte_name="Скрытый черновик",
                value_numeric=Decimal("1"),
                observed_on=date(2026, 8, 24),
                status="indeterminate",
                verification_status="unverified",
                reference_source="none",
                deleted=False,
            ),
        ]
    )
    db.commit()

    payload = build_doctor_report_payload(
        db,
        Settings(database_url="sqlite+pysqlite:///:memory:"),
        DoctorReportCreate(period="30d", sections=["labs"]),
        datetime(2026, 8, 25, 10, tzinfo=timezone.utc),
    )
    encoded = json.dumps(payload, ensure_ascii=False)
    assert "Ферритин" in encoded
    assert "Скрытый черновик" not in encoded
    assert "patient-private-name.pdf" not in encoded
    assert "root-only-random-key" not in encoded


def test_pdf_renders_sleep_scale_in_hours_and_stays_bounded():
    payload = {
        "meta": {
            "created_at": "2026-08-25T10:00:00+00:00",
            "period": "30d",
            "from": "2026-07-27",
            "to": "2026-08-25",
            "timezone": "Europe/Moscow",
        },
        "sections": {
            "recovery": {
                "daily": [
                    {"date": "2026-08-24", "sleep_minutes": 420},
                    {"date": "2026-08-25", "sleep_minutes": 450},
                ]
            },
            "activity": {"daily": [{"date": "2026-08-25", "steps": None}]},
        },
    }

    rendered = render_doctor_report(payload)
    assert len(rendered) < 10 * 1024 * 1024
    with fitz.open(stream=rendered, filetype="pdf") as document:
        assert document.page_count <= 40
        text = "\n".join(page.get_text() for page in document)
    assert "Продолжительность сна" in text
    assert "часы" in text
    assert "Шаги · только Xiaomi Cloud" in text


def test_pdf_rejects_content_that_would_exceed_page_bound():
    payload = {
        "meta": {"from": "2026-07-27", "to": "2026-08-25"},
        "sections": {
            "studies": [
                {
                    "modality": "other",
                    "observed_on": "2026-08-25",
                    "findings": ["длинное наблюдение " * 30_000],
                    "conclusion": None,
                }
            ]
        },
    }

    with pytest.raises(HTTPException) as error:
        render_doctor_report(payload)
    assert error.value.detail == "report_too_many_pages"


def test_report_snapshot_does_not_drift_and_expires_after_24_hours(db):
    document = LabDocument(
        id="22222222-2222-4222-8222-222222222222",
        storage_key="immutable-report-key",
        original_filename="private.pdf",
        file_sha256="b" * 64,
        media_type="application/pdf",
        size_bytes=100,
        status="complete",
        processing_stage="complete",
        progress_percent=100,
        verified=True,
    )
    result = LabResult(
        id="immutable-result",
        document_id=document.id,
        source_index=0,
        analyte_name="Ферритин",
        value_numeric=Decimal("45"),
        unit="нг/мл",
        observed_on=date.today(),
        status="within_reference",
        verification_status="verified",
        reference_source="laboratory",
        deleted=False,
    )
    db.add_all([document, result])
    db.commit()
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")

    created = create_doctor_report(
        DoctorReportCreate(period="30d", sections=["labs"]),
        None,  # type: ignore[arg-type]
        db,
        settings,
    )
    report_id = created["id"]
    result.value_numeric = Decimal("99")
    db.commit()

    persisted = get_doctor_report(report_id, db)
    assert persisted["preview"]["sections"]["labs"][0]["value"] == "45 нг/мл"
    downloaded = download_doctor_report(report_id, db)
    assert len(downloaded.body) == created["size_bytes"]

    row = db.get(DoctorReportSnapshot, report_id)
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    with pytest.raises(HTTPException) as expired:
        get_doctor_report(report_id, db)
    assert expired.value.status_code == 410
    assert cleanup_doctor_reports(db) == 1
    assert db.get(DoctorReportSnapshot, report_id) is None


def test_default_report_sections_persist_as_json_without_source_data(db):
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    created = create_doctor_report(
        DoctorReportCreate(),
        None,  # type: ignore[arg-type]
        db,
        settings,
    )

    assert created["options"]["sections"] == [
        "summary",
        "weight",
        "pressure",
        "activity",
        "recovery",
        "labs",
        "studies",
    ]
    assert created["page_count"] >= 1
    assert created["size_bytes"] > 0
