from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import math
import statistics
from typing import Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo


PROGRAM_START = date(2026, 8, 15)
PROGRAM_START_WEIGHT_KG = 127.03
PROGRAM_MONTHLY_CHANGE_KG = -4.0
PROGRAM_TARGET_WEIGHT_KG = 76.5
RULESET_VERSION = "2026-08-19.1"


@dataclass(frozen=True)
class ValuePoint:
    measured_at: datetime
    value: float
    group_id: int | None = None


@dataclass(frozen=True)
class DailyPoint:
    day: date
    value: float
    sample_count: int
    is_outlier: bool = False
    rolling_7d: float | None = None


@dataclass(frozen=True)
class PressureReading:
    measured_at: datetime
    systolic: float | None = None
    diastolic: float | None = None
    pulse: float | None = None


@dataclass(frozen=True)
class PressureSession:
    measured_at: datetime
    systolic: float | None
    diastolic: float | None
    pulse: float | None
    sample_count: int

    @property
    def pulse_pressure(self) -> float | None:
        if self.systolic is None or self.diastolic is None:
            return None
        return self.systolic - self.diastolic


@dataclass(frozen=True)
class Forecast:
    reliable: bool
    reason: str | None = None
    slope_kg_per_day: float | None = None
    weekly_change_kg: float | None = None
    target_date: date | None = None
    confidence_start: date | None = None
    confidence_end: date | None = None
    samples: int = 0
    span_days: int = 0
    origin_date: date | None = None
    intercept_kg: float | None = None


@dataclass(frozen=True)
class PlanSpec:
    start_date: date = PROGRAM_START
    start_weight_kg: float = PROGRAM_START_WEIGHT_KG
    monthly_change_kg: float = PROGRAM_MONTHLY_CHANGE_KG
    target_weight_kg: float = PROGRAM_TARGET_WEIGHT_KG
    version: str = "program-2026-08-15-v1"


def _number(value: float | Decimal) -> float:
    return float(value)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def add_months(day: date, count: int) -> date:
    month_index = day.year * 12 + day.month - 1 + count
    year, month0 = divmod(month_index, 12)
    month = month0 + 1
    # The program starts on the 15th. Keeping this generic avoids surprising
    # rollover if a future plan starts at the end of a month.
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    last_day = (next_month - timedelta(days=1)).day
    return date(year, month, min(day.day, last_day))


def plan_weight(day: date, plan: PlanSpec = PlanSpec()) -> float | None:
    if day < plan.start_date:
        return None
    months = (day.year - plan.start_date.year) * 12 + day.month - plan.start_date.month
    anchor = add_months(plan.start_date, months)
    if day < anchor:
        months -= 1
        anchor = add_months(plan.start_date, months)
    next_anchor = add_months(plan.start_date, months + 1)
    interval = max(1, (next_anchor - anchor).days)
    fraction = (day - anchor).days / interval
    planned = plan.start_weight_kg + plan.monthly_change_kg * (months + fraction)
    return round(max(plan.target_weight_kg, planned), 3)


def planned_target_date(plan: PlanSpec = PlanSpec()) -> date:
    cursor = plan.start_date
    while True:
        value = plan_weight(cursor, plan)
        if value is not None and value <= plan.target_weight_kg + 1e-9:
            return cursor
        cursor += timedelta(days=1)


def weekly_weight_points(
    daily: Sequence[DailyPoint],
    plan: PlanSpec = PlanSpec(),
    as_of: date | None = None,
) -> list[dict[str, object]]:
    """Aggregate program daily medians into continuous ISO-week buckets.

    The first program week and the current week are clipped to the program and
    reporting boundaries. Empty weeks remain in the result so an actual change
    is never calculated across a gap in measurements.
    """
    today = as_of or date.today()
    if today < plan.start_date:
        return []

    program = [point for point in daily if plan.start_date <= point.day <= today]
    by_week: dict[date, list[DailyPoint]] = defaultdict(list)
    for point in program:
        monday = point.day - timedelta(days=point.day.weekday())
        by_week[monday].append(point)

    first_monday = plan.start_date - timedelta(days=plan.start_date.weekday())
    last_monday = today - timedelta(days=today.weekday())
    result: list[dict[str, object]] = []
    previous_actual_average: float | None = None
    previous_planned_average: float | None = None
    monday = first_monday
    while monday <= last_monday:
        sunday = monday + timedelta(days=6)
        period_start = max(monday, plan.start_date)
        period_end = min(sunday, today)
        measured = sorted(by_week.get(monday, []), key=lambda point: point.day)
        clean = [point for point in measured if not point.is_outlier]
        planned: list[float] = []
        cursor = period_start
        while cursor <= period_end:
            value = plan_weight(cursor, plan)
            if value is not None:
                planned.append(value)
            cursor += timedelta(days=1)

        actual_average = statistics.fmean(point.value for point in clean) if clean else None
        actual_minimum = min((point.value for point in clean), default=None)
        planned_average = statistics.fmean(planned) if planned else None

        result.append(
            {
                "start_date": period_start.isoformat(),
                "end_date": period_end.isoformat(),
                "actual_avg_kg": round(actual_average, 3) if actual_average is not None else None,
                "actual_min_kg": round(actual_minimum, 3) if actual_minimum is not None else None,
                "planned_avg_kg": round(planned_average, 3) if planned_average is not None else None,
                "actual_change_kg": (
                    round(actual_average - previous_actual_average, 3)
                    if actual_average is not None and previous_actual_average is not None
                    else None
                ),
                "planned_change_kg": (
                    round(planned_average - previous_planned_average, 3)
                    if planned_average is not None and previous_planned_average is not None
                    else None
                ),
                "deviation_from_plan_kg": (
                    round(actual_average - planned_average, 3)
                    if actual_average is not None and planned_average is not None
                    else None
                ),
                "measurement_days": len(measured),
                "sample_count": sum(point.sample_count for point in measured),
                "outlier_days": sum(point.is_outlier for point in measured),
                "is_partial": period_start != monday or monday == last_monday,
            }
        )
        previous_actual_average = actual_average
        previous_planned_average = planned_average
        monday += timedelta(days=7)
    return result


def daily_medians(
    points: Iterable[ValuePoint],
    tz: ZoneInfo,
    since: date | None = None,
    until: date | None = None,
) -> list[DailyPoint]:
    grouped: dict[date, list[float]] = defaultdict(list)
    for point in points:
        local_day = _aware(point.measured_at).astimezone(tz).date()
        if since is not None and local_day < since:
            continue
        if until is not None and local_day > until:
            continue
        grouped[local_day].append(_number(point.value))
    base = [
        DailyPoint(day, statistics.median(values), len(values))
        for day, values in sorted(grouped.items())
    ]
    if not base:
        return []

    outlier_flags = hampel_flags([point.value for point in base])
    result: list[DailyPoint] = []
    clean: list[DailyPoint] = []
    for point, is_outlier in zip(base, outlier_flags, strict=True):
        window_start = point.day - timedelta(days=6)
        values = [
            previous.value
            for previous in clean
            if previous.day >= window_start and not previous.is_outlier
        ]
        if not is_outlier:
            values.append(point.value)
        rolling = statistics.fmean(values) if values else None
        completed = DailyPoint(
            point.day,
            round(point.value, 3),
            point.sample_count,
            is_outlier,
            round(rolling, 3) if rolling is not None else None,
        )
        result.append(completed)
        clean.append(completed)
    return result


def hampel_flags(
    values: Sequence[float], half_window: int = 3, threshold: float = 3.0, mad_floor: float = 0.1
) -> list[bool]:
    flags = [False] * len(values)
    for index, value in enumerate(values):
        lo, hi = max(0, index - half_window), min(len(values), index + half_window + 1)
        window = list(values[lo:hi])
        if len(window) < 4:
            continue
        median = statistics.median(window)
        deviations = [abs(item - median) for item in window]
        mad = statistics.median(deviations)
        # A perfectly flat neighbourhood has MAD=0. A small measurement-domain
        # floor avoids division/zero-threshold behaviour where any rounding
        # difference would otherwise be labelled an outlier.
        effective_mad = max(mad, mad_floor)
        # 1.4826 makes MAD comparable with standard deviation for normal data.
        flags[index] = abs(value - median) > threshold * 1.4826 * effective_mad
    return flags


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("cannot calculate a percentile of an empty sequence")
    position = (len(ordered) - 1) * fraction
    lo = math.floor(position)
    hi = math.ceil(position)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)


def theil_sen_forecast(
    daily: Sequence[DailyPoint],
    target_weight_kg: float = PROGRAM_TARGET_WEIGHT_KG,
    window_days: int = 42,
    minimum_samples: int = 14,
    minimum_span_days: int = 21,
    as_of: date | None = None,
    maximum_age_days: int = 14,
) -> Forecast:
    clean = [point for point in daily if not point.is_outlier]
    if not clean:
        return Forecast(False, "no_data")
    end = clean[-1].day
    if as_of is not None and (as_of - end).days > maximum_age_days:
        return Forecast(False, "latest_measurement_is_stale", samples=0, span_days=0)
    start = end - timedelta(days=window_days - 1)
    sample = [point for point in clean if point.day >= start]
    span = (sample[-1].day - sample[0].day).days if len(sample) > 1 else 0
    if len(sample) < minimum_samples:
        return Forecast(False, "not_enough_measurement_days", samples=len(sample), span_days=span)
    if span < minimum_span_days:
        return Forecast(False, "measurement_span_too_short", samples=len(sample), span_days=span)

    origin = sample[0].day
    xs = [(point.day - origin).days for point in sample]
    ys = [point.value for point in sample]
    slopes = [
        (ys[j] - ys[i]) / (xs[j] - xs[i])
        for i in range(len(sample))
        for j in range(i + 1, len(sample))
        if xs[j] != xs[i]
    ]
    slope = statistics.median(slopes)
    if slope >= -0.005:
        return Forecast(
            False,
            "trend_is_not_decreasing",
            round(slope, 5),
            round(slope * 7, 3),
            samples=len(sample),
            span_days=span,
        )
    slope_low = _percentile(slopes, 0.25)
    slope_high = _percentile(slopes, 0.75)
    if slope_high >= 0:
        return Forecast(
            False,
            "slope_interval_crosses_zero",
            round(slope, 5),
            round(slope * 7, 3),
            samples=len(sample),
            span_days=span,
        )
    intercept = statistics.median([y - slope * x for x, y in zip(xs, ys, strict=True)])
    target_x = (target_weight_kg - intercept) / slope
    target = origin + timedelta(days=math.ceil(target_x))
    if target <= end:
        target = end
    if target > end + timedelta(days=365 * 5):
        return Forecast(
            False,
            "target_is_too_far",
            round(slope, 5),
            round(slope * 7, 3),
            samples=len(sample),
            span_days=span,
        )

    confidence_start = confidence_end = None
    if len(slopes) >= 10:
        slow = slope_high
        fast = slope_low
        fast_date = origin + timedelta(days=max(0, math.ceil((target_weight_kg - intercept) / fast)))
        slow_date = origin + timedelta(days=max(0, math.ceil((target_weight_kg - intercept) / slow)))
        confidence_start, confidence_end = min(fast_date, slow_date), max(fast_date, slow_date)
    return Forecast(
        True,
        slope_kg_per_day=round(slope, 5),
        weekly_change_kg=round(slope * 7, 3),
        target_date=target,
        confidence_start=confidence_start,
        confidence_end=confidence_end,
        samples=len(sample),
        span_days=span,
        origin_date=origin,
        intercept_kg=round(intercept, 6),
    )


def trend_change(daily: Sequence[DailyPoint], days: int) -> float | None:
    """Return the observed endpoint change inside the requested calendar window.

    The window may contain fewer than ``days`` of measurements (we still need
    enough span to make the value useful), but the result must remain the
    actual difference between the first and last available daily medians.  It
    is deliberately not extrapolated to a full window: doing so turns a short
    observed change into a number that never occurred in the user's data.
    """
    clean = [point for point in daily if not point.is_outlier]
    if len(clean) < 2:
        return None
    cutoff = clean[-1].day - timedelta(days=days - 1)
    sample = [point for point in clean if point.day >= cutoff]
    if len(sample) < 2 or (sample[-1].day - sample[0].day).days < max(3, days // 3):
        return None
    first, last = sample[0], sample[-1]
    return round(last.value - first.value, 3)


def pressure_sessions(
    readings: Iterable[PressureReading], maximum_gap: timedelta = timedelta(minutes=5)
) -> list[PressureSession]:
    ordered = sorted(readings, key=lambda item: _aware(item.measured_at))
    buckets: list[list[PressureReading]] = []
    for reading in ordered:
        if not buckets or _aware(reading.measured_at) - _aware(buckets[-1][0].measured_at) > maximum_gap:
            buckets.append([reading])
        else:
            buckets[-1].append(reading)
    result: list[PressureSession] = []
    for bucket in buckets:
        def aggregate(name: str) -> float | None:
            values = [getattr(item, name) for item in bucket if getattr(item, name) is not None]
            return round(statistics.median(values), 1) if values else None

        result.append(
            PressureSession(
                measured_at=bucket[0].measured_at,
                systolic=aggregate("systolic"),
                diastolic=aggregate("diastolic"),
                pulse=aggregate("pulse"),
                sample_count=len(bucket),
            )
        )
    return result


def _summary(values: Sequence[float]) -> dict[str, float | int] | None:
    if not values:
        return None
    return {
        "count": len(values),
        "mean": round(statistics.fmean(values), 1),
        "median": round(statistics.median(values), 1),
        "min": round(min(values), 1),
        "max": round(max(values), 1),
        "variability": round(statistics.stdev(values), 1) if len(values) > 1 else 0.0,
    }


def pressure_statistics(
    sessions: Sequence[PressureSession], tz: ZoneInfo, as_of: datetime | None = None
) -> dict[str, object]:
    current = _aware(as_of or datetime.now(timezone.utc))

    def window(days: int) -> dict[str, object]:
        cutoff = current - timedelta(days=days)
        sample = [item for item in sessions if _aware(item.measured_at) >= cutoff]
        return {
            "sessions": len(sample),
            "systolic": _summary([item.systolic for item in sample if item.systolic is not None]),
            "diastolic": _summary([item.diastolic for item in sample if item.diastolic is not None]),
            "pulse": _summary([item.pulse for item in sample if item.pulse is not None]),
            "pulse_pressure": _summary(
                [item.pulse_pressure for item in sample if item.pulse_pressure is not None]
            ),
        }

    periods: dict[str, dict[str, object]] = {"morning": {}, "evening": {}}
    for label, predicate in (
        ("morning", lambda hour: 5 <= hour < 12),
        ("evening", lambda hour: 18 <= hour < 24),
    ):
        sample = [item for item in sessions if predicate(_aware(item.measured_at).astimezone(tz).hour)]
        periods[label] = {
            "sessions": len(sample),
            "systolic": _summary([item.systolic for item in sample if item.systolic is not None]),
            "diastolic": _summary([item.diastolic for item in sample if item.diastolic is not None]),
            "pulse": _summary([item.pulse for item in sample if item.pulse is not None]),
        }
    return {"last_7_days": window(7), "last_30_days": window(30), "time_of_day": periods}


def weekly_weight_pressure_correlation(
    weights: Sequence[DailyPoint], sessions: Sequence[PressureSession], tz: ZoneInfo
) -> dict[str, object] | None:
    weight_weeks: dict[tuple[int, int], list[float]] = defaultdict(list)
    pressure_weeks: dict[tuple[int, int], list[float]] = defaultdict(list)
    for point in weights:
        if point.is_outlier:
            continue
        iso = point.day.isocalendar()
        weight_weeks[(iso.year, iso.week)].append(point.value)
    for session in sessions:
        if session.systolic is None or session.diastolic is None:
            continue
        local = _aware(session.measured_at).astimezone(tz).date().isocalendar()
        pressure_weeks[(local.year, local.week)].append((session.systolic + session.diastolic) / 2)
    overlapping = sorted(weight_weeks.keys() & pressure_weeks.keys())
    if len(overlapping) < 8:
        return None
    xs = [statistics.fmean(weight_weeks[key]) for key in overlapping]
    ys = [statistics.fmean(pressure_weeks[key]) for key in overlapping]
    if statistics.pstdev(xs) == 0 or statistics.pstdev(ys) == 0:
        coefficient = 0.0
    else:
        coefficient = statistics.correlation(xs, ys)
    return {
        "weeks": len(overlapping),
        "coefficient": round(coefficient, 3),
        "note": "Корреляция не доказывает причинно-следственную связь.",
    }


def build_insights(
    daily: Sequence[DailyPoint], plan: PlanSpec = PlanSpec(), today: date | None = None
) -> list[dict[str, object]]:
    if not daily:
        return []
    current_day = today or daily[-1].day
    program = [point for point in daily if point.day >= plan.start_date and not point.is_outlier]
    if not program:
        return []
    latest = program[-1]
    trend_value = latest.rolling_7d if latest.rolling_7d is not None else latest.value
    desired = plan_weight(latest.day, plan)
    recent_14 = [point for point in program if point.day >= current_day - timedelta(days=13)]
    insights: list[dict[str, object]] = []

    def add(rule: str, title: str, message: str, priority: int) -> None:
        insights.append(
            {
                "id": f"{RULESET_VERSION}:{rule}",
                "rule": rule,
                "ruleset_version": RULESET_VERSION,
                "title": title,
                "message": message,
                "priority": priority,
            }
        )

    if len(recent_14) < 5:
        add(
            "measure_regularly",
            "Регулярность измерений",
            "За последние 14 дней мало замеров. Более регулярные измерения сделают тренд надёжнее.",
            90,
        )
    if (current_day - latest.day).days > 14:
        add(
            "stale_weight_data",
            "Нет свежих измерений",
            "Последний вес измерен больше двух недель назад. Текущие выводы о темпе и плане приостановлены до нового замера.",
            100,
        )
        return sorted(insights, key=lambda item: (-int(item["priority"]), str(item["id"])))
    change_28 = trend_change(program, 28)
    if change_28 is not None:
        weekly = change_28 / 4
        if weekly > -0.1:
            add(
                "plateau_or_growth",
                "Тренд почти не снижается",
                "Средний тренд за последние недели близок к плато или растёт. Проверьте привычки, которые можно устойчиво вернуть в режим.",
                100,
            )
        elif weekly < -1.5:
            add(
                "fast_change",
                "Быстрый темп изменения",
                "Вес снижается заметно быстрее недавнего плана. Оценивайте устойчивость темпа и самочувствие, не делая вывод по одному замеру.",
                95,
            )
    if desired is not None:
        delta = trend_value - desired
        if delta <= 0:
            add(
                "on_plan",
                "План соблюдается",
                f"Сглаженный прогресс примерно на {abs(delta):.1f} кг впереди плановой линии.",
                40,
            )
        elif delta >= 2:
            add(
                "behind_plan",
                "Отклонение от плана",
                f"Текущий вес примерно на {delta:.1f} кг выше плановой линии. Смотрите на многонедельный тренд, а не на суточные колебания.",
                80,
            )
    if len(recent_14) >= 5 and statistics.pstdev([item.value for item in recent_14]) >= 1.5:
        add(
            "high_variability",
            "Высокий разброс замеров",
            "Последние значения заметно колеблются. Сравнивайте измерения в похожих условиях и ориентируйтесь на сглаженную линию.",
            70,
        )
    lost = plan.start_weight_kg - trend_value
    if lost >= 5:
        milestone = int(lost // 5) * 5
        add(
            f"milestone_{milestone}",
            "Новый рубеж",
            f"От старта программы ушло около {milestone} кг.",
            30,
        )
    return sorted(insights, key=lambda item: (-int(item["priority"]), str(item["id"])))


def local_range_start(range_name: str, tz: ZoneInfo, now: datetime | None = None) -> date | None:
    today = _aware(now or datetime.now(timezone.utc)).astimezone(tz).date()
    return {
        "program": PROGRAM_START,
        "30d": today - timedelta(days=29),
        "90d": today - timedelta(days=89),
        "1y": today - timedelta(days=364),
        "all": None,
    }[range_name]
