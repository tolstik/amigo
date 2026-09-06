from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.ai_snapshot import build_analysis_snapshot


NOW = datetime(2026, 8, 19, 18, 0, tzinfo=timezone.utc)


def test_snapshot_maps_current_recovery_contract(monkeypatch):
    monkeypatch.setattr(
        "app.ai_snapshot.overview",
        lambda *_args: {
            "weight": {
                "latest_kg": 124.0,
                "latest_at": "2026-08-19T06:00:00Z",
            },
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
                    "average_heart_rate_bpm": 59,
                    "minimum_heart_rate_bpm": 47,
                    "maximum_heart_rate_bpm": 89,
                }
            ],
            "correlations": [],
            "data_as_of": "2026-08-19T18:00:00Z",
        },
    )
    result = build_analysis_snapshot(None, ZoneInfo("Europe/Moscow"), NOW)
    facts = {item.key: item for item in result.facts}
    assert facts["profile.height_cm"].value == 176
    assert facts["profile.height_cm"].unit == "centimeters"
    assert facts["weight.bmi_latest"].value == 40.03
    assert facts["weight.bmi_latest"].unit == "kg_m2"
    assert facts["weight.bmi_latest"].observed_on.isoformat() == "2026-08-19"
    assert facts["recovery.spo2_latest"].value == 97.2
    assert facts["recovery.vo2max_latest"].value == 41.5
    assert facts["recovery.hrv_latest"].scope == "heart"
    assert facts["recovery.hrv_baseline28d"].scope == "heart"
    assert facts["recovery.heart_rate_average_latest"].value == 59
    assert facts["recovery.heart_rate_minimum_latest"].value == 47
    assert facts["recovery.heart_rate_maximum_latest"].value == 89
    assert facts["recovery.heart_rate_average_baseline28d"].value == 59
    assert facts["recovery.spo2_latest"].scope == "oxygen"
    assert facts["recovery.vo2max_latest"].scope == "vo2"
    series = {item.key: item for item in result.series}
    assert series["recovery.spo290d"].points[0].value == 97.2
    assert series["recovery.spo290d"].scope == "oxygen"
    assert series["recovery.hrv90d"].scope == "heart"
    assert series["recovery.heart_rate_average90d"].points[0].value == 59
    assert series["recovery.heart_rate_average90d"].scope == "heart"


def test_snapshot_keeps_configured_height_without_weight(monkeypatch):
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
        lambda *_args: {"daily": [], "weekly": [], "correlations": []},
    )
    monkeypatch.setattr(
        "app.ai_snapshot.recovery_series",
        lambda *_args: {"daily": [], "correlations": []},
    )

    result = build_analysis_snapshot(
        None,
        ZoneInfo("Europe/Moscow"),
        NOW,
        user_height_cm=176,
    )
    facts = {item.key: item for item in result.facts}

    assert facts["profile.height_cm"].value == 176
    assert "weight.bmi_latest" not in facts


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


def _stub_snapshot_sources(monkeypatch):
    monkeypatch.setattr("app.ai_snapshot.overview", lambda *_args: {
        "weight": {"latest_kg": 125.83, "smoothed_7d_kg": 126.2,
                   "trend_28d_kg": -0.83, "deviation_from_plan_kg": 0.45},
        "plan": {"planned_today_kg": 126}, "composition": {}, "pressure": {},
    })
    monkeypatch.setattr("app.ai_snapshot.weight_series", lambda *_args: {"points": []})
    monkeypatch.setattr("app.ai_snapshot.pressure_series", lambda *_args: {"points": []})
    monkeypatch.setattr("app.ai_snapshot.activity_series", lambda *_args: {
        "daily": [], "weekly": [], "correlations": [],
    })
    monkeypatch.setattr("app.ai_snapshot.recovery_series", lambda *_args: {
        "daily": [], "correlations": [],
    })


def test_routine_series_use_moscow_calendar_window_without_changing_facts(monkeypatch):
    from datetime import timedelta
    from app.evidence import snapshot_evidence_descriptors

    _stub_snapshot_sources(monkeypatch)
    current = NOW.replace(hour=22)  # Moscow has already moved to the next day.
    today = current.astimezone(ZoneInfo("Europe/Moscow")).date()
    days = [today - timedelta(days=offset) for offset in range(60, -2, -1)]
    rows = [{"date": day.isoformat(), "sleep_minutes": 400 + index}
            for index, day in enumerate(days) if index != 50]
    monkeypatch.setattr("app.ai_snapshot.recovery_series", lambda *_args: {
        "daily": rows, "correlations": [],
    })

    full = build_analysis_snapshot(None, ZoneInfo("Europe/Moscow"), current)
    recent = build_analysis_snapshot(None, ZoneInfo("Europe/Moscow"), current,
                                     routine_context=True)
    assert recent.facts == full.facts
    assert recent.source_through == full.source_through
    assert len(full.series[0].points) > 28
    assert recent.series[0].key == "sleep.duration28d"
    points = recent.series[0].points
    assert points[0].day == today - timedelta(days=27)
    assert points[-1].day == today
    assert len(points) == 27  # The missing day is not filled or averaged.
    assert recent.series[0].unit == "minutes"
    original = {point.day: point.value for point in full.series[0].points}
    assert all(original[point.day] == point.value for point in points)
    descriptor = snapshot_evidence_descriptors(recent, ["sleep.duration28d"])["sleep.duration28d"]
    assert descriptor["range"] == {"from": points[0].day.isoformat(), "to": today.isoformat()}
    assert descriptor["count"] == 27
    assert descriptor["unit"] == "minutes"


def test_routine_labs_keep_latest_distinct_results_and_full_freshness(db, monkeypatch):
    from datetime import timedelta
    from decimal import Decimal
    from hashlib import sha256
    from app.lab_models import LabDocument, LabResult

    _stub_snapshot_sources(monkeypatch)
    document = LabDocument(id="synthetic-doc", storage_key="synthetic-store",
                           original_filename="must-not-leak.pdf", file_sha256="a" * 64,
                           media_type="application/pdf", size_bytes=1)
    db.add(document)
    db.flush()
    rows = []
    for index in range(30):
        rows.append(LabResult(
            id=f"synthetic-{index:02d}", document_id=document.id, source_index=index,
            analyte_name=f"Маркер {index:02d}", unit="unit", value_numeric=Decimal("1"),
            observed_on=NOW.date(), status="within_reference", reference_source="laboratory",
            reference_low=Decimal("0"), reference_high=Decimal("2"),
            verification_status="unverified", created_at=NOW - timedelta(hours=2),
            updated_at=NOW - timedelta(hours=2),
        ))
    rows += [
        LabResult(id="recent-attention", document_id=document.id, source_index=30,
                  analyte_name="Отдельный маркер", unit="unit", value_numeric=Decimal("3"),
                  observed_on=NOW.date() - timedelta(days=1), status="above_reference",
                  reference_source="laboratory", reference_low=Decimal("0"),
                  reference_high=Decimal("2"), verification_status="unverified",
                  created_at=NOW - timedelta(days=1), updated_at=NOW - timedelta(hours=2)),
        LabResult(id="old-repeated-attention", document_id=document.id, source_index=31,
                  analyte_name="Маркер 00", unit="unit", value_numeric=Decimal("3"),
                  observed_on=NOW.date() - timedelta(days=2), status="above_reference",
                  reference_source="laboratory", created_at=NOW - timedelta(days=2),
                  updated_at=NOW - timedelta(hours=2)),
        LabResult(id="ancient-attention", document_id=document.id, source_index=32,
                  analyte_name="Старый маркер", unit="unit", value_numeric=Decimal("3"),
                  observed_on=NOW.date() - timedelta(days=100), status="above_reference",
                  reference_source="laboratory", created_at=NOW - timedelta(days=100),
                  updated_at=NOW - timedelta(hours=1)),
    ]
    db.add_all(rows)
    db.commit()
    full = build_analysis_snapshot(db, ZoneInfo("Europe/Moscow"), NOW)
    recent = build_analysis_snapshot(db, ZoneInfo("Europe/Moscow"), NOW, routine_context=True)
    key = lambda identity: "lab." + sha256(identity.encode()).hexdigest()[:20]
    selected = {result.key: result for result in recent.labs}
    assert len(full.labs) == 33
    assert len(recent.labs) == 24
    assert key("recent-attention") in selected
    assert key("old-repeated-attention") not in selected
    assert key("ancient-attention") not in selected
    assert not selected[key("recent-attention")].verified
    assert selected[key("recent-attention")].reference_high == 2
    assert recent.facts == full.facts
    assert recent.source_through == full.source_through == NOW - timedelta(hours=1)
    assert "must-not-leak.pdf" not in recent.model_dump_json()
    assert db.query(LabResult).count() == 33


def test_routine_laboratory_selection_does_not_merge_specimens_methods_or_units():
    from datetime import timedelta
    from app.ai_snapshot import _routine_laboratory_rows
    from app.lab_models import LabResult

    rows = []
    for index, (specimen, method, unit) in enumerate((
        ("blood", "method-a", "unit"),
        ("urine", "method-a", "unit"),
        ("blood", "method-b", "unit"),
        ("blood", "method-a", "other-unit"),
        ("blood", "method-a", "unit"),
    )):
        rows.append(LabResult(
            id=str(index), analyte_id="shared-analyte", analyte_name="Общий маркер",
            specimen=specimen, method=method, unit=unit,
            observed_on=NOW.date() - timedelta(days=index),
            status="within_reference",
        ))
    selected = _routine_laboratory_rows(rows)
    assert {row.id for row in selected} == {"0", "1", "2", "3"}
