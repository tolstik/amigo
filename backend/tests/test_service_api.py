from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.config import Settings
from app.db import get_db
from app.main import app
from app.models import SyncState
from app.service import composition_series, ensure_default_plan, overview, pressure_series, weight_series


def seed(db, add_group):
    ensure_default_plan(db)
    start = datetime(2026, 8, 15, 6, tzinfo=timezone.utc)
    for index in range(5):
        add_group(
            f"w{index}",
            start + timedelta(days=index),
            {
                "weight": (127.03 - index * 0.3, "kg"),
                "fat_percent": (35.0 - index * 0.1, "%"),
                "fat_mass": (44.0 - index * 0.2, "kg"),
                "fat_free_mass": (83.0 - index * 0.1, "kg"),
            },
        )
    add_group(
        "p1",
        start + timedelta(days=4, hours=2),
        {"systolic": (128, "mmHg"), "diastolic": (82, "mmHg"), "pulse": (67, "bpm")},
    )
    db.add(SyncState(provider="withings", initial_import_done=True, last_success_at=start + timedelta(days=4)))
    db.commit()
    return start


def test_service_payloads_match_frontend_contract(db, add_group):
    start = seed(db, add_group)
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    summary = overview(db, settings.tz, start + timedelta(days=4, hours=3))
    assert summary["plan"]["start_date"] == "2026-08-15"
    assert summary["weight"]["latest_kg"] == 125.83
    assert summary["pressure"]["latest_systolic"] == 128
    assert summary["composition"]["fat_pct"] == 34.6
    assert summary["sync"]["status"] == "delayed"
    assert isinstance(summary["insights"], list)

    weights = weight_series(db, settings.tz, "program", start + timedelta(days=4))
    assert weights["meta"]["count"] == 5
    assert weights["points"][0]["weight_kg"] == 127.03
    assert len(weights["weekly"]) == 2
    assert weights["weekly"][0]["start_date"] == "2026-08-15"
    assert weights["weekly"][0]["end_date"] == "2026-08-16"
    assert weights["weekly"][0]["actual_avg_kg"] == 126.88
    assert weights["weekly"][0]["is_partial"] is True
    assert weights["weekly"][1]["actual_change_kg"] == -0.75
    assert weights["weekly"][1]["measurement_days"] == 3
    pressures = pressure_series(db, settings.tz, "all", start + timedelta(days=4, hours=3))
    assert pressures["points"][0]["session_size"] == 1
    assert pressures["stats_7d"]["avg_systolic"] == 128
    composition = composition_series(db, settings.tz, "all", start + timedelta(days=4))
    assert composition["points"][-1]["lean_mass_kg"] == 82.6


def test_fastapi_is_read_only_and_returns_csv(db, add_group):
    seed(db, add_group)

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/overview")
            assert response.status_code == 200
            assert "weight" in response.json()
            assert response.headers["cache-control"] == "no-store"
            series = client.get("/api/v1/series/weight?range=program")
            assert series.status_code == 200
            assert {"points", "weekly", "meta"} <= series.json().keys()
            csv_response = client.get("/api/v1/export/weight.csv?range=all")
            assert csv_response.status_code == 200
            assert csv_response.text.startswith("measured_at,value,unit")
            assert client.post("/api/v1/overview").status_code == 405
    finally:
        app.dependency_overrides.clear()


def test_program_overview_excludes_pre_program_weight(db, add_group):
    ensure_default_plan(db)
    add_group(
        "old-weight",
        datetime(2026, 8, 10, 6, tzinfo=timezone.utc),
        {"weight": (130.0, "kg")},
    )
    summary = overview(
        db,
        Settings(database_url="sqlite+pysqlite:///:memory:").tz,
        datetime(2026, 8, 19, 6, tzinfo=timezone.utc),
    )
    assert summary["weight"]["latest_kg"] is None
    assert summary["weight"]["progress_pct"] is None


def test_weight_series_contains_continuous_plan_and_future_forecast(db, add_group):
    ensure_default_plan(db)
    start = datetime(2026, 8, 15, 6, tzinfo=timezone.utc)
    for index in range(28):
        add_group(
            f"trend-{index}",
            start + timedelta(days=index),
            {"weight": (127.03 - index * 0.2, "kg")},
        )
    payload = weight_series(
        db,
        Settings(database_url="sqlite+pysqlite:///:memory:").tz,
        "program",
        start + timedelta(days=27, hours=1),
    )
    assert payload["plan_projection"][0]["measured_at"] == "2026-08-15"
    assert payload["plan_projection"][-1]["measured_at"] == payload["plan"]["planned_target_date"]
    assert payload["plan_projection"][-1]["planned_kg"] == 76.5
    assert payload["projection"]
    assert payload["projection"][-1]["measured_at"] > payload["points"][-1]["measured_at"]


def test_all_history_never_uses_pre_program_weight_for_forecast(db, add_group):
    ensure_default_plan(db)
    start = datetime(2026, 7, 16, 6, tzinfo=timezone.utc)
    for index in range(35):
        add_group(
            f"boundary-{index}",
            start + timedelta(days=index),
            {"weight": (133.0 - index * 0.2, "kg")},
        )
    payload = weight_series(
        db,
        Settings(database_url="sqlite+pysqlite:///:memory:").tz,
        "all",
        start + timedelta(days=34, hours=1),
    )
    assert payload["points"][0]["measured_at"] == "2026-07-16"
    assert payload["forecast"]["reliable"] is False
    assert payload["forecast"]["reason"] == "not_enough_measurement_days"
    assert payload["projection"] == []
    assert all(
        point["forecast_kg"] is None
        for point in payload["points"]
        if point["measured_at"] < "2026-08-15"
    )


def test_stale_overview_suppresses_current_plan_comparison(db, add_group):
    ensure_default_plan(db)
    start = datetime(2026, 8, 15, 6, tzinfo=timezone.utc)
    for index in range(18):
        add_group(
            f"stale-{index}",
            start + timedelta(days=index),
            {"weight": (127.03 - index * 0.15, "kg")},
        )
    payload = overview(
        db,
        Settings(database_url="sqlite+pysqlite:///:memory:").tz,
        start + timedelta(days=33, hours=1),
    )
    assert payload["weight"]["is_stale"] is True
    assert payload["weight"]["latest_age_days"] == 16
    assert payload["weight"]["deviation_from_plan_kg"] is None
    assert payload["plan_delta_kg"] is None
    assert payload["weight"]["forecast_date"] is None
    assert payload["forecast"]["reason"] == "latest_measurement_is_stale"
