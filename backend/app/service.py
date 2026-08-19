from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .analytics import (
    PROGRAM_START,
    DailyPoint,
    PlanSpec,
    PressureReading,
    ValuePoint,
    build_insights,
    daily_medians,
    local_range_start,
    plan_weight,
    planned_target_date,
    pressure_sessions,
    pressure_statistics,
    theil_sen_forecast,
    trend_change,
    weekly_weight_points,
    weekly_weight_pressure_correlation,
)
from .models import Measurement, MeasurementGroup, Plan, SyncState


RangeName = Literal["program", "30d", "90d", "1y", "all"]
COMPOSITION_KINDS = ("fat_percent", "fat_mass", "fat_free_mass", "muscle_mass", "hydration", "bone_mass")


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _iso(value: datetime) -> str:
    return _aware(value).isoformat().replace("+00:00", "Z")


def series_meta(range_name: RangeName, points: list[dict[str, Any]], tz: ZoneInfo) -> dict[str, Any]:
    dates = [str(point["measured_at"]) for point in points if point.get("measured_at")]
    return {
        "range": range_name,
        "from": dates[0] if dates else None,
        "to": dates[-1] if dates else None,
        "count": len(points),
        "timezone": tz.key,
    }


def pressure_period(measured_at: datetime, tz: ZoneInfo) -> str:
    hour = _aware(measured_at).astimezone(tz).hour
    if 5 <= hour < 12:
        return "morning"
    if 18 <= hour < 24:
        return "evening"
    return "other"


def flat_pressure_stats(payload: dict[str, Any]) -> dict[str, Any]:
    systolic = payload.get("systolic") or {}
    diastolic = payload.get("diastolic") or {}
    pulse = payload.get("pulse") or {}
    return {
        "avg_systolic": systolic.get("mean"),
        "avg_diastolic": diastolic.get("mean"),
        "avg_pulse": pulse.get("mean"),
        "min_systolic": systolic.get("min"),
        "max_systolic": systolic.get("max"),
        "min_diastolic": diastolic.get("min"),
        "max_diastolic": diastolic.get("max"),
        "variability_systolic": systolic.get("variability"),
        "variability_diastolic": diastolic.get("variability"),
        "sessions": payload.get("sessions", 0),
    }


def insight_tone(rule: str, priority: int) -> str:
    if rule.startswith("milestone"):
        return "achievement"
    if rule == "on_plan":
        return "positive"
    if priority >= 70:
        return "attention"
    return "neutral"


def active_plan(db: Session) -> PlanSpec:
    row = db.scalar(select(Plan).where(Plan.active.is_(True)).order_by(Plan.created_at.desc()))
    if row is None:
        return PlanSpec()
    return PlanSpec(
        start_date=row.start_date,
        start_weight_kg=float(row.start_weight_kg),
        monthly_change_kg=float(row.monthly_change_kg),
        target_weight_kg=float(row.target_weight_kg),
        version=row.version,
    )


def ensure_default_plan(db: Session) -> Plan:
    existing = db.scalar(select(Plan).where(Plan.version == PlanSpec().version))
    if existing:
        return existing
    db.query(Plan).update({Plan.active: False})
    spec = PlanSpec()
    row = Plan(
        version=spec.version,
        start_date=spec.start_date,
        start_weight_kg=Decimal(str(spec.start_weight_kg)),
        monthly_change_kg=Decimal(str(spec.monthly_change_kg)),
        target_weight_kg=Decimal(str(spec.target_weight_kg)),
        active=True,
    )
    db.add(row)
    db.commit()
    return row


def value_points(
    db: Session, kind: str, tz: ZoneInfo, start: date | None = None
) -> list[ValuePoint]:
    query = (
        select(Measurement.value, MeasurementGroup.measured_at, MeasurementGroup.id)
        .join(MeasurementGroup, Measurement.group_id == MeasurementGroup.id)
        .where(Measurement.kind == kind)
        .order_by(MeasurementGroup.measured_at, Measurement.id)
    )
    if start is not None:
        local_start = datetime.combine(start, time.min, tzinfo=tz).astimezone(timezone.utc)
        query = query.where(MeasurementGroup.measured_at >= local_start)
    return [ValuePoint(measured_at, float(value), group_id) for value, measured_at, group_id in db.execute(query)]


def weight_daily(db: Session, tz: ZoneInfo, start: date | None = None) -> list[DailyPoint]:
    return daily_medians(value_points(db, "weight", tz, start), tz, since=start)


def pressure_readings(
    db: Session, tz: ZoneInfo, start: date | None = None
) -> list[PressureReading]:
    query = (
        select(
            MeasurementGroup.id,
            MeasurementGroup.measured_at,
            Measurement.kind,
            Measurement.value,
        )
        .join(Measurement, Measurement.group_id == MeasurementGroup.id)
        .where(Measurement.kind.in_(("systolic", "diastolic", "pulse")))
        .order_by(MeasurementGroup.measured_at, Measurement.id)
    )
    if start is not None:
        local_start = datetime.combine(start, time.min, tzinfo=tz).astimezone(timezone.utc)
        query = query.where(MeasurementGroup.measured_at >= local_start)
    grouped: dict[int, dict[str, Any]] = {}
    for group_id, measured_at, kind, value in db.execute(query):
        item = grouped.setdefault(group_id, {"measured_at": measured_at})
        item[kind] = float(value)
    return [PressureReading(**item) for item in grouped.values()]


def weight_series(db: Session, tz: ZoneInfo, range_name: RangeName, now: datetime | None = None) -> dict[str, Any]:
    today = _aware(now or datetime.now(timezone.utc)).astimezone(tz).date()
    plan = active_plan(db)
    start = plan.start_date if range_name == "program" else local_range_start(range_name, tz, now)
    raw = value_points(db, "weight", tz, start)
    daily = daily_medians(raw, tz, since=start)
    # The visible range is only a presentation choice. Forecasts always use
    # program data, so pre-program history can never make a forecast reliable
    # and a short display range cannot hide useful program samples.
    program_daily = daily if start == plan.start_date else weight_daily(db, tz, plan.start_date)
    forecast = theil_sen_forecast(program_daily, plan.target_weight_kg, as_of=today)
    clean_program_daily = [point for point in program_daily if not point.is_outlier]
    forecast_anchor = clean_program_daily[-1] if clean_program_daily else None
    residual_band = None
    can_project = (
        forecast.reliable
        and forecast.slope_kg_per_day is not None
        and forecast.origin_date is not None
        and forecast.intercept_kg is not None
        and forecast_anchor is not None
    )

    def estimate(day: date) -> float | None:
        if not can_project:
            return None
        assert forecast.origin_date is not None
        assert forecast.intercept_kg is not None
        assert forecast.slope_kg_per_day is not None
        return forecast.intercept_kg + forecast.slope_kg_per_day * (
            day - forecast.origin_date
        ).days

    forecast_window_start = (
        max(plan.start_date, forecast_anchor.day - timedelta(days=41))
        if forecast_anchor
        else None
    )
    if can_project and forecast_window_start is not None:
        sample = [point for point in clean_program_daily if point.day >= forecast_window_start]
        fitted = [
            estimate(point.day)
            for point in sample
        ]
        residuals = [
            abs(point.value - fit)
            for point, fit in zip(sample, fitted, strict=True)
            if fit is not None
        ]
        residual_band = round(sorted(residuals)[len(residuals) // 2] * 1.4826, 3)

    projection: list[dict[str, Any]] = []
    if can_project and forecast.target_date is not None and forecast_anchor is not None:
        projection_days: list[date] = []
        cursor = forecast_anchor.day
        while cursor < forecast.target_date:
            projection_days.append(cursor)
            cursor += timedelta(days=7)
        if not projection_days or projection_days[-1] != forecast.target_date:
            projection_days.append(forecast.target_date)
        for day in projection_days:
            predicted = estimate(day)
            if predicted is None:
                continue
            predicted = max(plan.target_weight_kg, predicted)
            projection.append(
                {
                    "measured_at": day.isoformat(),
                    "forecast_kg": round(predicted, 3),
                    "forecast_low_kg": (
                        round(max(plan.target_weight_kg, predicted - residual_band), 3)
                        if residual_band is not None
                        else None
                    ),
                    "forecast_high_kg": (
                        round(predicted + residual_band, 3)
                        if residual_band is not None
                        else None
                    ),
                }
            )
    plan_projection: list[dict[str, Any]] = []
    # The progress view compares both complete trajectories. Other historical
    # ranges stop at today so their axes remain focused on the requested data.
    plan_end = (
        planned_target_date(plan)
        if range_name == "program"
        else min(today, planned_target_date(plan))
    )
    plan_start = max(plan.start_date, start or plan.start_date)
    if plan_start <= plan_end:
        cursor = plan_start
        while cursor <= plan_end:
            plan_projection.append(
                {
                    "measured_at": cursor.isoformat(),
                    "planned_kg": plan_weight(cursor, plan),
                }
            )
            cursor += timedelta(days=1)
    points = [
        {
            "measured_at": point.day.isoformat(),
            "weight_kg": point.value,
            "smoothed_7d_kg": point.rolling_7d,
            "planned_kg": plan_weight(point.day, plan) if point.day >= plan.start_date else None,
            "forecast_kg": (
                round(estimate(point.day), 3)
                if can_project and forecast_window_start is not None and point.day >= forecast_window_start
                else None
            ),
            "forecast_low_kg": (
                round(estimate(point.day) - residual_band, 3)
                if can_project
                and forecast_window_start is not None
                and point.day >= forecast_window_start
                and residual_band is not None
                else None
            ),
            "forecast_high_kg": (
                round(estimate(point.day) + residual_band, 3)
                if can_project
                and forecast_window_start is not None
                and point.day >= forecast_window_start
                and residual_band is not None
                else None
            ),
            "is_outlier": point.is_outlier,
            "sample_count": point.sample_count,
        }
        for point in daily
    ]
    return {
        "range": range_name,
        "unit": "kg",
        "points": points,
        "weekly": weekly_weight_points(program_daily, plan, today),
        "projection": projection,
        "plan_projection": plan_projection,
        "meta": series_meta(range_name, points, tz),
        "raw": [
            {"measured_at": _iso(point.measured_at), "value": round(point.value, 3)} for point in raw
        ],
        "daily": [
            {
                "date": point.day.isoformat(),
                "value": point.value,
                "sample_count": point.sample_count,
                "is_outlier": point.is_outlier,
                "rolling_7d": point.rolling_7d,
                "planned": plan_weight(point.day, plan) if point.day >= plan.start_date else None,
            }
            for point in daily
        ],
        "plan": {
            "version": plan.version,
            "start_date": plan.start_date.isoformat(),
            "start_weight_kg": plan.start_weight_kg,
            "target_weight_kg": plan.target_weight_kg,
            "planned_target_date": planned_target_date(plan).isoformat(),
        },
        "forecast": forecast_dict(forecast),
    }


def forecast_dict(value: Any) -> dict[str, Any]:
    return {
        "reliable": value.reliable,
        "reason": value.reason,
        "slope_kg_per_day": value.slope_kg_per_day,
        "weekly_change_kg": value.weekly_change_kg,
        "target_date": value.target_date.isoformat() if value.target_date else None,
        "confidence_start": value.confidence_start.isoformat() if value.confidence_start else None,
        "confidence_end": value.confidence_end.isoformat() if value.confidence_end else None,
        "samples": value.samples,
        "span_days": value.span_days,
    }


def pressure_series(
    db: Session, tz: ZoneInfo, range_name: RangeName, now: datetime | None = None
) -> dict[str, Any]:
    start = local_range_start(range_name, tz, now)
    sessions = pressure_sessions(pressure_readings(db, tz, start))
    correlation_sessions = sessions if start is None else pressure_sessions(pressure_readings(db, tz))
    all_weights = weight_daily(db, tz, PROGRAM_START)
    points = [
        {
            "measured_at": _iso(item.measured_at),
            "systolic": item.systolic,
            "diastolic": item.diastolic,
            "pulse": item.pulse,
            "pulse_pressure": item.pulse_pressure,
            "session_size": item.sample_count,
            "sample_count": item.sample_count,
            "period_of_day": pressure_period(item.measured_at, tz),
        }
        for item in sessions
        if item.systolic is not None and item.diastolic is not None
    ]
    statistics_payload = pressure_statistics(sessions, tz, now)
    return {
        "range": range_name,
        "units": {"systolic": "mmHg", "diastolic": "mmHg", "pulse": "bpm"},
        "points": points,
        "meta": series_meta(range_name, points, tz),
        "sessions": points,
        "statistics": statistics_payload,
        "stats_7d": flat_pressure_stats(statistics_payload["last_7_days"]),
        "stats_30d": flat_pressure_stats(statistics_payload["last_30_days"]),
        "weight_pressure_correlation": weekly_weight_pressure_correlation(
            all_weights, correlation_sessions, tz
        ),
        "disclaimer": "Статистика носит описательный характер и не является медицинской рекомендацией.",
    }


def composition_series(
    db: Session, tz: ZoneInfo, range_name: RangeName, now: datetime | None = None
) -> dict[str, Any]:
    start = local_range_start(range_name, tz, now)
    query = (
        select(
            MeasurementGroup.id,
            MeasurementGroup.measured_at,
            Measurement.kind,
            Measurement.value,
            Measurement.unit,
        )
        .join(Measurement, Measurement.group_id == MeasurementGroup.id)
        .where(Measurement.kind.in_(COMPOSITION_KINDS))
        .order_by(MeasurementGroup.measured_at, Measurement.id)
    )
    if start is not None:
        local_start = datetime.combine(start, time.min, tzinfo=tz).astimezone(timezone.utc)
        query = query.where(MeasurementGroup.measured_at >= local_start)
    series: dict[str, list[dict[str, Any]]] = {kind: [] for kind in COMPOSITION_KINDS}
    grouped: dict[int, dict[str, Any]] = {}
    units: dict[str, str] = {}
    for group_id, measured_at, kind, value, unit in db.execute(query):
        series[kind].append({"measured_at": _iso(measured_at), "value": round(float(value), 3)})
        units[kind] = unit
        point = grouped.setdefault(
            group_id,
            {
                "measured_at": _iso(measured_at),
                "fat_pct": None,
                "fat_mass_kg": None,
                "lean_mass_kg": None,
            },
        )
        target = {
            "fat_percent": "fat_pct",
            "fat_mass": "fat_mass_kg",
            "fat_free_mass": "lean_mass_kg",
        }.get(kind)
        if target:
            point[target] = round(float(value), 3)
    points = sorted(
        [point for point in grouped.values() if any(point[key] is not None for key in ("fat_pct", "fat_mass_kg", "lean_mass_kg"))],
        key=lambda point: point["measured_at"],
    )
    return {
        "range": range_name,
        "points": points,
        "meta": series_meta(range_name, points, tz),
        "series": series,
        "units": units,
        "bia_note": "Показатели состава тела — приблизительные BIA-оценки.",
    }


def overview(db: Session, tz: ZoneInfo, now: datetime | None = None) -> dict[str, Any]:
    current = _aware(now or datetime.now(timezone.utc))
    today = current.astimezone(tz).date()
    plan = active_plan(db)
    daily = weight_daily(db, tz, plan.start_date)
    latest_raw = value_points(db, "weight", tz, plan.start_date)[-1:] or [None]
    latest_daily = daily[-1] if daily else None
    forecast = theil_sen_forecast(daily, plan.target_weight_kg, as_of=today)
    current_trend = latest_daily.rolling_7d if latest_daily else None
    latest_age_days = max(0, (today - latest_daily.day).days) if latest_daily else None
    weight_is_stale = latest_age_days is not None and latest_age_days > 14
    planned = plan_weight(today, plan)
    measured_days_14 = len([item for item in daily if item.day >= today - timedelta(days=13)])
    measured_days_30 = len([item for item in daily if item.day >= today - timedelta(days=29)])
    state = db.get(SyncState, "withings")
    pressure_payload = pressure_series(db, tz, "all", current)
    latest_pressure = pressure_payload["points"][-1] if pressure_payload["points"] else None
    pressure_7d = pressure_payload["stats_7d"]
    pressure_30d = pressure_payload["stats_30d"]
    composition_payload = composition_series(db, tz, "all", current)
    latest_composition = composition_payload["points"][-1] if composition_payload["points"] else None
    generated_insights = build_insights(daily, plan, today)
    sync_status = "unknown"
    if state and state.last_error:
        sync_status = "error"
    elif state and state.last_success_at:
        sync_status = "delayed" if current - _aware(state.last_success_at) > timedelta(minutes=15) else "ok"
    nested_plan = {
        "version": plan.version,
        "start_date": plan.start_date.isoformat(),
        "start_weight_kg": plan.start_weight_kg,
        "target_weight_kg": plan.target_weight_kg,
        "target_date": planned_target_date(plan).isoformat(),
        "planned_target_date": planned_target_date(plan).isoformat(),
        "planned_today_kg": planned,
    }
    nested_weight = {
        "latest_kg": round(latest_raw[0].value, 3) if latest_raw[0] else None,
        "latest_at": _iso(latest_raw[0].measured_at) if latest_raw[0] else None,
        "smoothed_7d_kg": current_trend,
        "change_since_start_kg": round(current_trend - plan.start_weight_kg, 3) if current_trend is not None else None,
        "deviation_from_plan_kg": (
            round(current_trend - planned, 3)
            if current_trend is not None and planned is not None and not weight_is_stale
            else None
        ),
        "progress_pct": (
            round(max(0.0, min(100.0, (plan.start_weight_kg - current_trend) / (plan.start_weight_kg - plan.target_weight_kg) * 100)), 1)
            if current_trend is not None
            else None
        ),
        "trend_28d_kg": trend_change(daily, 28),
        "trend_42d_kg": trend_change(daily, 42),
        "forecast_date": forecast.target_date.isoformat() if forecast.reliable and forecast.target_date else None,
        "measurement_days_30d": measured_days_30,
        "latest_age_days": latest_age_days,
        "is_stale": weight_is_stale,
    }
    return {
        "generated_at": _iso(current),
        "weight": nested_weight,
        "pressure": {
            "latest_systolic": latest_pressure["systolic"] if latest_pressure else None,
            "latest_diastolic": latest_pressure["diastolic"] if latest_pressure else None,
            "latest_pulse": latest_pressure["pulse"] if latest_pressure else None,
            "latest_at": latest_pressure["measured_at"] if latest_pressure else None,
            "avg_7d_systolic": pressure_7d["avg_systolic"],
            "avg_7d_diastolic": pressure_7d["avg_diastolic"],
            "avg_30d_systolic": pressure_30d["avg_systolic"],
            "avg_30d_diastolic": pressure_30d["avg_diastolic"],
        },
        "composition": {
            "fat_pct": latest_composition["fat_pct"] if latest_composition else None,
            "fat_mass_kg": latest_composition["fat_mass_kg"] if latest_composition else None,
            "lean_mass_kg": latest_composition["lean_mass_kg"] if latest_composition else None,
            "measured_at": latest_composition["measured_at"] if latest_composition else None,
            "bia_estimate": True,
        },
        "latest_weight": (
            {
                "value": round(latest_raw[0].value, 3),
                "measured_at": _iso(latest_raw[0].measured_at),
                "unit": "kg",
            }
            if latest_raw[0]
            else None
        ),
        "rolling_7d_kg": current_trend,
        "change_from_start_kg": (
            round(current_trend - plan.start_weight_kg, 3) if current_trend is not None else None
        ),
        "goal_progress_percent": (
            round(
                max(
                    0.0,
                    min(
                        100.0,
                        (plan.start_weight_kg - current_trend)
                        / (plan.start_weight_kg - plan.target_weight_kg)
                        * 100,
                    ),
                ),
                1,
            )
            if current_trend is not None
            else None
        ),
        "planned_weight_today_kg": planned,
        "plan_delta_kg": (
            round(current_trend - planned, 3)
            if current_trend is not None and planned is not None and not weight_is_stale
            else None
        ),
        "change_28d_kg": trend_change(daily, 28),
        "change_42d_kg": trend_change(daily, 42),
        "measurement_days_last_14": measured_days_14,
        "forecast": forecast_dict(forecast),
        "plan": nested_plan,
        "sync": {
            "status": sync_status,
            "last_success_at": _iso(state.last_success_at) if state and state.last_success_at else None,
            "next_sync_at": (
                _iso(_aware(state.last_success_at) + timedelta(minutes=5))
                if state and state.last_success_at
                else None
            ),
            "source": "Withings Cloud",
            "initial_import_done": state.initial_import_done if state else False,
            "has_error": bool(state and state.last_error),
        },
        "insights": [
            {
                "id": item["id"],
                "title": item["title"],
                "text": item["message"],
                "tone": insight_tone(str(item["rule"]), int(item["priority"])),
                "created_at": _iso(current),
            }
            for item in generated_insights
        ],
    }


def insights(db: Session, tz: ZoneInfo, now: datetime | None = None) -> dict[str, Any]:
    current = _aware(now or datetime.now(timezone.utc))
    plan = active_plan(db)
    daily = weight_daily(db, tz, plan.start_date)
    return {
        "generated_at": _iso(current),
        "items": build_insights(daily, plan, current.astimezone(tz).date()),
        "rules_based": True,
    }
