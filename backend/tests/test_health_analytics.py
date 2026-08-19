from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.config import Settings
from app.health_analytics import (
    activity_series,
    full_week_correlation,
    recovery_series,
)
from app.health_models import HealthConnectDevice, HealthConnectRecord


def add_device(db, data_as_of):
    device = HealthConnectDevice(
        id="00000000-0000-4000-8000-000000000001",
        label="test",
        public_key_pem="test-only",
        public_key_fingerprint="f" * 64,
        status="approved",
        data_origin="com.mi.health",
        data_as_of=data_as_of,
    )
    db.add(device)
    db.flush()
    return device


def add_record(db, device, record_id, record_type, start, value, unit, subtype=None, metrics=None):
    row = HealthConnectRecord(
        device_id=device.id,
        external_record_id=record_id,
        record_type=record_type,
        data_origin="com.mi.health",
        start_time=start,
        end_time=start + timedelta(minutes=1),
        primary_value=Decimal(str(value)),
        primary_unit=unit,
        subtype=subtype,
        metrics=metrics or {},
        is_deleted=False,
    )
    db.add(row)
    return row


def test_activity_matched_weekday_28_day_baseline(db):
    tz = Settings(database_url="sqlite+pysqlite:///:memory:").tz
    first = date(2026, 7, 13)  # Monday
    now = datetime(2026, 8, 19, 12, tzinfo=timezone.utc)
    device = add_device(db, now)
    for offset in range(38):
        day = first + timedelta(days=offset)
        count = 1_000 + day.weekday() * 100
        add_record(
            db,
            device,
            f"steps-{day}",
            "steps",
            datetime(day.year, day.month, day.day, 9, tzinfo=timezone.utc),
            count,
            "count",
            metrics={"count": count},
        )
    db.commit()
    payload = activity_series(db, tz, "30d", now)
    week = next(row for row in payload["weekly"] if row["start_date"] == "2026-08-10")
    assert week["actual_steps"] == 9100
    assert week["baseline_steps"] == 9100
    assert week["delta_steps_pct"] == 0
    assert week["coverage_days"]["steps"] == 7
    assert payload["available_metrics"] == ["steps"]
    assert all("_present" not in row for row in payload["daily"])


def test_recovery_only_advertises_metrics_that_exist(db):
    tz = Settings(database_url="sqlite+pysqlite:///:memory:").tz
    now = datetime(2026, 8, 19, 12, tzinfo=timezone.utc)
    device = add_device(db, now)
    wake = datetime(2026, 8, 19, 4, tzinfo=timezone.utc)
    add_record(
        db,
        device,
        "sleep-1",
        "sleep",
        wake - timedelta(hours=7),
        7 * 3600,
        "s",
        metrics={"duration_seconds": 7 * 3600},
    ).end_time = wake
    add_record(
        db,
        device,
        "rhr-1",
        "resting_heart_rate",
        wake,
        58,
        "bpm",
        metrics={"bpm": 58},
    )
    db.commit()
    payload = recovery_series(db, tz, "30d", now)
    assert payload["available_metrics"] == ["resting_heart_rate", "sleep"]
    point = payload["daily"][0]
    assert point["sleep_minutes"] == 420
    assert point["resting_heart_rate_bpm"] == 58
    assert point["hrv_rmssd_ms"] is None


def test_correlation_requires_eight_complete_overlapping_weeks():
    first = date(2026, 5, 4)
    daily = []
    target = {}
    for week in range(8):
        for weekday in range(7):
            day = first + timedelta(days=week * 7 + weekday)
            daily.append(
                {
                    "date": day.isoformat(),
                    "steps": 5_000 + week * 1_000,
                    "_present": {"steps"},
                }
            )
            target[day] = 130 - week
    today = first + timedelta(weeks=9)
    result = full_week_correlation(
        daily,
        health_metric="steps",
        health_presence="steps",
        target_daily=target,
        target_name="weight_kg",
        today=today,
    )
    assert result is not None
    assert result["full_overlapping_weeks"] == 8
    assert result["coefficient"] == -1
    assert "не доказывает" in result["disclaimer"]

    assert (
        full_week_correlation(
            daily[:-7],
            health_metric="steps",
            health_presence="steps",
            target_daily=target,
            target_name="weight_kg",
            today=today,
        )
        is None
    )
