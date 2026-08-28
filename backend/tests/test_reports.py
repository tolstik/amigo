from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json

import fitz
import pytest
from fastapi import HTTPException

from app.config import Settings
from app.feature_models import DoctorReportSnapshot
from app.lab_models import LabDocument, LabReport, LabResult
from app.reports_api import (
    DoctorReportCreate,
    build_doctor_report_payload,
    create_doctor_report,
    download_doctor_report,
    download_doctor_report_html,
    get_doctor_report,
    render_doctor_report,
    render_doctor_report_html,
)
from app.worker import cleanup_doctor_reports


def test_report_payload_includes_structured_labs_with_verification_status(db):
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
    assert "Скрытый черновик" in encoded
    assert next(item for item in payload["sections"]["labs"] if item["analyte"] == "Скрытый черновик")["verification_status"] == "unverified"
    assert "patient-private-name.pdf" not in encoded
    assert "root-only-random-key" not in encoded


def test_report_export_keeps_unverified_labs_and_raw_weight_points():
    payload = {
        "meta": {"from": "2026-08-01", "to": "2026-08-28", "labs_unverified_count": 1},
        "sections": {
            "weight": {
                "raw": [
                    {"measured_at": "2026-08-20T06:00:00Z", "value": 126.8},
                    {"measured_at": "2026-08-20T18:00:00Z", "value": 126.4},
                ],
                "points": [{"measured_at": "2026-08-20", "weight_kg": 126.6, "planned_kg": 126.5}],
            },
            "labs": [{"analyte": "Ферритин", "value": "42 нг/мл", "observed_on": "2026-08-20", "status": "within_reference", "verification_status": "unverified"}],
        },
    }
    html = render_doctor_report_html(payload).decode("utf-8")
    assert "Реальные измерения" in html
    assert "Не проверено" in html
    assert "126.5" not in html


def test_report_export_never_substitutes_daily_weight_for_raw_measurements():
    payload = {
        "meta": {"from": "2026-08-01", "to": "2026-08-28"},
        "sections": {
            "weight": {
                "raw": [],
                "points": [{"measured_at": "2026-08-20", "weight_kg": 126.6, "planned_kg": 126.5}],
            },
        },
    }
    html = render_doctor_report_html(payload).decode("utf-8")
    assert "126.6" not in html
    assert "126.5" not in html


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
                    {"date": "2026-08-24", "sleep_minutes": 420, "minimum_heart_rate_bpm": 48, "average_heart_rate_bpm": 64, "maximum_heart_rate_bpm": 102},
                    {"date": "2026-08-25", "sleep_minutes": 450, "minimum_heart_rate_bpm": 50, "average_heart_rate_bpm": 66, "maximum_heart_rate_bpm": 98},
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
    assert "Пульс с часов" in text
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


def test_html_download_is_immutable_and_bounded(db):
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    created = create_doctor_report(
        DoctorReportCreate(period="30d", sections=["summary", "circumference"]),
        None,  # type: ignore[arg-type]
        db,
        settings,
    )
    response = download_doctor_report_html(created["id"], db, settings)
    assert response.media_type == "text/html; charset=utf-8"
    assert response.headers["content-disposition"].endswith('amigo-doctor-report.html"')
    assert len(response.body) == created["html_size_bytes"]
    assert b"<!doctype html>" in response.body.lower()


def test_html_uses_bundled_echarts_runtime_when_available(tmp_path):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "echarts.min.js").write_text("window.echarts={init:function(){}};", encoding="utf-8")
    rendered = render_doctor_report_html(
        {
            "meta": {"from": "2026-08-01", "to": "2026-08-28"},
            "sections": {
                "pressure": {"points": [{"measured_at": "2026-08-20", "systolic": 120, "diastolic": 80, "pulse": 65}]},
                "recovery": {"daily": [{"date": "2026-08-20", "minimum_heart_rate_bpm": 48, "average_heart_rate_bpm": 64, "maximum_heart_rate_bpm": 102}]},
            },
        },
        static_dir,
    ).decode("utf-8")
    assert "echarts.init" in rendered
    assert '<div class="echart" id="chart-pressure"' in rendered
    assert '<div class="echart" id="chart-watch-heart-rate"' in rendered
    pressure_script = rendered.split("const pressure =", 1)[1].split("const activity =", 1)[0]
    assert 'line("Пульс"' not in pressure_script
    assert "<svg" not in rendered


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
        "circumference",
        "pressure",
        "activity",
        "recovery",
        "labs",
        "studies",
    ]
    assert created["page_count"] >= 1
    assert created["size_bytes"] > 0


def test_report_uses_lab_report_date_and_renders_self_contained_html(db):
    document = LabDocument(
        id="33333333-3333-4333-8333-333333333333",
        storage_key="html-report-key",
        original_filename="private.pdf",
        file_sha256="c" * 64,
        media_type="application/pdf",
        size_bytes=100,
        status="complete",
        processing_stage="complete",
        progress_percent=100,
        verified=True,
    )
    db.add(document)
    db.flush()
    report = LabReport(
        id="44444444-4444-4444-8444-444444444444",
        document_id=document.id,
        observed_on=date(2026, 8, 24),
    )
    db.add(report)
    db.add(
        LabResult(
            id="html-result",
            document_id=document.id,
            report_id=report.id,
            source_index=0,
            analyte_name="Глюкоза",
            value_numeric=Decimal("5.2"),
            unit="ммоль/л",
            observed_on=None,
            status="within_reference",
            verification_status="verified",
            reference_source="laboratory",
            deleted=False,
        )
    )
    db.commit()
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    payload = build_doctor_report_payload(
        db,
        settings,
        DoctorReportCreate(period="30d", sections=["labs", "circumference"]),
        datetime(2026, 8, 25, 10, tzinfo=timezone.utc),
    )
    assert payload["sections"]["labs"][0]["observed_on"] == "2026-08-24"
    html = render_doctor_report_html(
        {
            "meta": {"from": "2026-07-27", "to": "2026-08-25", "timezone": "Europe/Moscow"},
            "sections": {
                "labs": payload["sections"]["labs"],
                "circumference": {"points": [{"measured_on": "2026-08-24", "waist_cm": 96.5, "hip_cm": 108.0}]},
            },
        }
    )
    text = html.decode("utf-8")
    assert "<!doctype html>" in text.lower()
    assert "Глюкоза" in text and "2026-08-24" in text
    assert "Обхваты тела" in text and "<svg" in text
    assert "https://" not in text and "private.pdf" not in text
