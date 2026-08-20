from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
import math
import statistics
from typing import Any, Iterable, Literal, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .health_models import HealthConnectDevice, HealthConnectRecord
from .models import Measurement, MeasurementGroup


HealthRange = Literal["30d", "90d", "1y", "all"]
ACTIVITY_TYPES = frozenset(
    ("steps", "distance", "active_calories", "total_calories", "exercise")
)
RECOVERY_TYPES = frozenset(
    (
        "sleep",
        "heart_rate",
        "resting_heart_rate",
        "hrv_rmssd",
        "oxygen_saturation",
        "vo2_max",
    )
)
CORRELATION_MINIMUM_WEEKS = 8
CORRELATION_DISCLAIMER = "Корреляция не доказывает причинность."


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _range_start(range_name: HealthRange, today: date) -> date | None:
    days = {"30d": 30, "90d": 90, "1y": 365}.get(range_name)
    return today - timedelta(days=days - 1) if days is not None else None


def _utc_start(day: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, time.min, tzinfo=tz).astimezone(timezone.utc)


def _records(
    db: Session,
    record_types: frozenset[str],
    tz: ZoneInfo,
    start: date | None = None,
) -> list[HealthConnectRecord]:
    query = (
        select(HealthConnectRecord)
        .join(
            HealthConnectDevice,
            HealthConnectRecord.device_id == HealthConnectDevice.id,
        )
        .where(
            HealthConnectDevice.status == "approved",
            HealthConnectRecord.is_deleted.is_(False),
            HealthConnectRecord.record_type.in_(record_types),
            HealthConnectRecord.start_time.is_not(None),
        )
        .order_by(HealthConnectRecord.start_time, HealthConnectRecord.id)
    )
    if start is not None:
        boundary = _utc_start(start, tz)
        query = query.where(
            or_(
                HealthConnectRecord.start_time >= boundary,
                HealthConnectRecord.end_time >= boundary,
            )
        )
    return list(db.scalars(query))


def _latest_data_as_of(db: Session) -> datetime | None:
    values = list(
        db.scalars(
            select(HealthConnectDevice.data_as_of).where(
                HealthConnectDevice.status == "approved",
                HealthConnectDevice.data_as_of.is_not(None),
            )
        )
    )
    return max((_aware(value) for value in values), default=None)


def _split_interval(start: datetime, end: datetime, tz: ZoneInfo) -> list[tuple[date, float]]:
    start = _aware(start)
    end = max(start, _aware(end))
    cursor = start
    result: list[tuple[date, float]] = []
    while cursor < end:
        local = cursor.astimezone(tz)
        next_local_midnight = datetime.combine(
            local.date() + timedelta(days=1), time.min, tzinfo=tz
        )
        boundary = min(end, next_local_midnight.astimezone(timezone.utc))
        result.append((local.date(), (boundary - cursor).total_seconds()))
        cursor = boundary
    if not result:
        result.append((start.astimezone(tz).date(), 0.0))
    return result


def aggregate_activity_records(
    records: Iterable[HealthConnectRecord], tz: ZoneInfo
) -> list[dict[str, Any]]:
    grouped: dict[date, dict[str, Any]] = defaultdict(
        lambda: {
            "steps": 0.0,
            "distance_m": 0.0,
            "active_calories_kcal": 0.0,
            "total_calories_kcal": 0.0,
            "active_seconds": 0.0,
            "workouts": 0,
            "workout_types": set(),
            "_present": set(),
        }
    )
    for row in records:
        if row.start_time is None:
            continue
        start = _aware(row.start_time)
        end = _aware(row.end_time or row.start_time)
        day = start.astimezone(tz).date()
        value = float(row.primary_value) if row.primary_value is not None else 0.0
        item = grouped[day]
        if row.record_type == "steps":
            item["steps"] += value
            item["_present"].add("steps")
        elif row.record_type == "distance":
            item["distance_m"] += value
            item["_present"].add("distance")
        elif row.record_type == "active_calories":
            item["active_calories_kcal"] += value
            item["_present"].add("active_calories")
        elif row.record_type == "total_calories":
            item["total_calories_kcal"] += value
            item["_present"].add("total_calories")
        elif row.record_type == "exercise":
            item["workouts"] += 1
            item["_present"].add("exercise")
            if row.subtype:
                item["workout_types"].add(row.subtype)
            for interval_day, seconds in _split_interval(start, end, tz):
                grouped[interval_day]["active_seconds"] += seconds
                grouped[interval_day]["_present"].add("exercise")
    result: list[dict[str, Any]] = []
    for day, item in sorted(grouped.items()):
        present = set(item.pop("_present"))
        result.append(
            {
                "date": day.isoformat(),
                "steps": round(item["steps"]) if "steps" in present else None,
                "distance_km": round(item["distance_m"] / 1_000, 3)
                if "distance" in present
                else None,
                "active_calories_kcal": round(item["active_calories_kcal"], 1)
                if "active_calories" in present
                else None,
                "total_calories_kcal": round(item["total_calories_kcal"], 1)
                if "total_calories" in present
                else None,
                "active_minutes": round(item["active_seconds"] / 60, 1)
                if "exercise" in present
                else None,
                "workout_minutes": round(item["active_seconds"] / 60, 1)
                if "exercise" in present
                else None,
                "workouts": item["workouts"] if "exercise" in present else None,
                "workout_types": sorted(item["workout_types"]),
                "_present": present,
            }
        )
    return result


_ACTIVITY_METRICS = (
    "steps",
    "distance_km",
    "active_calories_kcal",
    "active_minutes",
    "workouts",
)
_ACTIVITY_PRESENCE = {
    "steps": "steps",
    "distance_km": "distance",
    "active_calories_kcal": "active_calories",
    "active_minutes": "exercise",
    "workouts": "exercise",
}


def weekly_activity(
    daily: Sequence[dict[str, Any]],
    *,
    output_start: date | None,
    today: date,
) -> list[dict[str, Any]]:
    if not daily:
        return []
    by_day = {date.fromisoformat(str(row["date"])): row for row in daily}
    first_data = min(by_day)
    first_visible = max(first_data, output_start) if output_start else first_data
    monday = first_visible - timedelta(days=first_visible.weekday())
    last_monday = today - timedelta(days=today.weekday())
    result: list[dict[str, Any]] = []
    while monday <= last_monday:
        sunday = monday + timedelta(days=6)
        visible_start = max(monday, first_visible)
        visible_end = min(sunday, today)
        expected_days: list[date] = []
        cursor = visible_start
        while cursor <= visible_end:
            expected_days.append(cursor)
            cursor += timedelta(days=1)
        row: dict[str, Any] = {
            "start_date": visible_start.isoformat(),
            "end_date": visible_end.isoformat(),
            "is_partial": visible_start != monday or monday == last_monday,
            "expected_days": len(expected_days),
            "baseline_window_start": (visible_start - timedelta(days=28)).isoformat(),
            "baseline_window_end": (visible_end - timedelta(days=7)).isoformat(),
            "coverage_days": {},
        }
        for metric in _ACTIVITY_METRICS:
            presence = _ACTIVITY_PRESENCE[metric]
            actual_values = [
                float(by_day[day][metric])
                for day in expected_days
                if day in by_day
                and presence in by_day[day]["_present"]
                and by_day[day][metric] is not None
            ]
            coverage = len(actual_values)
            row["coverage_days"][metric] = coverage
            actual = sum(actual_values) if actual_values else None
            baseline_values: list[float] = []
            baseline_complete = True
            for day in expected_days:
                samples: list[float] = []
                for weeks_back in range(1, 5):
                    previous = day - timedelta(days=7 * weeks_back)
                    prior = by_day.get(previous)
                    if (
                        prior is None
                        or presence not in prior["_present"]
                        or prior[metric] is None
                    ):
                        baseline_complete = False
                        break
                    samples.append(float(prior[metric]))
                if not baseline_complete:
                    break
                baseline_values.append(statistics.fmean(samples))
            baseline = sum(baseline_values) if baseline_complete and baseline_values else None
            actual_key = f"actual_{metric}"
            baseline_key = f"baseline_{metric}"
            row[actual_key] = round(actual, 3) if actual is not None else None
            row[baseline_key] = round(baseline, 3) if baseline is not None else None
            row[f"delta_{metric}_pct"] = (
                round((actual - baseline) / baseline * 100, 1)
                if actual is not None
                and baseline not in (None, 0)
                and coverage == len(expected_days)
                else None
            )
        row["actual_workout_minutes"] = row["actual_active_minutes"]
        row["baseline_workout_minutes"] = row["baseline_active_minutes"]
        row["workouts"] = row["actual_workouts"]
        result.append(row)
        monday += timedelta(days=7)
    return result


def aggregate_recovery_records(
    records: Iterable[HealthConnectRecord], tz: ZoneInfo
) -> list[dict[str, Any]]:
    grouped: dict[date, dict[str, Any]] = defaultdict(
        lambda: {
            "sleep_unstaged": [],
            "sleep_stages": defaultdict(float),
            "heart_rate": [],
            "heart_rate_weighted_sum": 0.0,
            "heart_rate_sample_count": 0,
            "heart_rate_minimum": None,
            "heart_rate_maximum": None,
            "resting_heart_rate": [],
            "hrv_rmssd": [],
            "oxygen_saturation": [],
            "vo2_max": [],
            "_present": set(),
        }
    )
    for row in records:
        if row.start_time is None:
            continue
        start = _aware(row.start_time)
        end = _aware(row.end_time or row.start_time)
        # Sleep is attributed to the wake-up date, matching how people read a
        # nightly summary. Other samples use their local sample date.
        day = (end if row.record_type == "sleep" else start).astimezone(tz).date()
        item = grouped[day]
        if row.record_type == "sleep":
            item["_present"].add("sleep")
            metrics = row.metrics or {}
            explicit_stages = {
                stage: float(metrics.get(f"{stage}_seconds", 0))
                for stage in (
                    "awake",
                    "sleeping",
                    "out_of_bed",
                    "light",
                    "deep",
                    "rem",
                    "unknown",
                )
                if f"{stage}_seconds" in metrics
            }
            if explicit_stages:
                for stage, seconds in explicit_stages.items():
                    item["sleep_stages"][stage] += seconds
            elif row.subtype:
                item["sleep_stages"][row.subtype] += float(
                    metrics.get("duration_seconds", row.primary_value or 0)
                )
            else:
                item["sleep_unstaged"].append(
                    float(metrics.get("duration_seconds", row.primary_value or 0))
                )
        elif row.record_type == "heart_rate":
            metrics = row.metrics or {}
            average_bpm = float(metrics.get("average_bpm", row.primary_value or 0))
            sample_count = int(metrics.get("sample_count", 1))
            minimum_bpm = float(metrics.get("minimum_bpm", average_bpm))
            maximum_bpm = float(metrics.get("maximum_bpm", average_bpm))
            item["heart_rate_weighted_sum"] += average_bpm * sample_count
            item["heart_rate_sample_count"] += sample_count
            item["heart_rate_minimum"] = (
                minimum_bpm
                if item["heart_rate_minimum"] is None
                else min(item["heart_rate_minimum"], minimum_bpm)
            )
            item["heart_rate_maximum"] = (
                maximum_bpm
                if item["heart_rate_maximum"] is None
                else max(item["heart_rate_maximum"], maximum_bpm)
            )
            item["_present"].add("heart_rate")
        elif row.record_type in RECOVERY_TYPES:
            item[row.record_type].append(float(row.primary_value or 0))
            item["_present"].add(row.record_type)

    result: list[dict[str, Any]] = []
    for day, item in sorted(grouped.items()):
        stages = item["sleep_stages"]
        has_stages = bool(stages)
        asleep_seconds = (
            sum(stages.get(name, 0) for name in ("sleeping", "light", "deep", "rem", "unknown"))
            if has_stages
            else sum(item["sleep_unstaged"])
        )
        time_in_bed = (
            sum(
                seconds
                for name, seconds in stages.items()
                if name != "out_of_bed"
            )
            if has_stages
            else sum(item["sleep_unstaged"])
        )

        def average(name: str) -> float | None:
            values = item[name]
            return round(statistics.fmean(values), 2) if values else None

        heart_count = item["heart_rate_sample_count"]
        result.append(
            {
                "date": day.isoformat(),
                "sleep_minutes": round(asleep_seconds / 60, 1)
                if "sleep" in item["_present"]
                else None,
                "time_in_bed_minutes": round(time_in_bed / 60, 1)
                if "sleep" in item["_present"]
                else None,
                "awake_minutes": round(stages.get("awake", 0) / 60, 1)
                if has_stages
                else None,
                "light_sleep_minutes": round(stages.get("light", 0) / 60, 1)
                if has_stages
                else None,
                "deep_sleep_minutes": round(stages.get("deep", 0) / 60, 1)
                if has_stages
                else None,
                "rem_sleep_minutes": round(stages.get("rem", 0) / 60, 1)
                if has_stages
                else None,
                "average_heart_rate_bpm": round(
                    item["heart_rate_weighted_sum"] / heart_count, 1
                )
                if heart_count
                else None,
                "minimum_heart_rate_bpm": round(item["heart_rate_minimum"], 1)
                if item["heart_rate_minimum"] is not None
                else None,
                "maximum_heart_rate_bpm": round(item["heart_rate_maximum"], 1)
                if item["heart_rate_maximum"] is not None
                else None,
                "resting_heart_rate_bpm": average("resting_heart_rate"),
                "hrv_rmssd_ms": average("hrv_rmssd"),
                "spo2_pct": average("oxygen_saturation"),
                "vo2_max": average("vo2_max"),
                "_present": set(item["_present"]),
            }
        )
    return result


def aggregate_hourly_heart_rate(
    records: Iterable[HealthConnectRecord], tz: ZoneInfo, start: date | None = None
) -> list[dict[str, Any]]:
    """Combine only persisted hourly aggregates; raw watch samples never leave ingest."""

    grouped: dict[datetime, dict[str, float | int]] = {}
    for row in records:
        if row.record_type != "heart_rate":
            continue
        hourly = (row.metrics or {}).get("hourly")
        if not isinstance(hourly, list):
            continue
        for bucket in hourly:
            if not isinstance(bucket, dict):
                continue
            try:
                at = _aware(datetime.fromisoformat(str(bucket["at"]).replace("Z", "+00:00")))
                count = int(bucket["sample_count"])
                average_bpm = float(bucket["average_bpm"])
                minimum_bpm = float(bucket["minimum_bpm"])
                maximum_bpm = float(bucket["maximum_bpm"])
            except (KeyError, TypeError, ValueError):
                continue
            local_hour = at.astimezone(tz).replace(minute=0, second=0, microsecond=0)
            if start is not None and local_hour.date() < start:
                continue
            item = grouped.setdefault(
                local_hour,
                {
                    "weighted_sum": 0.0,
                    "sample_count": 0,
                    "minimum_bpm": minimum_bpm,
                    "maximum_bpm": maximum_bpm,
                },
            )
            item["weighted_sum"] = float(item["weighted_sum"]) + average_bpm * count
            item["sample_count"] = int(item["sample_count"]) + count
            item["minimum_bpm"] = min(float(item["minimum_bpm"]), minimum_bpm)
            item["maximum_bpm"] = max(float(item["maximum_bpm"]), maximum_bpm)
    return [
        {
            "measured_at": hour.isoformat(),
            "average_bpm": round(float(item["weighted_sum"]) / int(item["sample_count"]), 1),
            "minimum_bpm": round(float(item["minimum_bpm"]), 1),
            "maximum_bpm": round(float(item["maximum_bpm"]), 1),
            "sample_count": int(item["sample_count"]),
        }
        for hour, item in sorted(grouped.items())
        if int(item["sample_count"]) > 0
    ]


_RECOVERY_WEEKLY_METRICS = (
    "sleep_minutes",
    "time_in_bed_minutes",
    "resting_heart_rate_bpm",
    "hrv_rmssd_ms",
    "spo2_pct",
    "vo2_max",
)
_RECOVERY_PRESENCE = {
    "sleep_minutes": "sleep",
    "time_in_bed_minutes": "sleep",
    "resting_heart_rate_bpm": "resting_heart_rate",
    "hrv_rmssd_ms": "hrv_rmssd",
    "spo2_pct": "oxygen_saturation",
    "vo2_max": "vo2_max",
}


def weekly_recovery(
    daily: Sequence[dict[str, Any]],
    *,
    output_start: date | None,
    today: date,
) -> list[dict[str, Any]]:
    if not daily:
        return []
    by_day = {date.fromisoformat(str(row["date"])): row for row in daily}
    first_data = min(by_day)
    first_visible = max(first_data, output_start) if output_start else first_data
    monday = first_visible - timedelta(days=first_visible.weekday())
    last_monday = today - timedelta(days=today.weekday())
    result: list[dict[str, Any]] = []
    while monday <= last_monday:
        sunday = monday + timedelta(days=6)
        visible_start = max(monday, first_visible)
        visible_end = min(sunday, today)
        days: list[date] = []
        cursor = visible_start
        while cursor <= visible_end:
            days.append(cursor)
            cursor += timedelta(days=1)
        item: dict[str, Any] = {
            "start_date": visible_start.isoformat(),
            "end_date": visible_end.isoformat(),
            "is_partial": visible_start != monday or monday == last_monday,
            "expected_days": len(days),
            "coverage_days": {},
        }
        for metric in _RECOVERY_WEEKLY_METRICS:
            presence = _RECOVERY_PRESENCE[metric]
            values = [
                float(by_day[day][metric])
                for day in days
                if day in by_day
                and presence in by_day[day]["_present"]
                and by_day[day][metric] is not None
            ]
            item["coverage_days"][metric] = len(values)
            item[f"average_{metric}"] = (
                round(statistics.fmean(values), 2) if values else None
            )
        heart_rows = [
            by_day[day]
            for day in days
            if day in by_day and "heart_rate" in by_day[day]["_present"]
        ]
        item["coverage_days"]["heart_rate"] = len(heart_rows)
        daily_averages = [
            float(row["average_heart_rate_bpm"])
            for row in heart_rows
            if row.get("average_heart_rate_bpm") is not None
        ]
        daily_minimums = [
            float(row["minimum_heart_rate_bpm"])
            for row in heart_rows
            if row.get("minimum_heart_rate_bpm") is not None
        ]
        daily_maximums = [
            float(row["maximum_heart_rate_bpm"])
            for row in heart_rows
            if row.get("maximum_heart_rate_bpm") is not None
        ]
        item["average_heart_rate_bpm"] = (
            round(statistics.fmean(daily_averages), 2) if daily_averages else None
        )
        item["minimum_heart_rate_bpm"] = (
            round(min(daily_minimums), 2) if daily_minimums else None
        )
        item["maximum_heart_rate_bpm"] = (
            round(max(daily_maximums), 2) if daily_maximums else None
        )
        result.append(item)
        monday += timedelta(days=7)
    return result


def _measurement_daily(db: Session, kind: str, tz: ZoneInfo) -> dict[date, float]:
    values: dict[date, list[float]] = defaultdict(list)
    rows = db.execute(
        select(MeasurementGroup.measured_at, Measurement.value)
        .join(Measurement, Measurement.group_id == MeasurementGroup.id)
        .where(Measurement.kind == kind)
        .order_by(MeasurementGroup.measured_at)
    )
    for measured_at, value in rows:
        day = _aware(measured_at).astimezone(tz).date()
        values[day].append(float(value))
    return {day: statistics.median(samples) for day, samples in values.items()}


def full_week_correlation(
    health_daily: Sequence[dict[str, Any]],
    *,
    health_metric: str,
    health_presence: str,
    target_daily: dict[date, float],
    target_name: str,
    today: date,
    minimum_weeks: int = CORRELATION_MINIMUM_WEEKS,
) -> dict[str, Any] | None:
    by_day = {date.fromisoformat(str(row["date"])): row for row in health_daily}
    if not by_day or not target_daily:
        return None
    first = min(min(by_day), min(target_daily))
    last = max(max(by_day), max(target_daily))
    monday = first - timedelta(days=first.weekday())
    pairs: list[tuple[float, float]] = []
    while monday + timedelta(days=6) < today and monday <= last:
        week = [monday + timedelta(days=offset) for offset in range(7)]
        health_values = []
        for day in week:
            row = by_day.get(day)
            if (
                row is None
                or health_presence not in row["_present"]
                or row.get(health_metric) is None
            ):
                health_values = []
                break
            health_values.append(float(row[health_metric]))
        target_values = [target_daily[day] for day in week if day in target_daily]
        if len(health_values) == 7 and target_values:
            pairs.append((statistics.fmean(health_values), statistics.fmean(target_values)))
        monday += timedelta(days=7)
    if len(pairs) < minimum_weeks:
        return None
    xs, ys = zip(*pairs, strict=True)
    if math.isclose(max(xs), min(xs)) or math.isclose(max(ys), min(ys)):
        return None
    coefficient = statistics.correlation(xs, ys)
    return {
        "metric": health_metric,
        "target": target_name,
        "coefficient": round(coefficient, 3),
        "full_overlapping_weeks": len(pairs),
        "disclaimer": CORRELATION_DISCLAIMER,
    }


def _activity_correlations(
    db: Session,
    daily: Sequence[dict[str, Any]],
    tz: ZoneInfo,
    today: date,
) -> list[dict[str, Any]]:
    weight = _measurement_daily(db, "weight", tz)
    systolic = _measurement_daily(db, "systolic", tz)
    requested = (
        ("steps", "steps", weight, "weight_kg"),
        ("active_minutes", "exercise", weight, "weight_kg"),
        ("steps", "steps", systolic, "systolic_mm_hg"),
    )
    return [
        result
        for metric, presence, target, target_name in requested
        if (
            result := full_week_correlation(
                daily,
                health_metric=metric,
                health_presence=presence,
                target_daily=target,
                target_name=target_name,
                today=today,
            )
        )
        is not None
    ]


def _recovery_correlations(
    db: Session,
    daily: Sequence[dict[str, Any]],
    tz: ZoneInfo,
    today: date,
) -> list[dict[str, Any]]:
    weight = _measurement_daily(db, "weight", tz)
    systolic = _measurement_daily(db, "systolic", tz)
    requested = (
        ("sleep_minutes", "sleep", weight, "weight_kg"),
        ("sleep_minutes", "sleep", systolic, "systolic_mm_hg"),
        ("resting_heart_rate_bpm", "resting_heart_rate", weight, "weight_kg"),
    )
    return [
        result
        for metric, presence, target, target_name in requested
        if (
            result := full_week_correlation(
                daily,
                health_metric=metric,
                health_presence=presence,
                target_daily=target,
                target_name=target_name,
                today=today,
            )
        )
        is not None
    ]


def _public_daily(rows: Sequence[dict[str, Any]], start: date | None) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        day = date.fromisoformat(str(row["date"]))
        if start is not None and day < start:
            continue
        result.append({key: value for key, value in row.items() if not key.startswith("_")})
    return result


def _meta(
    range_name: HealthRange,
    daily: Sequence[dict[str, Any]],
    tz: ZoneInfo,
) -> dict[str, Any]:
    return {
        "range": range_name,
        "from": daily[0]["date"] if daily else None,
        "to": daily[-1]["date"] if daily else None,
        "count": len(daily),
        "timezone": tz.key,
    }


def _activity_summary(
    daily: Sequence[dict[str, Any]], data_as_of: datetime | None
) -> dict[str, Any]:
    if not daily:
        return {
            "latest_date": None,
            "steps": None,
            "baseline_steps": None,
            "distance_km": None,
            "active_calories_kcal": None,
            "active_minutes": None,
            "workouts_7d": 0,
            "data_as_of": data_as_of.isoformat().replace("+00:00", "Z")
            if data_as_of
            else None,
        }
    by_day = {date.fromisoformat(str(row["date"])): row for row in daily}
    latest = daily[-1]
    latest_day = date.fromisoformat(str(latest["date"]))
    baseline_samples = [
        by_day[latest_day - timedelta(days=7 * weeks)]["steps"]
        for weeks in range(1, 5)
        if latest_day - timedelta(days=7 * weeks) in by_day
        and "steps" in by_day[latest_day - timedelta(days=7 * weeks)]["_present"]
        and by_day[latest_day - timedelta(days=7 * weeks)]["steps"] is not None
    ]
    recent_start = latest_day - timedelta(days=6)
    workouts = sum(
        int(row["workouts"] or 0)
        for day, row in by_day.items()
        if recent_start <= day <= latest_day and "exercise" in row["_present"]
    )
    return {
        "latest_date": latest["date"],
        "steps": latest["steps"],
        "baseline_steps": round(statistics.fmean(baseline_samples), 1)
        if len(baseline_samples) == 4
        else None,
        "distance_km": latest["distance_km"],
        "active_calories_kcal": latest["active_calories_kcal"],
        "active_minutes": latest["active_minutes"],
        "workouts_7d": workouts,
        "data_as_of": data_as_of.isoformat().replace("+00:00", "Z")
        if data_as_of
        else None,
    }


def _recovery_summary(
    daily: Sequence[dict[str, Any]], data_as_of: datetime | None
) -> dict[str, Any]:
    if not daily:
        return {
            "latest_date": None,
            "sleep_minutes": None,
            "baseline_sleep_minutes": None,
            "average_heart_rate_bpm": None,
            "minimum_heart_rate_bpm": None,
            "maximum_heart_rate_bpm": None,
            "resting_heart_rate_bpm": None,
            "baseline_resting_heart_rate_bpm": None,
            "hrv_rmssd_ms": None,
            "baseline_hrv_rmssd_ms": None,
            "spo2_pct": None,
            "data_as_of": data_as_of.isoformat().replace("+00:00", "Z")
            if data_as_of
            else None,
        }
    latest = daily[-1]
    latest_day = date.fromisoformat(str(latest["date"]))
    by_day = {date.fromisoformat(str(row["date"])): row for row in daily}

    def baseline(metric: str, presence: str) -> float | None:
        values: list[float] = []
        for offset in range(1, 29):
            row = by_day.get(latest_day - timedelta(days=offset))
            if row is None or presence not in row["_present"] or row.get(metric) is None:
                return None
            values.append(float(row[metric]))
        return round(statistics.fmean(values), 2)

    return {
        "latest_date": latest["date"],
        "sleep_minutes": latest["sleep_minutes"],
        "baseline_sleep_minutes": baseline("sleep_minutes", "sleep"),
        "average_heart_rate_bpm": latest["average_heart_rate_bpm"],
        "minimum_heart_rate_bpm": latest["minimum_heart_rate_bpm"],
        "maximum_heart_rate_bpm": latest["maximum_heart_rate_bpm"],
        "resting_heart_rate_bpm": latest["resting_heart_rate_bpm"],
        "baseline_resting_heart_rate_bpm": baseline(
            "resting_heart_rate_bpm", "resting_heart_rate"
        ),
        "hrv_rmssd_ms": latest["hrv_rmssd_ms"],
        "baseline_hrv_rmssd_ms": baseline("hrv_rmssd_ms", "hrv_rmssd"),
        "spo2_pct": latest["spo2_pct"],
        "data_as_of": data_as_of.isoformat().replace("+00:00", "Z")
        if data_as_of
        else None,
    }


def activity_series(
    db: Session,
    tz: ZoneInfo,
    range_name: HealthRange = "90d",
    now: datetime | None = None,
) -> dict[str, Any]:
    current = _aware(now or datetime.now(timezone.utc))
    today = current.astimezone(tz).date()
    start = _range_start(range_name, today)
    # Correlations use all history; the public daily series is restricted below.
    all_daily = aggregate_activity_records(_records(db, ACTIVITY_TYPES, tz), tz)
    visible = _public_daily(all_daily, start)
    available = sorted(
        {
            value
            for row in all_daily
            for value in row["_present"]
        }
    )
    data_as_of = _latest_data_as_of(db)
    return {
        "range": range_name,
        "data_as_of": data_as_of.isoformat().replace("+00:00", "Z") if data_as_of else None,
        "available_metrics": available,
        "summary": _activity_summary(all_daily, data_as_of),
        "daily": visible,
        "weekly": weekly_activity(all_daily, output_start=start, today=today),
        "correlations": _activity_correlations(db, all_daily, tz, today),
        "correlation_policy": {
            "minimum_full_overlapping_weeks": CORRELATION_MINIMUM_WEEKS,
            "disclaimer": CORRELATION_DISCLAIMER,
        },
        "meta": _meta(range_name, visible, tz),
    }


def recovery_series(
    db: Session,
    tz: ZoneInfo,
    range_name: HealthRange = "90d",
    now: datetime | None = None,
) -> dict[str, Any]:
    current = _aware(now or datetime.now(timezone.utc))
    today = current.astimezone(tz).date()
    start = _range_start(range_name, today)
    # Correlations deliberately use all available recovery history. Public
    # daily points are still restricted to the requested range below.
    recovery_records = _records(db, RECOVERY_TYPES, tz)
    all_daily = aggregate_recovery_records(recovery_records, tz)
    visible = _public_daily(all_daily, start)
    available = sorted(
        {
            value
            for row in all_daily
            for value in row["_present"]
        }
    )
    data_as_of = _latest_data_as_of(db)
    return {
        "range": range_name,
        "data_as_of": data_as_of.isoformat().replace("+00:00", "Z") if data_as_of else None,
        "available_metrics": available,
        "summary": _recovery_summary(all_daily, data_as_of),
        "daily": visible,
        "heart_rate_hourly": aggregate_hourly_heart_rate(recovery_records, tz, start),
        "weekly": weekly_recovery(all_daily, output_start=start, today=today),
        "correlations": _recovery_correlations(db, all_daily, tz, today),
        "correlation_policy": {
            "minimum_full_overlapping_weeks": CORRELATION_MINIMUM_WEEKS,
            "disclaimer": CORRELATION_DISCLAIMER,
        },
        "meta": _meta(range_name, visible, tz),
    }
