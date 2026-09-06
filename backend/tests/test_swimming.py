from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
import pytest

from app.auth import require_session
from app.db import get_db
from app.health_ingest import HealthIngestError, _normalise_record
from app.health_models import HealthConnectDevice, HealthConnectRecord
from app.health_schemas import HealthRecordInput
from app.main import app
from app.mi_fitness_ingest import ingest_signed_mi_fitness_batch, report_signed_status
from app.mi_fitness_models import MiFitnessCoverage, MiFitnessRecord, MiFitnessSource
from app.service import overview
from app.swimming import normalise_swimming, swimming_series
from test_mi_fitness_ingest import paired, signed_call, status_payload, batch_payload


NOW = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
TZ = ZoneInfo("Europe/Moscow")


def cloud(db, *, active=True, approved=True):
    device = HealthConnectDevice(id="test-device", label="test", public_key_pem="test", public_key_fingerprint="f" * 64, status="approved" if approved else "revoked")
    db.add(device)
    db.flush()
    source = MiFitnessSource(device_id=device.id, enabled=True, account_fingerprint="a" * 64, status="success", activated_at=NOW if active else None, data_as_of=NOW)
    db.add(source)
    coverage(db, "first")
    return device, source


def coverage(db, snapshot, *, finalised=NOW, empty=False, start=None, end=NOW):
    db.add(MiFitnessCoverage(device_id="test-device", account_fingerprint="a" * 64, snapshot_id=snapshot, record_type="exercise", range_start=start or NOW - timedelta(days=100), range_end=end, finalised_at=finalised, confirmed_empty=empty))
    db.flush()


def workout(db, record_id, *, at=None, snapshot="first", subtype="pool_swimming", details=None):
    at = at or NOW - timedelta(days=1)
    row = MiFitnessRecord(device_id="test-device", account_fingerprint="a" * 64, snapshot_id=snapshot, external_record_id=record_id, record_type="exercise", subtype=subtype, start_time=at, end_time=at + timedelta(minutes=30), primary_value=Decimal("1800"), primary_unit="s", metrics={"duration_seconds": 1800, "swimming": details or {}})
    db.add(row)
    db.flush()
    return row


def test_only_finalized_active_pool_sessions_and_nullable_totals(db):
    device, _ = cloud(db)
    workout(db, "pool", details={"distance_meters": 1000, "active_duration_seconds": 1500, "minimum_bpm": 75, "average_bpm": 100, "maximum_bpm": 120, "stroke_style": "breaststroke", "kilocalories": 240})
    workout(db, "duration-only", at=NOW - timedelta(days=2))
    workout(db, "unfinalized", snapshot="unfinished", at=NOW - timedelta(days=3))
    workout(db, "open-water", subtype="open_water_swimming")
    workout(db, "ambiguous", subtype="swimming")
    workout(db, "running", subtype="running")
    db.add(HealthConnectRecord(device_id=device.id, external_record_id="hc-pool", record_type="exercise", data_origin="com.mi.health", subtype="pool_swimming", start_time=NOW - timedelta(days=1), end_time=NOW, primary_value=1800, primary_unit="s", metrics={"duration_seconds": 1800}))
    db.commit()
    payload = swimming_series(db, TZ, "90d", NOW)
    assert payload["summary"]["workouts"] == 2
    assert payload["summary"]["distance_meters"] == 1000
    assert payload["summary"]["distance_meters_count"] == 1
    assert payload["summary"]["duration_seconds"] == 3600
    assert payload["summary"]["kilocalories_count"] == 1
    assert payload["sessions"][0]["pace_seconds_per_100m"] == 150
    assert payload["sessions"][1]["distance_meters"] is None
    assert payload["coverage"]["status"] == "available"
    serialized = json.dumps(payload)
    for private in ("test-device", "hc-pool", "first", "account_fingerprint", "external_record_id", "samples", "data_origin"):
        assert private not in serialized


@pytest.mark.parametrize("active,approved", [(False, True), (True, False)])
def test_unactivated_or_revoked_source_is_not_published(db, active, approved):
    cloud(db, active=active, approved=approved)
    workout(db, "pool")
    db.commit()
    payload = swimming_series(db, TZ, "all", NOW)
    assert payload["sessions"] == []
    assert payload["coverage"]["status"] == "missing"
    assert payload["summary"]["distance_meters"] is None


def test_empty_reconciliation_and_paginated_enrichment_do_not_duplicate(db):
    cloud(db)
    workout(db, "pool")
    workout(db, "pool", snapshot="enriched", details={"distance_meters": 750})
    assert swimming_series(db, TZ, "90d", NOW)["summary"]["distance_meters"] is None
    coverage(db, "enriched", finalised=NOW + timedelta(seconds=1))
    enriched = swimming_series(db, TZ, "90d", NOW)
    assert enriched["summary"]["workouts"] == 1
    assert enriched["summary"]["distance_meters"] == 750
    coverage(db, "empty", finalised=NOW + timedelta(seconds=2), empty=True)
    empty = swimming_series(db, TZ, "90d", NOW)
    assert empty["sessions"] == []
    assert empty["coverage"]["status"] == "confirmed_empty"
    assert empty["summary"]["distance_meters"] is None


def test_history_pagination_moscow_range_and_partial_coverage(db):
    cloud(db)
    for index in range(55):
        workout(db, f"pool-{index}", at=NOW - timedelta(days=1, minutes=index))
    # 30d starts on 8 August at 00:00 Moscow = 7 August 21:00 UTC.
    workout(db, "outside", at=datetime(2026, 8, 7, 20, 59, tzinfo=timezone.utc))
    first = swimming_series(db, TZ, "30d", NOW)
    second = swimming_series(db, TZ, "30d", NOW, offset=50)
    assert first["summary"]["workouts"] == 55
    assert len(first["sessions"]) == 50
    assert len(second["sessions"]) == 5
    assert first["next_offset"] == 50
    assert second["next_offset"] is None
    assert not {item["id"] for item in first["sessions"]} & {item["id"] for item in second["sessions"]}
    db.query(MiFitnessCoverage).update({"range_start": NOW - timedelta(days=3)})
    assert swimming_series(db, TZ, "30d", NOW)["coverage"]["status"] == "partial"


def test_coverage_separates_freshness_from_missing_history_and_checks_all_history(db):
    cloud(db)
    workout(db, "pool")
    db.query(MiFitnessCoverage).update({"range_end": NOW - timedelta(minutes=10)})
    current = swimming_series(db, TZ, "90d", NOW)
    assert current["coverage"]["status"] == "available"
    assert current["coverage"]["to"] == "2026-09-06T11:50:00Z"
    assert swimming_series(db, TZ, "all", NOW)["coverage"]["status"] == "partial"
    db.query(MiFitnessCoverage).update({"range_start": datetime(2000, 1, 1, tzinfo=timezone.utc)})
    assert swimming_series(db, TZ, "all", NOW)["coverage"]["status"] == "available"
    db.query(MiFitnessCoverage).update({"range_end": NOW - timedelta(days=10)})
    coverage(db, "recent", start=NOW - timedelta(days=3))
    assert swimming_series(db, TZ, "90d", NOW)["coverage"]["status"] == "partial"


@pytest.mark.parametrize("values", [
    {"distance_meters": True}, {"distance_meters": -1}, {"distance_meters": float("inf")},
    {"pool_lengths": 1.5}, {"stroke_style": "unknown"}, {"route": []},
    {"active_duration_seconds": 1803}, {"minimum_bpm": 120, "maximum_bpm": 100},
])
def test_swimming_rejects_nonfinite_forbidden_or_inconsistent_values(values):
    with pytest.raises(HealthIngestError):
        normalise_swimming(values, 1800)


def test_extended_signed_ingest_keeps_health_connect_strict_and_replay_idempotent(db):
    private, device = paired(db, NOW)
    signed_call(report_signed_status, db, private, device, status_payload("pending"), NOW)
    at = NOW - timedelta(hours=2)
    record = {"record_id": "pool", "type": "exercise", "data_origin": "xiaomi_cloud", "start_time": at.isoformat(), "end_time": (at + timedelta(minutes=30)).isoformat(), "values": {"exercise_type": "pool_swimming", "duration_seconds": 1800, "swimming": {"distance_meters": 750, "active_duration_seconds": 1500}}}
    with pytest.raises(HealthIngestError):
        _normalise_record(HealthRecordInput.model_validate(record))
    payload = batch_payload("exercise", NOW - timedelta(days=3), NOW, "pool-snapshot", [record])
    first = signed_call(ingest_signed_mi_fitness_batch, db, private, device, payload, NOW, request_id="pool-batch")
    replay = signed_call(ingest_signed_mi_fitness_batch, db, private, device, payload, NOW, request_id="pool-batch")
    assert not first.idempotent and replay.idempotent
    assert db.query(MiFitnessRecord).count() == 1
    assert db.query(MiFitnessRecord).one().metrics["swimming"]["distance_meters"] == 750


def test_swimming_get_is_read_only_and_comparison_is_removed(db, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("GET must only read persisted records")
    monkeypatch.setattr("app.health_api.enqueue_current_analysis", forbidden)
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/series/swimming").status_code == 401
            assert client.post("/api/v1/labs/compare", json={"document_ids": []}).status_code == 404
            app.dependency_overrides[require_session] = lambda: object()
            response = client.get("/api/v1/series/swimming?range=90d")
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            assert response.json()["coverage"]["status"] == "missing"
            assert client.get("/api/v1/series/swimming?offset=-1").status_code == 422
            assert client.get("/api/v1/series/swimming?range=program").status_code == 422
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("weight,expected", [(130, 0), (127.03, 0), (76.5, 100), (75, 100)])
def test_overview_actual_progress_boundaries(db, add_group, weight, expected):
    add_group("latest", NOW - timedelta(hours=1), {"weight": (weight, "kg")})
    payload = overview(db, TZ, NOW)
    assert payload["weight"]["progress_pct"] == expected
    assert payload["weight"]["change_since_start_kg"] == round(weight - 127.03, 3)


def test_plan_moscow_midnight_and_stale_actual(db, add_group):
    add_group("old", datetime(2026, 8, 15, 7, tzinfo=timezone.utc), {"weight": (125.83, "kg")})
    before = overview(db, TZ, datetime(2026, 9, 14, 20, 59, tzinfo=timezone.utc))
    after = overview(db, TZ, datetime(2026, 9, 14, 21, tzinfo=timezone.utc))
    assert before["plan"]["planned_today_kg"] > after["plan"]["planned_today_kg"] == 123.03
    assert after["weight"]["progress_pct"] == 2.4
    assert after["weight"]["latest_deviation_from_plan_kg"] is None
    assert after["weight"]["is_stale"]


def test_plan_stays_visible_without_measurements_and_caps_at_goal(db):
    assert overview(db, TZ, datetime(2026, 8, 15, tzinfo=timezone.utc))["plan"]["progress_today_pct"] == 0
    future = overview(db, TZ, datetime(2030, 1, 1, tzinfo=timezone.utc))
    assert future["plan"]["progress_today_pct"] == 100
    assert future["weight"]["progress_pct"] is None
    assert future["weight"]["change_since_start_kg"] is None
