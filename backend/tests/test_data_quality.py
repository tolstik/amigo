from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

from fastapi.testclient import TestClient

from app.auth import require_session
from app.config import Settings
from app.data_quality import data_quality
from app.db import get_db
from app.health_analytics import activity_series
from app.health_models import HealthConnectDevice, HealthConnectRecord
from app.main import app
from app.mi_fitness_models import MiFitnessCoverage, MiFitnessRecord, MiFitnessSource


NOW = datetime(2026, 8, 24, 12, tzinfo=timezone.utc)
TZ = Settings(database_url="sqlite+pysqlite:///:memory:").tz
DEVICE_ID = "00000000-0000-4000-8000-000000000091"
FINGERPRINT = "f" * 64


def _at(day: int) -> datetime:
    return datetime(2026, 8, day, 9, tzinfo=timezone.utc)


def _seed_sources(db) -> None:
    db.add(
        HealthConnectDevice(
            id=DEVICE_ID,
            label="SECRET_DEVICE_IDENTIFIER",
            public_key_pem="SECRET_PUBLIC_KEY",
            public_key_fingerprint="e" * 64,
            status="approved",
            data_origin="SECRET_PACKAGE_ORIGIN",
            last_sync_at=NOW,
            data_as_of=NOW,
            last_error="SECRET_RAW_ERROR",
        )
    )
    db.add(
        MiFitnessSource(
            device_id=DEVICE_ID,
            account_fingerprint=FINGERPRINT,
            region="SECRET_REGION",
            enabled=True,
            status="success",
            activated_at=NOW,
            last_success_at=NOW,
            data_as_of=NOW,
        )
    )
    db.flush()


def _mi_record(db, *, day: int, snapshot_id: str, value: int) -> None:
    measured_at = _at(day)
    db.add(
        MiFitnessRecord(
            device_id=DEVICE_ID,
            external_record_id=f"SECRET_RECORD_{day}",
            snapshot_id=snapshot_id,
            record_type="steps",
            account_fingerprint=FINGERPRINT,
            start_time=measured_at,
            end_time=measured_at + timedelta(minutes=1),
            primary_value=Decimal(value),
            primary_unit="count",
            metrics={"count": value},
            is_deleted=False,
        )
    )


def _coverage(db, *, day: int, snapshot_id: str, empty: bool) -> None:
    local_start = datetime(2026, 8, day, tzinfo=TZ)
    db.add(
        MiFitnessCoverage(
            device_id=DEVICE_ID,
            snapshot_id=snapshot_id,
            record_type="steps",
            account_fingerprint=FINGERPRINT,
            range_start=local_start.astimezone(timezone.utc),
            range_end=(local_start + timedelta(days=1)).astimezone(timezone.utc),
            confirmed_empty=empty,
            finalised_at=NOW,
        )
    )


def test_steps_are_xiaomi_finalized_only_in_analytics_and_quality(db):
    _seed_sources(db)
    db.add(
        HealthConnectRecord(
            device_id=DEVICE_ID,
            external_record_id="SECRET_HC_STEP",
            record_type="steps",
            data_origin="SECRET_PACKAGE_ORIGIN",
            start_time=_at(21),
            end_time=_at(21) + timedelta(minutes=1),
            primary_value=Decimal(9_999),
            primary_unit="count",
            metrics={"count": 9_999},
            is_deleted=False,
        )
    )
    # This row exists but its snapshot has not reached a final page.
    _mi_record(db, day=20, snapshot_id="SECRET_PARTIAL_SNAPSHOT", value=8_888)
    _coverage(db, day=22, snapshot_id="SECRET_EMPTY_SNAPSHOT", empty=True)
    _mi_record(db, day=23, snapshot_id="SECRET_FINAL_SNAPSHOT", value=1_234)
    _coverage(db, day=23, snapshot_id="SECRET_FINAL_SNAPSHOT", empty=False)
    db.commit()

    activity = activity_series(db, TZ, "30d", NOW)
    assert [(row["date"], row["steps"]) for row in activity["daily"]] == [
        ("2026-08-23", 1234)
    ]
    assert db.query(HealthConnectRecord).filter_by(record_type="steps").count() == 1

    payload = data_quality(db, TZ, "30d", NOW)
    steps = next(metric for metric in payload["metrics"] if metric["key"] == "steps")
    by_day = {row["date"]: row for row in steps["days"]}
    assert by_day["2026-08-20"] == {
        "date": "2026-08-20",
        "state": "missing",
        "source": None,
    }
    assert by_day["2026-08-21"]["state"] == "missing"
    assert by_day["2026-08-22"]["state"] == "confirmed_empty"
    assert by_day["2026-08-22"]["source"] == "mi_fitness"
    assert by_day["2026-08-23"]["state"] == "available"
    assert by_day["2026-08-23"]["source"] == "mi_fitness"
    assert steps["source_policy"] == "xiaomi_finalized_only"
    assert steps["coverage"]["health_connect"] == 0

    encoded = json.dumps(payload, ensure_ascii=False)
    for forbidden in (
        DEVICE_ID,
        FINGERPRINT,
        "SECRET_DEVICE_IDENTIFIER",
        "SECRET_PUBLIC_KEY",
        "SECRET_PACKAGE_ORIGIN",
        "SECRET_REGION",
        "SECRET_RAW_ERROR",
        "SECRET_RECORD",
        "SECRET_SNAPSHOT",
    ):
        assert forbidden not in encoded


def test_data_quality_route_is_authenticated_and_range_is_bounded(db):
    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/data-quality?range=30d").status_code == 401
        app.dependency_overrides[require_session] = lambda: object()
        with TestClient(app) as client:
            response = client.get("/api/v1/data-quality?range=90d")
            invalid = client.get("/api/v1/data-quality?range=1y")
        assert response.status_code == 200
        assert response.json()["range"] == "90d"
        assert len(response.json()["metrics"][0]["days"]) == 90
        assert invalid.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_disabled_xiaomi_history_is_not_reported_as_pending_configuration(db):
    db.add(
        HealthConnectDevice(
            id=DEVICE_ID,
            label="Amigo Sync",
            public_key_pem="public-key",
            public_key_fingerprint="e" * 64,
            status="approved",
        )
    )
    db.add(
        MiFitnessSource(
            device_id=DEVICE_ID,
            account_fingerprint=FINGERPRINT,
            enabled=False,
            status="disabled",
        )
    )
    db.commit()

    payload = data_quality(db, TZ, "30d", NOW)
    assert payload["sources"]["mi_fitness"] == {
        "status": "not_configured",
        "last_success_at": None,
        "data_as_of": None,
    }
