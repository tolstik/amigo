from __future__ import annotations

from datetime import date, datetime, timezone
import math
import statistics
from hashlib import sha256
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .ai_contracts import AnalysisSnapshot, SnapshotFact, SnapshotLabResult, SnapshotPoint, SnapshotSeries
from .ai_queue import AnalysisTrigger, enqueue_analysis
from .config import Settings
from .health_analytics import activity_series, recovery_series
from .lab_models import LabResult
from .service import overview, pressure_series, weight_series


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    if not math.isfinite(result):
        return None
    return int(result) if result.is_integer() else round(result, 3)


def _day(value: Any) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _median(rows: list[dict[str, Any]], key: str, limit: int = 28) -> float | None:
    values = [_number(row.get(key)) for row in rows[-limit:]]
    clean = [float(value) for value in values if value is not None]
    return round(statistics.median(clean), 3) if clean else None


def _metric_key_part(value: Any) -> str:
    return "".join(
        character if character.isalnum() else "_"
        for character in str(value or "").lower()
    ).strip("_")


def _correlation_scope(source_metric: str, target_metric: str) -> str:
    metrics = (source_metric, target_metric)
    tokens = [set(metric.split("_")) for metric in metrics]
    if any(
        "systolic" in parts
        or "diastolic" in parts
        or {"blood", "pressure"} <= parts
        for parts in tokens
    ):
        return "pressure"
    if any(
        "vo2" in parts
        for parts in tokens
    ):
        return "vo2"
    if any(
        "spo2" in parts
        or "oxygen" in parts
        or "saturation" in parts
        for parts in tokens
    ):
        return "oxygen"
    if any(
        "pulse" in parts
        or "hrv" in parts
        or {"heart", "rate"} <= parts
        for parts in tokens
    ):
        return "heart"
    return "correlation"


def _series(
    key: str,
    scope: str,
    unit: str,
    rows: Iterable[dict[str, Any]],
    date_key: str,
    value_key: str,
) -> SnapshotSeries | None:
    by_day: dict[date, float] = {}
    for row in rows:
        observed = _day(row.get(date_key))
        value = _number(row.get(value_key))
        if observed is not None and value is not None:
            by_day[observed] = float(value)
    if not by_day:
        return None
    return SnapshotSeries(
        key=key,
        scope=scope,
        unit=unit,
        points=[SnapshotPoint(day=day, value=value) for day, value in sorted(by_day.items())][-120:],
    )


def build_analysis_snapshot(
    db: Session,
    tz: ZoneInfo,
    now: datetime | None = None,
    *,
    user_height_cm: float = 176.0,
) -> AnalysisSnapshot:
    current = now or datetime.now(timezone.utc)
    current = current.replace(tzinfo=timezone.utc) if current.tzinfo is None else current
    today = current.astimezone(tz).date()
    summary = overview(db, tz, current)
    weights = weight_series(db, tz, "90d", current)
    pressures = pressure_series(db, tz, "90d", current)
    activity = activity_series(db, tz, "90d", current)
    recovery = recovery_series(db, tz, "90d", current)
    activity_rows = list(activity.get("daily") or [])
    recovery_rows = list(recovery.get("daily") or [])
    latest_activity = activity_rows[-1] if activity_rows else {}
    latest_recovery = recovery_rows[-1] if recovery_rows else {}
    latest_week = (activity.get("weekly") or [{}])[-1]

    facts: list[SnapshotFact] = []

    def fact(
        key: str,
        scope: str,
        period: str,
        value: Any,
        unit: str,
        observed_on: date | None = None,
    ) -> None:
        numeric = _number(value)
        if numeric is None:
            return
        facts.append(
            SnapshotFact(
                key=key,
                scope=scope,
                period=period,
                value=numeric,
                unit=unit,
                observed_on=observed_on,
            )
        )

    weight = summary["weight"]
    plan = summary["plan"]
    fact("weight.latest", "weight", "current", weight.get("latest_kg"), "kg", _day(weight.get("latest_at")))
    if user_height_cm > 0:
        fact("profile.height_cm", "profile", "current", user_height_cm, "centimeters")
    latest_weight = _number(weight.get("latest_kg"))
    if latest_weight is not None and user_height_cm > 0:
        height_m = user_height_cm / 100
        fact(
            "weight.bmi_latest",
            "weight",
            "current",
            round(float(latest_weight) / (height_m * height_m), 2),
            "kg_m2",
            _day(weight.get("latest_at")),
        )
    fact("weight.trend7d", "weight", "7d", weight.get("smoothed_7d_kg"), "kg")
    fact("weight.change28d", "weight", "28d", weight.get("trend_28d_kg"), "kg")
    fact("weight.plan_today", "weight", "day", plan.get("planned_today_kg"), "kg", today)
    fact("weight.plan_delta", "weight", "day", weight.get("deviation_from_plan_kg"), "kg", today)
    fact("weight.measurement_days30d", "quality", "30d", weight.get("measurement_days_30d"), "days")

    composition = summary["composition"]
    composition_day = _day(composition.get("measured_at"))
    fact("composition.fat_percent", "composition", "current", composition.get("fat_pct"), "percent", composition_day)
    fact("composition.fat_mass", "composition", "current", composition.get("fat_mass_kg"), "kg", composition_day)
    fact("composition.lean_mass", "composition", "current", composition.get("lean_mass_kg"), "kg", composition_day)

    pressure = summary["pressure"]
    pressure_day = _day(pressure.get("latest_at"))
    fact("pressure.latest_systolic", "pressure", "current", pressure.get("latest_systolic"), "mmhg", pressure_day)
    fact("pressure.latest_diastolic", "pressure", "current", pressure.get("latest_diastolic"), "mmhg", pressure_day)
    fact("pressure.average7d_systolic", "pressure", "7d", pressure.get("avg_7d_systolic"), "mmhg")
    fact("pressure.average7d_diastolic", "pressure", "7d", pressure.get("avg_7d_diastolic"), "mmhg")
    fact("pressure.average30d_systolic", "pressure", "30d", pressure.get("avg_30d_systolic"), "mmhg")
    fact("pressure.average30d_diastolic", "pressure", "30d", pressure.get("avg_30d_diastolic"), "mmhg")

    activity_day = _day(latest_activity.get("date"))
    fact("activity.steps_latest", "activity", "day", latest_activity.get("steps"), "steps", activity_day)
    fact("activity.distance_latest", "activity", "day", latest_activity.get("distance_km"), "km", activity_day)
    fact("activity.active_minutes_latest", "activity", "day", latest_activity.get("active_minutes"), "minutes", activity_day)
    fact("activity.workouts_latest", "activity", "day", latest_activity.get("workouts"), "count", activity_day)
    fact("activity.steps_week", "activity", "week", latest_week.get("actual_steps"), "steps", _day(latest_week.get("end_date")))
    fact("activity.steps_baseline28d", "activity", "28d", latest_week.get("baseline_steps"), "steps", _day(latest_week.get("end_date")))
    coverage = latest_week.get("coverage_days")
    if isinstance(coverage, dict):
        fact("quality.activity_days_week", "quality", "week", coverage.get("steps"), "days")

    recovery_day = _day(latest_recovery.get("date"))
    fact("sleep.duration_latest", "sleep", "day", latest_recovery.get("sleep_minutes"), "minutes", recovery_day)
    fact("sleep.duration_baseline28d", "sleep", "28d", _median(recovery_rows, "sleep_minutes"), "minutes")
    fact("recovery.heart_rate_average_latest", "heart", "day", latest_recovery.get("average_heart_rate_bpm"), "bpm", recovery_day)
    fact("recovery.heart_rate_minimum_latest", "heart", "day", latest_recovery.get("minimum_heart_rate_bpm"), "bpm", recovery_day)
    fact("recovery.heart_rate_maximum_latest", "heart", "day", latest_recovery.get("maximum_heart_rate_bpm"), "bpm", recovery_day)
    fact("recovery.heart_rate_average_baseline28d", "heart", "28d", _median(recovery_rows, "average_heart_rate_bpm"), "bpm")
    fact("recovery.resting_heart_rate_latest", "heart", "day", latest_recovery.get("resting_heart_rate_bpm"), "bpm", recovery_day)
    fact("recovery.resting_heart_rate_baseline28d", "heart", "28d", _median(recovery_rows, "resting_heart_rate_bpm"), "bpm")
    fact("recovery.hrv_latest", "heart", "day", latest_recovery.get("hrv_rmssd_ms"), "milliseconds", recovery_day)
    fact("recovery.hrv_baseline28d", "heart", "28d", _median(recovery_rows, "hrv_rmssd_ms"), "milliseconds")
    fact(
        "recovery.spo2_latest",
        "oxygen",
        "day",
        latest_recovery.get("spo2_pct", latest_recovery.get("oxygen_saturation_pct")),
        "percent",
        recovery_day,
    )
    fact(
        "recovery.vo2max_latest",
        "vo2",
        "current",
        latest_recovery.get("vo2_max", latest_recovery.get("vo2_max_ml_kg_min")),
        "ml_kg_min",
        recovery_day,
    )

    for family, payload in (("activity", activity), ("recovery", recovery)):
        correlations = payload.get("correlations")
        if not isinstance(correlations, list):
            continue
        for index, item in enumerate(correlations[:4]):
            if not isinstance(item, dict):
                continue
            coefficient = item.get("coefficient")
            source_metric = _metric_key_part(
                item.get("metric") or item.get("source_metric") or index
            )
            target_metric = _metric_key_part(item.get("target") or "unknown")
            if source_metric and target_metric:
                fact(
                    f"correlation.{family}_{source_metric}_to_{target_metric}",
                    _correlation_scope(source_metric, target_metric),
                    "all",
                    coefficient,
                    "coefficient",
                )

    series_candidates = [
        _series("weight.daily90d", "weight", "kg", weights.get("points") or [], "measured_at", "weight_kg"),
        _series("weight.plan90d", "weight", "kg", weights.get("points") or [], "measured_at", "planned_kg"),
        _series("pressure.systolic90d", "pressure", "mmhg", pressures.get("points") or [], "measured_at", "systolic"),
        _series("pressure.diastolic90d", "pressure", "mmhg", pressures.get("points") or [], "measured_at", "diastolic"),
        _series("activity.steps90d", "activity", "steps", activity_rows, "date", "steps"),
        _series("activity.active_minutes90d", "activity", "minutes", activity_rows, "date", "active_minutes"),
        _series("sleep.duration90d", "sleep", "minutes", recovery_rows, "date", "sleep_minutes"),
        _series("recovery.heart_rate_average90d", "heart", "bpm", recovery_rows, "date", "average_heart_rate_bpm"),
        _series("recovery.resting_heart_rate90d", "heart", "bpm", recovery_rows, "date", "resting_heart_rate_bpm"),
        _series("recovery.hrv90d", "heart", "milliseconds", recovery_rows, "date", "hrv_rmssd_ms"),
        _series(
            "recovery.spo290d",
            "oxygen",
            "percent",
            recovery_rows,
            "date",
            "spo2_pct",
        ),
    ]

    source_candidates = [
        _timestamp(weight.get("latest_at")),
        _timestamp(pressure.get("latest_at")),
        _timestamp(composition.get("measured_at")),
        _timestamp(activity.get("data_as_of")),
        _timestamp(recovery.get("data_as_of")),
    ]
    laboratory_rows = [] if db is None else list(
        db.scalars(
            select(LabResult)
            .where(LabResult.deleted.is_(False), LabResult.observed_on.is_not(None))
            .order_by(LabResult.observed_on.desc(), LabResult.created_at.desc())
            .limit(240)
        )
    )
    laboratory = [
        SnapshotLabResult(
            key=f"lab.{sha256(row.id.encode()).hexdigest()[:20]}",
            analyte=row.analyte_name,
            value_numeric=float(row.value_numeric) if row.value_numeric is not None else None,
            value_text=row.value_text,
            comparator=row.comparator,
            unit=row.unit,
            observed_on=row.observed_on,
            reference_low=float(row.reference_low) if row.reference_low is not None else None,
            reference_high=float(row.reference_high) if row.reference_high is not None else None,
            reference_text=row.reference_text,
            reference_source=row.reference_source,
            status=row.status,
            verified=row.verification_status == "verified",
        )
        for row in reversed(laboratory_rows)
    ]
    source_candidates.extend(
        row.updated_at.astimezone(timezone.utc)
        for row in laboratory_rows
        if row.updated_at is not None
    )
    source_through = max((value for value in source_candidates if value is not None), default=current)
    if not facts and not any(series_candidates) and not laboratory:
        facts.append(
            SnapshotFact(
                key="quality.no_health_data",
                scope="quality",
                period="current",
                value=True,
                unit="boolean",
                observed_on=today,
            )
        )
    return AnalysisSnapshot(
        source_through=source_through,
        facts=facts,
        series=[value for value in series_candidates if value is not None],
        labs=laboratory,
    )


def enqueue_current_analysis(
    db: Session,
    settings: Settings,
    *,
    trigger: AnalysisTrigger,
    now: datetime | None = None,
    debounce_seconds: int | None = None,
    retry_terminal: bool = False,
):
    """Create one minimized snapshot and enqueue it without contacting Codex."""

    if not settings.ai_enabled:
        return None
    snapshot = build_analysis_snapshot(
        db,
        settings.tz,
        now,
        user_height_cm=settings.user_height_cm,
    )
    return enqueue_analysis(
        db,
        snapshot,
        trigger=trigger,
        now=now,
        debounce_seconds=(
            settings.ai_debounce_seconds if debounce_seconds is None else debounce_seconds
        ),
        activity_min_interval_seconds=settings.ai_activity_min_interval_seconds,
        stale_seconds=settings.ai_stale_seconds,
        retry_terminal=retry_terminal,
    )
