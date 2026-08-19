from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.ai_snapshot import build_analysis_snapshot


NOW = datetime(2026, 8, 19, 18, 0, tzinfo=timezone.utc)


def test_snapshot_maps_current_recovery_contract(monkeypatch):
    monkeypatch.setattr(
        "app.ai_snapshot.overview",
        lambda *_args: {
            "weight": {},
            "plan": {},
            "composition": {},
            "pressure": {},
        },
    )
    monkeypatch.setattr("app.ai_snapshot.weight_series", lambda *_args: {"points": []})
    monkeypatch.setattr("app.ai_snapshot.pressure_series", lambda *_args: {"points": []})
    monkeypatch.setattr(
        "app.ai_snapshot.activity_series",
        lambda *_args: {"daily": [], "weekly": [], "correlations": [], "data_as_of": None},
    )
    monkeypatch.setattr(
        "app.ai_snapshot.recovery_series",
        lambda *_args: {
            "daily": [
                {
                    "date": "2026-08-19",
                    "spo2_pct": 97.2,
                    "vo2_max": 41.5,
                    "hrv_rmssd_ms": 48,
                }
            ],
            "correlations": [],
            "data_as_of": "2026-08-19T18:00:00Z",
        },
    )
    result = build_analysis_snapshot(None, ZoneInfo("Europe/Moscow"), NOW)
    facts = {item.key: item for item in result.facts}
    assert facts["recovery.spo2_latest"].value == 97.2
    assert facts["recovery.vo2max_latest"].value == 41.5
    assert facts["recovery.hrv_latest"].scope == "heart"
    assert facts["recovery.hrv_baseline28d"].scope == "heart"
    assert facts["recovery.spo2_latest"].scope == "oxygen"
    assert facts["recovery.vo2max_latest"].scope == "vo2"
    series = {item.key: item for item in result.series}
    assert series["recovery.spo290d"].points[0].value == 97.2
    assert series["recovery.spo290d"].scope == "oxygen"
    assert series["recovery.hrv90d"].scope == "heart"


def test_snapshot_keeps_correlation_targets_unique_and_restricted(monkeypatch):
    monkeypatch.setattr(
        "app.ai_snapshot.overview",
        lambda *_args: {
            "weight": {},
            "plan": {},
            "composition": {},
            "pressure": {},
        },
    )
    monkeypatch.setattr("app.ai_snapshot.weight_series", lambda *_args: {"points": []})
    monkeypatch.setattr("app.ai_snapshot.pressure_series", lambda *_args: {"points": []})
    monkeypatch.setattr(
        "app.ai_snapshot.activity_series",
        lambda *_args: {
            "daily": [],
            "weekly": [],
            "correlations": [
                {"metric": "steps", "target": "weight_kg", "coefficient": -0.5},
                {
                    "metric": "steps",
                    "target": "systolic_mm_hg",
                    "coefficient": 0.25,
                },
            ],
            "data_as_of": None,
        },
    )
    monkeypatch.setattr(
        "app.ai_snapshot.recovery_series",
        lambda *_args: {
            "daily": [],
            "correlations": [
                {
                    "metric": "resting_heart_rate_bpm",
                    "target": "weight_kg",
                    "coefficient": 0.4,
                },
                {
                    "metric": "spo2_pct",
                    "target": "weight_kg",
                    "coefficient": -0.1,
                },
                {
                    "metric": "vo2_max",
                    "target": "weight_kg",
                    "coefficient": -0.2,
                },
            ],
            "data_as_of": None,
        },
    )

    result = build_analysis_snapshot(None, ZoneInfo("Europe/Moscow"), NOW)
    correlations = {
        item.key: item for item in result.facts if item.key.startswith("correlation.")
    }

    assert set(correlations) == {
        "correlation.activity_steps_to_weight_kg",
        "correlation.activity_steps_to_systolic_mm_hg",
        "correlation.recovery_resting_heart_rate_bpm_to_weight_kg",
        "correlation.recovery_spo2_pct_to_weight_kg",
        "correlation.recovery_vo2_max_to_weight_kg",
    }
    assert correlations["correlation.activity_steps_to_weight_kg"].scope == "correlation"
    assert (
        correlations["correlation.activity_steps_to_systolic_mm_hg"].scope
        == "pressure"
    )
    assert (
        correlations["correlation.recovery_resting_heart_rate_bpm_to_weight_kg"].scope
        == "heart"
    )
    assert correlations["correlation.recovery_spo2_pct_to_weight_kg"].scope == "oxygen"
    assert correlations["correlation.recovery_vo2_max_to_weight_kg"].scope == "vo2"
