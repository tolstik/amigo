from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.analytics import (
    DailyPoint,
    PressureReading,
    ValuePoint,
    build_insights,
    daily_medians,
    plan_weight,
    planned_target_date,
    pressure_sessions,
    pressure_statistics,
    theil_sen_forecast,
    weekly_weight_points,
)


def test_calendar_plan_points_and_target_date():
    assert plan_weight(date(2026, 8, 14)) is None
    assert plan_weight(date(2026, 8, 15)) == pytest.approx(127.03)
    assert plan_weight(date(2026, 9, 15)) == pytest.approx(123.03)
    assert planned_target_date() == date(2027, 9, 4)
    assert plan_weight(date(2028, 1, 1)) == pytest.approx(76.5)


def test_daily_median_uses_moscow_day_and_marks_extreme_outlier():
    tz = ZoneInfo("Europe/Moscow")
    start = datetime(2026, 8, 15, 20, tzinfo=timezone.utc)
    points = [ValuePoint(start + timedelta(days=index), 127 - index * 0.1) for index in range(8)]
    # Same Moscow day as the first sample, making the daily value the median.
    points.append(ValuePoint(start + timedelta(minutes=30), 125.0))
    points[4] = ValuePoint(points[4].measured_at, 180.0)
    daily = daily_medians(points, tz)
    assert daily[0].sample_count == 2
    assert daily[0].value == pytest.approx(126.0)
    assert any(point.is_outlier and point.value == 180.0 for point in daily)
    assert daily[-1].rolling_7d is not None


def test_weekly_weight_points_use_iso_weeks_and_preserve_empty_buckets():
    daily = [
        DailyPoint(date(2026, 8, 15), 127.0, 2),
        DailyPoint(date(2026, 8, 16), 160.0, 1, is_outlier=True),
        DailyPoint(date(2026, 8, 17), 126.5, 1),
        DailyPoint(date(2026, 8, 20), 126.0, 2),
        DailyPoint(date(2026, 8, 31), 125.5, 1),
        DailyPoint(date(2026, 9, 3), 100.0, 1),
    ]

    weekly = weekly_weight_points(daily, as_of=date(2026, 9, 2))

    assert len(weekly) == 4
    assert weekly[0] == {
        "start_date": "2026-08-15",
        "end_date": "2026-08-16",
        "actual_avg_kg": 127.0,
        "actual_min_kg": 127.0,
        "planned_avg_kg": 126.965,
        "actual_change_kg": None,
        "planned_change_kg": None,
        "deviation_from_plan_kg": pytest.approx(
            127.0 - (plan_weight(date(2026, 8, 15)) + plan_weight(date(2026, 8, 16))) / 2,
            abs=0.001,
        ),
        "measurement_days": 2,
        "sample_count": 3,
        "outlier_days": 1,
        "is_partial": True,
    }
    assert weekly[1]["start_date"] == "2026-08-17"
    assert weekly[1]["end_date"] == "2026-08-23"
    assert weekly[1]["actual_avg_kg"] == 126.25
    assert weekly[1]["actual_min_kg"] == 126.0
    assert weekly[1]["actual_change_kg"] == -0.75
    assert weekly[1]["planned_change_kg"] < 0
    assert weekly[1]["is_partial"] is False
    assert weekly[2]["start_date"] == "2026-08-24"
    assert weekly[2]["actual_avg_kg"] is None
    assert weekly[2]["actual_change_kg"] is None
    assert weekly[2]["measurement_days"] == 0
    assert weekly[3]["start_date"] == "2026-08-31"
    assert weekly[3]["end_date"] == "2026-09-02"
    assert weekly[3]["actual_avg_kg"] == 125.5
    assert weekly[3]["actual_change_kg"] is None
    assert weekly[3]["is_partial"] is True


def test_weekly_weight_points_are_empty_before_program_start():
    assert weekly_weight_points([], as_of=date(2026, 8, 14)) == []


def test_weekly_weight_points_mark_current_week_partial_even_on_sunday():
    assert weekly_weight_points([], as_of=date(2026, 8, 30))[-1]["is_partial"] is True


def test_theil_sen_forecast_is_gated_and_projects_declining_trend():
    start = date(2026, 8, 15)
    declining = [
        DailyPoint(start + timedelta(days=index * 2), 127 - index * 0.35, 1)
        for index in range(18)
    ]
    forecast = theil_sen_forecast(declining)
    assert forecast.reliable
    assert forecast.weekly_change_kg < 0
    assert forecast.target_date is not None

    growing = [
        DailyPoint(start + timedelta(days=index * 2), 127 + index * 0.1, 1)
        for index in range(18)
    ]
    rejected = theil_sen_forecast(growing)
    assert not rejected.reliable
    assert rejected.reason == "trend_is_not_decreasing"

    uncertain = [
        DailyPoint(
            start + timedelta(days=index * 2),
            127 - index * 0.03 + (-1 if index % 2 == 0 else 1),
            1,
        )
        for index in range(18)
    ]
    uncertain_forecast = theil_sen_forecast(uncertain)
    assert not uncertain_forecast.reliable
    assert uncertain_forecast.reason == "slope_interval_crosses_zero"

    stale = theil_sen_forecast(declining, as_of=declining[-1].day + timedelta(days=15))
    assert not stale.reliable
    assert stale.reason == "latest_measurement_is_stale"


def test_stale_weight_only_emits_freshness_insights():
    start = date(2026, 8, 15)
    daily = [DailyPoint(start + timedelta(days=index), 127 - index * 0.2, 1) for index in range(18)]
    items = build_insights(daily, today=daily[-1].day + timedelta(days=20))
    rules = {str(item["rule"]) for item in items}
    assert {"measure_regularly", "stale_weight_data"} <= rules


def test_plan_advice_uses_smoothed_trend_not_one_day_weight():
    day = date(2026, 8, 15) + timedelta(days=10)
    points = [
        DailyPoint(
            day,
            120.0,
            1,
            rolling_7d=130.0,
        )
    ]

    rules = {item["rule"] for item in build_insights(points, today=day)}

    assert "on_plan" not in rules
    assert "milestone_5" not in rules


def test_pressure_readings_within_five_minutes_form_one_session():
    start = datetime(2026, 8, 19, 5, tzinfo=timezone.utc)
    sessions = pressure_sessions(
        [
            PressureReading(start, 130, 85, 70),
            PressureReading(start + timedelta(minutes=4), 128, 83, 68),
            PressureReading(start + timedelta(minutes=6), 140, 90, 75),
        ]
    )
    assert len(sessions) == 2
    assert sessions[0].systolic == 129
    assert sessions[0].pulse_pressure == 45
    stats = pressure_statistics(sessions, ZoneInfo("Europe/Moscow"), start + timedelta(hours=1))
    assert stats["last_7_days"]["sessions"] == 2
    assert stats["last_7_days"]["systolic"]["mean"] == 134.5
