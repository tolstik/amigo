from __future__ import annotations

import csv
import io
import zipfile
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.analytics import DailyPoint, PlanSpec
from app.auth import require_csrf, require_session
from app.config import Settings
from app.db import get_db
from app.journal import (
    checkin_correlations,
    is_current_medication,
    maintenance_state,
    medication_pressure_comparison,
    milestones,
    plateau_status,
)
from app.journal_models import DailyCheckin
from app.main import app
from app.medication_models import Medication
from app.service import ensure_default_plan, overview
from app.telegram import TelegramNotifier


MSK = ZoneInfo("Europe/Moscow")


@pytest.fixture
def client(db):
    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_session] = lambda: object()
    app.dependency_overrides[require_csrf] = lambda: object()
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def _trend(start: date, values: list[float]) -> list[DailyPoint]:
    return [
        DailyPoint(start + timedelta(days=index), value, 1, False, value)
        for index, value in enumerate(values)
    ]


def test_notes_crud_validates_category_length_and_future_days(client):
    created = client.post("/api/v1/notes", json={"day": "2026-09-01", "category": "illness", "text": "  Простуда  "})
    assert created.status_code == 201
    note = created.json()
    assert note["text"] == "Простуда"
    assert note["category"] == "illness"

    assert client.post("/api/v1/notes", json={"day": "2026-09-01", "category": "party", "text": "x"}).status_code == 422
    assert client.post("/api/v1/notes", json={"day": "2026-09-01", "category": "other", "text": "x" * 281}).status_code == 422
    assert client.post("/api/v1/notes", json={"day": "2999-01-01", "category": "other", "text": "x"}).status_code == 422

    patched = client.patch(f"/api/v1/notes/{note['id']}", json={"category": "travel"})
    assert patched.status_code == 200
    assert patched.json()["category"] == "travel"
    assert client.patch(f"/api/v1/notes/{note['id']}", json={"text": None}).status_code == 422
    assert client.patch(f"/api/v1/notes/{note['id']}", json={}).status_code == 422

    listed = client.get("/api/v1/notes?range=all").json()
    assert [item["id"] for item in listed["items"]] == [note["id"]]
    assert client.delete(f"/api/v1/notes/{note['id']}").status_code == 204
    assert client.delete(f"/api/v1/notes/{note['id']}").status_code == 404
    assert client.patch("/api/v1/notes/not-a-uuid", json={"text": "x"}).status_code == 422


def test_checkins_replace_whole_day_and_reject_empty_or_out_of_range(client):
    assert client.put("/api/v1/checkins/2026-09-02", json={}).status_code == 422
    assert client.put("/api/v1/checkins/2026-09-02", json={"energy": 6}).status_code == 422
    assert client.put("/api/v1/checkins/2999-01-01", json={"energy": 3}).status_code == 422

    first = client.put("/api/v1/checkins/2026-09-02", json={"energy": 4, "mood": 3})
    assert first.status_code == 200
    replaced = client.put("/api/v1/checkins/2026-09-02", json={"hunger": 2})
    assert replaced.json() == {"day": "2026-09-02", "energy": None, "hunger": 2, "mood": None}

    payload = client.get("/api/v1/checkins?range=all").json()
    assert payload["items"] == [replaced.json()]
    assert payload["correlations"] == []
    assert client.delete("/api/v1/checkins/2026-09-02").status_code == 204
    assert client.delete("/api/v1/checkins/2026-09-02").status_code == 404


def test_checkin_correlations_require_fourteen_same_day_pairs():
    start = date(2026, 9, 1)
    checkins = [DailyCheckin(day=start + timedelta(days=index), energy=1 + index % 5) for index in range(14)]
    sleep = [
        {"date": (start + timedelta(days=index)).isoformat(), "sleep_minutes": 300 + 30 * (index % 5)}
        for index in range(14)
    ]
    result = checkin_correlations(checkins, sleep, [])
    assert [(item["metric"], item["target"], item["paired_days"]) for item in result] == [
        ("energy", "sleep_minutes", 14)
    ]
    assert result[0]["coefficient"] == 1.0
    assert "причинность" in result[0]["disclaimer"]
    assert checkin_correlations(checkins[:13], sleep, []) == []


def test_plateau_needs_recent_dense_data_and_flags_flat_trend():
    today = date(2026, 10, 31)
    flat = _trend(today - timedelta(days=20), [110.0 - 0.005 * index for index in range(21)])
    status = plateau_status(flat, today)
    assert status["active"] is True
    assert status["change_kg"] == pytest.approx(-0.07)
    assert status["to"] == today.isoformat()

    falling = _trend(today - timedelta(days=20), [110.0 - 0.1 * index for index in range(21)])
    assert plateau_status(falling, today)["active"] is False

    assert plateau_status(flat, today + timedelta(days=5))["reason"] == "stale"
    sparse = [point for index, point in enumerate(flat) if index % 5 == 0]
    assert plateau_status(sparse, today)["reason"] == "insufficient_data"
    assert plateau_status([], today)["reason"] == "insufficient_data"


def test_maintenance_phase_is_sticky_and_reports_corridor():
    plan = PlanSpec()
    loss = maintenance_state(_trend(date(2026, 9, 1), [100.0, 99.0]), plan, 99.0)
    assert loss["phase"] == "loss"
    assert loss["band_status"] is None

    reached = _trend(date(2027, 9, 1), [77.0, 76.4, 78.5])
    state = maintenance_state(reached, plan, 78.5)
    assert state == {
        "phase": "maintenance",
        "target_reached_on": "2027-09-02",
        "band_low_kg": 75.0,
        "band_high_kg": 78.0,
        "band_status": "above",
    }
    assert maintenance_state(reached, plan, 76.0)["band_status"] == "inside"


def test_milestones_cover_weight_steps_bmi_streaks_and_best_week():
    plan = PlanSpec()
    start = plan.start_date
    values = [127.0, 126.0, 125.0, 124.0, 123.0, 122.5, 122.0, 121.9]
    daily = _trend(start, values) + [DailyPoint(start + timedelta(days=10), 121.5, 1, False, 121.5)]
    today = start + timedelta(days=10)
    result = milestones(daily, plan, 176.0, today)

    by_id = {item["id"]: item for item in result["items"]}
    assert by_id["weight-5"]["achieved_on"] == (start + timedelta(days=6)).isoformat()
    assert by_id["weight-10"]["achieved_on"] is None
    assert by_id["target"]["threshold_kg"] == plan.target_weight_kg
    assert {"bmi-40", "bmi-35", "bmi-30", "bmi-25"} <= by_id.keys()
    # 40 × 1.76² = 123.9 kg, first crossed by the 123.0 kg day.
    assert by_id["bmi-40"]["achieved_on"] == (start + timedelta(days=4)).isoformat()
    assert result["next"]["id"] == "weight-10"
    assert result["next"]["remaining_kg"] == pytest.approx(121.5 - by_id["weight-10"]["threshold_kg"])
    assert result["streaks"] == {
        "current_days": 1,
        "longest_days": 8,
        "longest_end": (start + timedelta(days=7)).isoformat(),
    }
    thresholds = [item["threshold_kg"] for item in result["items"]]
    assert thresholds == sorted(thresholds, reverse=True)


def test_outliers_do_not_achieve_milestones():
    plan = PlanSpec()
    daily = [DailyPoint(plan.start_date, 100.0, 1, True, None)]
    result = milestones(daily, plan, 176.0, plan.start_date)
    assert all(item["achieved_on"] is None for item in result["items"])
    assert result["streaks"]["current_days"] == 1


def test_medication_comparison_uses_equal_windows_and_excludes_start_day():
    started = date(2026, 9, 15)
    sessions = [
        {"measured_at": "2026-09-10T06:00:00Z", "systolic": 140, "diastolic": 90},
        {"measured_at": "2026-09-15T06:00:00Z", "systolic": 200, "diastolic": 120},
        {"measured_at": "2026-09-20T06:00:00Z", "systolic": 130, "diastolic": 80},
        {"measured_at": "2026-08-01T06:00:00Z", "systolic": 180, "diastolic": 110},
    ]
    result = medication_pressure_comparison(sessions, started, MSK, date(2026, 9, 22))
    assert result["before"]["sessions"] == 1
    assert result["before"]["systolic_mean"] == 140.0
    assert result["after"]["diastolic_mean"] == 80.0
    assert result["after"]["complete"] is False
    assert medication_pressure_comparison(sessions, None, MSK, date(2026, 9, 22)) is None


def test_medication_dates_round_trip_and_mark_current(client, db, monkeypatch):
    monkeypatch.setattr("app.api._enqueue_medication_analysis", lambda *_args: None)
    created = client.post(
        "/api/v1/medications",
        json={"name": "A", "dosage": "5 мг", "started_on": "2026-09-01", "stopped_on": "2026-09-10"},
    )
    assert created.status_code == 201
    assert created.json()["started_on"] == "2026-09-01"
    bad = client.post(
        "/api/v1/medications",
        json={"name": "B", "dosage": "1", "started_on": "2026-09-10", "stopped_on": "2026-09-01"},
    )
    assert bad.status_code == 422
    medication_id = created.json()["id"]
    assert client.patch(f"/api/v1/medications/{medication_id}", json={"stopped_on": "2026-08-01"}).status_code == 422
    items = client.get("/api/v1/medications").json()["items"]
    assert items[0]["is_current"] is False
    assert items[0]["pressure_comparison"]["window_days"] == 14
    row = db.get(Medication, medication_id)
    assert row.stopped_on == date(2026, 9, 10)

    assert is_current_medication(Medication(name="x", dosage="y"), date(2026, 9, 1)) is True
    assert is_current_medication(Medication(name="x", dosage="y", started_on=date(2026, 9, 2)), date(2026, 9, 1)) is False


def test_archive_export_contains_all_structured_tables_without_originals(client, db, add_group):
    ensure_default_plan(db)
    add_group("w1", datetime(2026, 9, 1, 6, tzinfo=timezone.utc), {"weight": (120.0, "kg")})
    client.post("/api/v1/notes", json={"day": "2026-09-01", "category": "holiday", "text": "День рождения"})
    client.put("/api/v1/checkins/2026-09-01", json={"energy": 4})

    response = client.get("/api/v1/export/archive.zip")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["cache-control"] == "no-store"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        assert names == {
            "amigo-weight.csv",
            "amigo-pressure.csv",
            "amigo-composition.csv",
            "amigo-circumference.csv",
            "amigo-activity.csv",
            "amigo-recovery.csv",
            "amigo-notes.csv",
            "amigo-checkins.csv",
            "amigo-medications.csv",
            "amigo-lab-results.csv",
        }
        notes = list(csv.reader(io.StringIO(archive.read("amigo-notes.csv").decode())))
        assert notes == [["day", "category", "text"], ["2026-09-01", "holiday", "День рождения"]]
        assert "120.0" in archive.read("amigo-weight.csv").decode()


def test_milestones_endpoint_and_overview_expose_deterministic_state(client, db, add_group):
    ensure_default_plan(db)
    add_group("w1", datetime(2026, 8, 16, 6, tzinfo=timezone.utc), {"weight": (121.0, "kg")})
    payload = client.get("/api/v1/milestones").json()
    by_id = {item["id"]: item for item in payload["items"]}
    assert by_id["weight-5"]["achieved_on"] == "2026-08-16"
    assert by_id["bmi-40"]["achieved_on"] == "2026-08-16"
    summary = overview(db, MSK, datetime(2026, 8, 17, 6, tzinfo=timezone.utc))
    assert summary["maintenance"]["phase"] == "loss"
    assert summary["plateau"]["active"] is False


def test_daily_digest_reports_active_plateau_fact(db, monkeypatch):
    ensure_default_plan(db)
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    notifier = TelegramNotifier(db, settings, client=object())  # type: ignore[arg-type]
    plateau = {"active": True, "window_days": 14, "threshold_kg": 0.2, "change_kg": -0.05}
    original = overview

    def fake_overview(*args, **kwargs):
        return {**original(*args, **kwargs), "plateau": plateau}

    monkeypatch.setattr("app.telegram.overview", fake_overview)
    text = notifier._daily_digest_text(datetime(2026, 8, 18, 6, 30, tzinfo=timezone.utc))
    assert "Плато: тренд веса за 14 дней изменился на -0.05 кг" in text
    plateau["active"] = False
    assert "Плато" not in notifier._daily_digest_text(datetime(2026, 8, 18, 6, 30, tzinfo=timezone.utc))
