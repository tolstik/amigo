"""Deterministic journal, milestone, plateau and maintenance calculations.

Everything here is derived from stored measurements and owner-entered rows.
No function produces narrative advice; the dashboard renders the facts.
"""

from __future__ import annotations

import math
import statistics
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .analytics import DailyPoint, PlanSpec, weekly_weight_points, monthly_weight_points
from .journal_models import DailyCheckin, DayNote
from .medication_models import Medication


MAINTENANCE_BAND_KG = 1.5
PLATEAU_WINDOW_DAYS = 14
PLATEAU_MIN_DROP_KG = 0.2
PLATEAU_MIN_MEASURED_DAYS = 6
PLATEAU_MAX_DATA_AGE_DAYS = 3
WEIGHT_MILESTONE_STEP_KG = 5.0
BMI_THRESHOLDS = (40.0, 35.0, 30.0, 25.0)
CHECKIN_CORRELATION_MINIMUM_DAYS = 14
MEDICATION_COMPARISON_DAYS = 14
CORRELATION_DISCLAIMER = "Корреляция не доказывает причинность."


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# --- Notes, check-ins and medications ---------------------------------------


def note_payload(row: DayNote) -> dict[str, Any]:
    return {
        "id": row.id,
        "day": row.day.isoformat(),
        "category": row.category,
        "text": row.text,
        "updated_at": _aware(row.updated_at).isoformat().replace("+00:00", "Z"),
    }


def checkin_payload(row: DailyCheckin) -> dict[str, Any]:
    return {
        "day": row.day.isoformat(),
        "energy": row.energy,
        "hunger": row.hunger,
        "mood": row.mood,
    }


def list_notes(db: Session, start: date | None = None, end: date | None = None) -> list[DayNote]:
    query = select(DayNote).order_by(DayNote.day.desc(), DayNote.created_at.desc(), DayNote.id)
    if start is not None:
        query = query.where(DayNote.day >= start)
    if end is not None:
        query = query.where(DayNote.day <= end)
    return list(db.scalars(query))


def list_checkins(db: Session, start: date | None = None) -> list[DailyCheckin]:
    query = select(DailyCheckin).order_by(DailyCheckin.day)
    if start is not None:
        query = query.where(DailyCheckin.day >= start)
    return list(db.scalars(query))


def is_current_medication(row: Medication, today: date) -> bool:
    if row.stopped_on is not None and row.stopped_on < today:
        return False
    return row.started_on is None or row.started_on <= today


def _mean(values: Sequence[float]) -> float | None:
    return round(statistics.fmean(values), 1) if values else None


def medication_pressure_comparison(
    sessions: Iterable[dict[str, Any]],
    started_on: date | None,
    tz: ZoneInfo,
    today: date,
    days: int = MEDICATION_COMPARISON_DAYS,
) -> dict[str, Any] | None:
    """Compare mean home pressure in equal windows before and after a start date.

    The start day belongs to neither window. The result is descriptive only;
    it never estimates a treatment effect.
    """
    if started_on is None:
        return None
    before: dict[str, list[float]] = {"systolic": [], "diastolic": []}
    after: dict[str, list[float]] = {"systolic": [], "diastolic": []}
    before_start = started_on - timedelta(days=days)
    after_end = min(today, started_on + timedelta(days=days))
    for session in sessions:
        measured = datetime.fromisoformat(str(session["measured_at"]).replace("Z", "+00:00"))
        day = _aware(measured).astimezone(tz).date()
        if before_start <= day < started_on:
            target = before
        elif started_on < day <= after_end:
            target = after
        else:
            continue
        for key in ("systolic", "diastolic"):
            if session.get(key) is not None:
                target[key].append(float(session[key]))
    return {
        "window_days": days,
        "before": {
            "from": before_start.isoformat(),
            "to": (started_on - timedelta(days=1)).isoformat(),
            "sessions": len(before["systolic"]),
            "systolic_mean": _mean(before["systolic"]),
            "diastolic_mean": _mean(before["diastolic"]),
        },
        "after": {
            "from": (started_on + timedelta(days=1)).isoformat(),
            "to": after_end.isoformat(),
            "sessions": len(after["systolic"]),
            "systolic_mean": _mean(after["systolic"]),
            "diastolic_mean": _mean(after["diastolic"]),
            "complete": after_end >= started_on + timedelta(days=days),
        },
    }


def _pearson(pairs: Sequence[tuple[float, float]]) -> float | None:
    if len(pairs) < CHECKIN_CORRELATION_MINIMUM_DAYS:
        return None
    xs, ys = zip(*pairs, strict=True)
    if math.isclose(max(xs), min(xs)) or math.isclose(max(ys), min(ys)):
        return None
    return round(statistics.correlation(xs, ys), 3)


def checkin_correlations(
    checkins: Sequence[DailyCheckin],
    recovery_daily: Sequence[dict[str, Any]],
    activity_daily: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Same-day Pearson correlations between self-assessments and published data."""
    sleep = {
        str(row["date"]): float(row["sleep_minutes"])
        for row in recovery_daily
        if row.get("sleep_minutes") is not None
    }
    steps = {
        str(row["date"]): float(row["steps"])
        for row in activity_daily
        if row.get("steps") is not None
    }
    requested = (
        ("energy", "sleep_minutes", sleep),
        ("mood", "sleep_minutes", sleep),
        ("energy", "steps", steps),
        ("hunger", "sleep_minutes", sleep),
    )
    results: list[dict[str, Any]] = []
    for field, target_name, target in requested:
        pairs = [
            (float(getattr(row, field)), target[row.day.isoformat()])
            for row in checkins
            if getattr(row, field) is not None and row.day.isoformat() in target
        ]
        coefficient = _pearson(pairs)
        if coefficient is None:
            continue
        results.append(
            {
                "metric": field,
                "target": target_name,
                "coefficient": coefficient,
                "paired_days": len(pairs),
                "disclaimer": CORRELATION_DISCLAIMER,
            }
        )
    return results


# --- Maintenance phase -------------------------------------------------------


def maintenance_state(
    clean_program_daily: Sequence[DailyPoint],
    plan: PlanSpec,
    latest_weight: float | None,
) -> dict[str, Any]:
    """Switch to a ±1.5 kg maintenance corridor once the target was reached.

    The phase is sticky: a later regain stays in maintenance and is shown as
    above the corridor instead of re-entering the loss plan.
    """
    reached = next(
        (point for point in clean_program_daily if point.value <= plan.target_weight_kg),
        None,
    )
    low = round(plan.target_weight_kg - MAINTENANCE_BAND_KG, 3)
    high = round(plan.target_weight_kg + MAINTENANCE_BAND_KG, 3)
    status = None
    if reached is not None and latest_weight is not None:
        status = "below" if latest_weight < low else "above" if latest_weight > high else "inside"
    return {
        "phase": "maintenance" if reached is not None else "loss",
        "target_reached_on": reached.day.isoformat() if reached else None,
        "band_low_kg": low,
        "band_high_kg": high,
        "band_status": status,
    }


# --- Plateau -----------------------------------------------------------------


def plateau_status(
    clean_program_daily: Sequence[DailyPoint],
    today: date,
) -> dict[str, Any]:
    """Detect a 14-day span in which the 7-day median trend did not fall.

    Returns ``active=False`` with a reason whenever data is too old or sparse,
    so a missing weighing is never mistaken for a plateau.
    """
    base: dict[str, Any] = {
        "active": False,
        "window_days": PLATEAU_WINDOW_DAYS,
        "threshold_kg": PLATEAU_MIN_DROP_KG,
        "change_kg": None,
        "from": None,
        "to": None,
        "reason": None,
    }
    trend = [point for point in clean_program_daily if point.rolling_7d is not None]
    if not trend:
        return {**base, "reason": "insufficient_data"}
    latest = trend[-1]
    if (today - latest.day).days > PLATEAU_MAX_DATA_AGE_DAYS:
        return {**base, "reason": "stale"}
    window_start = latest.day - timedelta(days=PLATEAU_WINDOW_DAYS)
    earlier = [point for point in trend if window_start - timedelta(days=3) <= point.day <= window_start]
    measured = [point for point in clean_program_daily if window_start < point.day <= latest.day]
    if not earlier or len(measured) < PLATEAU_MIN_MEASURED_DAYS:
        return {**base, "reason": "insufficient_data"}
    anchor = earlier[-1]
    assert anchor.rolling_7d is not None and latest.rolling_7d is not None
    change = round(latest.rolling_7d - anchor.rolling_7d, 3)
    return {
        **base,
        "active": change > -PLATEAU_MIN_DROP_KG,
        "change_kg": change,
        "from": anchor.day.isoformat(),
        "to": latest.day.isoformat(),
    }


# --- Milestones --------------------------------------------------------------


def _streaks(days: Sequence[date], today: date) -> tuple[int, int, date | None]:
    unique = sorted(set(days))
    longest = 0
    longest_end: date | None = None
    run = 0
    previous: date | None = None
    for day in unique:
        run = run + 1 if previous is not None and (day - previous).days == 1 else 1
        if run >= longest:
            longest = run
            longest_end = day
        previous = day
    current = 0
    if unique and (today - unique[-1]).days <= 1:
        current = 1
        for index in range(len(unique) - 1, 0, -1):
            if (unique[index] - unique[index - 1]).days != 1:
                break
            current += 1
    return current, longest, longest_end


def _best_period(points: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    complete = [
        point
        for point in points
        if not point.get("is_partial") and point.get("actual_change_kg") is not None
    ]
    if not complete:
        return None
    best = min(complete, key=lambda point: (point["actual_change_kg"], point["start_date"]))
    if best["actual_change_kg"] >= 0:
        return None
    return {
        "start_date": best["start_date"],
        "end_date": best["period_end_date"],
        "change_kg": best["actual_change_kg"],
        "actual_start_date": best.get("actual_start_date"),
        "actual_end_date": best.get("actual_end_date"),
    }


def milestones(
    program_daily: Sequence[DailyPoint],
    plan: PlanSpec,
    height_cm: float,
    today: date,
) -> dict[str, Any]:
    clean = [point for point in program_daily if not point.is_outlier]
    height_m = height_cm / 100
    items: list[dict[str, Any]] = []

    def first_at_or_below(threshold: float) -> DailyPoint | None:
        return next((point for point in clean if point.value <= threshold), None)

    step = 1
    while plan.start_weight_kg - step * WEIGHT_MILESTONE_STEP_KG > plan.target_weight_kg:
        lost = step * WEIGHT_MILESTONE_STEP_KG
        threshold = round(plan.start_weight_kg - lost, 3)
        hit = first_at_or_below(threshold)
        items.append(
            {
                "id": f"weight-{int(lost)}",
                "kind": "weight_loss",
                "label": f"−{int(lost)} кг от старта",
                "threshold_kg": threshold,
                "achieved_on": hit.day.isoformat() if hit else None,
                "achieved_kg": hit.value if hit else None,
            }
        )
        step += 1
    target_hit = first_at_or_below(plan.target_weight_kg)
    items.append(
        {
            "id": "target",
            "kind": "target",
            "label": "Цель достигнута",
            "threshold_kg": plan.target_weight_kg,
            "achieved_on": target_hit.day.isoformat() if target_hit else None,
            "achieved_kg": target_hit.value if target_hit else None,
        }
    )
    start_bmi = plan.start_weight_kg / height_m**2
    for bmi in BMI_THRESHOLDS:
        if start_bmi <= bmi:
            continue
        threshold = round(bmi * height_m**2 - 0.001, 3)
        hit = next((point for point in clean if point.value / height_m**2 < bmi), None)
        items.append(
            {
                "id": f"bmi-{int(bmi)}",
                "kind": "bmi",
                "label": f"ИМТ ниже {int(bmi)}",
                "threshold_kg": threshold,
                "achieved_on": hit.day.isoformat() if hit else None,
                "achieved_kg": hit.value if hit else None,
            }
        )
    items.sort(key=lambda item: (-item["threshold_kg"], item["id"]))
    latest = clean[-1].value if clean else None
    next_item = next((item for item in items if item["achieved_on"] is None), None)
    current_streak, longest_streak, longest_end = _streaks([point.day for point in program_daily], today)
    weekly = weekly_weight_points(list(program_daily), plan, today)
    monthly = monthly_weight_points(list(program_daily), plan, today)
    return {
        "items": items,
        "next": (
            {
                **next_item,
                "remaining_kg": round(latest - next_item["threshold_kg"], 3) if latest is not None else None,
            }
            if next_item
            else None
        ),
        "streaks": {
            "current_days": current_streak,
            "longest_days": longest_streak,
            "longest_end": longest_end.isoformat() if longest_end else None,
        },
        "best_week": _best_period(weekly),
        "best_month": _best_period(monthly),
        "plateau": plateau_status(clean, today),
        "height_cm": height_cm,
    }
