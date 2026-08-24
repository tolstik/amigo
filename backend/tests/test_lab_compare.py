from datetime import datetime, timezone
from decimal import Decimal
import json

import pytest
from fastapi import HTTPException

from app.lab_models import LabDocument, LabResult
from app.labs_api import LabCompareRequest, compare_lab_documents


def _document(db, document_id: str) -> LabDocument:
    row = LabDocument(
        id=document_id,
        storage_key=f"key-{document_id}",
        original_filename="private-name.pdf",
        file_sha256=(document_id.replace("-", "") * 4)[:64],
        media_type="application/pdf",
        size_bytes=10,
        status="complete",
        processing_stage="complete",
        progress_percent=100,
        verified=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(row)
    return row


def _result(db, document: LabDocument, result_id: str, value: str, *, unit: str = "г/л"):
    row = LabResult(
        id=result_id,
        document_id=document.id,
        analyte_id="hemoglobin",
        source_index=0,
        analyte_name="Гемоглобин",
        value_numeric=Decimal(value),
        unit=unit,
        specimen="кровь",
        method="метод",
        status="within_reference",
        verification_status="verified",
        reference_source="laboratory",
        deleted=False,
    )
    db.add(row)


def test_compares_two_panels_only_by_persisted_analyte_id(db):
    first = _document(db, "11111111-1111-4111-8111-111111111111")
    second = _document(db, "22222222-2222-4222-8222-222222222222")
    db.flush()
    _result(db, first, "result-1", "140")
    _result(db, second, "result-2", "147")
    db.commit()

    payload = compare_lab_documents(
        LabCompareRequest(document_ids=[first.id, second.id]),
        None,  # type: ignore[arg-type]
        db,
    )

    assert len(payload["rows"]) == 1
    row = payload["rows"][0]
    assert row["comparable"] is True
    assert row["deltas"][0]["absolute"] == 7
    assert row["deltas"][0]["percent"] == 5
    assert "private-name.pdf" not in json.dumps(payload, ensure_ascii=False, default=str)


def test_different_units_are_visible_but_not_compared(db):
    first = _document(db, "33333333-3333-4333-8333-333333333333")
    second = _document(db, "44444444-4444-4444-8444-444444444444")
    db.flush()
    _result(db, first, "result-3", "140")
    _result(db, second, "result-4", "8.6", unit="ммоль/л")
    db.commit()

    payload = compare_lab_documents(
        LabCompareRequest(document_ids=[first.id, second.id]),
        None,  # type: ignore[arg-type]
        db,
    )
    assert payload["rows"][0]["comparable"] is False
    assert payload["rows"][0]["incompatibility"] == "different_unit"
    assert len(payload["rows"][0]["cells"]) == 2


def test_rejects_incomplete_panel(db):
    first = _document(db, "55555555-5555-4555-8555-555555555555")
    second = _document(db, "66666666-6666-4666-8666-666666666666")
    second.status = "processing"
    db.commit()

    with pytest.raises(HTTPException) as error:
        compare_lab_documents(
            LabCompareRequest(document_ids=[first.id, second.id]),
            None,  # type: ignore[arg-type]
            db,
        )
    assert error.value.status_code == 409


def test_panel_date_falls_back_to_non_deleted_result_date(db):
    first = _document(db, "77777777-7777-4777-8777-777777777777")
    second = _document(db, "88888888-8888-4888-8888-888888888888")
    db.flush()
    _result(db, first, "result-7", "140")
    _result(db, second, "result-8", "141")
    first.results[0].observed_on = datetime(2026, 7, 10, tzinfo=timezone.utc).date()
    second.results[0].observed_on = datetime(2026, 8, 10, tzinfo=timezone.utc).date()
    db.commit()

    payload = compare_lab_documents(
        LabCompareRequest(document_ids=[first.id, second.id]),
        None,  # type: ignore[arg-type]
        db,
    )

    assert [panel["observed_on"].isoformat() for panel in payload["panels"]] == [
        "2026-07-10",
        "2026-08-10",
    ]
