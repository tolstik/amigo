from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from app.config import Settings
from app.db import get_db
from app.auth import require_csrf, require_session
from app.main import app
from app.ai_models import AiAnalysisJob, AiAnalysisResult
from app.models import SyncState
from app.body_measurements_models import BodyCircumference
from app.service import circumference_series, composition_series, ensure_default_plan, overview, pressure_series, weight_series


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


def test_circumference_series_filters_range_and_keeps_independent_values(db):
    db.add_all([
        BodyCircumference(measured_on=date(2026, 6, 1), waist_cm=Decimal("98.5"), hip_cm=None),
        BodyCircumference(measured_on=date(2026, 8, 20), waist_cm=None, hip_cm=Decimal("108.2")),
    ])
    db.commit()
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    payload = circumference_series(db, settings.tz, "30d", datetime(2026, 8, 25, 10, tzinfo=timezone.utc))
    assert payload["points"] == [{"measured_on": "2026-08-20", "waist_cm": None, "hip_cm": 108.2}]


def test_fastapi_is_read_only_and_returns_csv(db, add_group):
    seed(db, add_group)

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_session] = lambda: object()
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


def test_fastapi_circumference_upsert_delete_and_csv(db):
    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_session] = lambda: object()
    app.dependency_overrides[require_csrf] = lambda: object()
    try:
        with TestClient(app) as client:
            saved = client.put(
                "/api/v1/body-measurements/2026-08-25",
                json={"waist_cm": 96.5},
            )
            assert saved.status_code == 200
            assert saved.json()["hip_cm"] is None
            series = client.get("/api/v1/series/circumference?range=all")
            assert series.status_code == 200
            assert series.json()["points"][0]["waist_cm"] == 96.5
            csv_response = client.get("/api/v1/export/circumference.csv?range=all")
            assert csv_response.status_code == 200
            assert "measured_on,waist_cm,hip_cm,unit" in csv_response.text
            assert client.delete("/api/v1/body-measurements/2026-08-25").status_code == 204
    finally:
        app.dependency_overrides.clear()


def test_public_ai_gets_read_only_cached_state_without_invoking_codex(db, monkeypatch):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("a public GET must never invoke or enqueue Codex")

    monkeypatch.setattr("app.ai_gateway.CodexRunner.run", fail_if_called)
    monkeypatch.setattr("app.ai_snapshot.enqueue_current_analysis", fail_if_called)
    monkeypatch.setattr("app.ai_queue.enqueue_analysis", fail_if_called)

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_session] = lambda: object()
    try:
        with TestClient(app) as client:
            analysis = client.get("/api/v1/ai-analysis")
            insights = client.get("/api/v1/insights")
    finally:
        app.dependency_overrides.clear()

    assert analysis.status_code == 200
    assert analysis.json()["status"] == "unavailable"
    assert analysis.json()["ai_generated"] is True
    assert insights.status_code == 200
    assert insights.json()["status"] == "unavailable"
    assert db.query(AiAnalysisJob).count() == 0
    assert db.query(AiAnalysisResult).count() == 0


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
