from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .health_analytics import ACTIVITY_TYPES, RECOVERY_TYPES, _records
from .health_models import HealthConnectBatch, HealthConnectDevice
from .mi_fitness_models import MiFitnessCoverage, MiFitnessRecord, MiFitnessSource
from .models import Measurement, MeasurementGroup, SyncState


DataQualityRange = Literal["30d", "90d"]


@dataclass(frozen=True)
class _WithingsMetric:
    key: str
    family: str
    kinds: frozenset[str]
    require_all: bool = False


_WITHINGS_METRICS = (
    _WithingsMetric("weight", "weight", frozenset({"weight"})),
    _WithingsMetric(
        "blood_pressure",
        "pressure",
        frozenset({"systolic", "diastolic"}),
        require_all=True,
    ),
    _WithingsMetric(
        "body_composition",
        "composition",
        frozenset(
            {
                "fat_percent",
                "fat_mass",
                "fat_free_mass",
                "muscle_mass",
                "hydration",
                "bone_mass",
            }
        ),
    ),
)

_HEALTH_FAMILIES = {
    "steps": "activity",
    "distance": "activity",
    "active_calories": "activity",
    "total_calories": "activity",
    "exercise": "activity",
    "sleep": "recovery",
    "heart_rate": "heart",
    "resting_heart_rate": "heart",
    "hrv_rmssd": "heart",
    "oxygen_saturation": "oxygen",
    "vo2_max": "vo2",
}
_HEALTH_TYPES = frozenset(ACTIVITY_TYPES | RECOVERY_TYPES)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _iso(value: datetime | None) -> str | None:
    return _aware(value).isoformat().replace("+00:00", "Z") if value else None


def _max_time(values: list[datetime | None]) -> datetime | None:
    return max((_aware(value) for value in values if value is not None), default=None)


def _source_status(
    *,
    configured: bool,
    pending: bool,
    last_success_at: datetime | None,
    has_error: bool,
    current: datetime,
    delayed_after: timedelta,
) -> str:
    if not configured:
        return "not_configured"
    if has_error:
        return "error"
    if pending or last_success_at is None:
        return "pending"
    if current - _aware(last_success_at) > delayed_after:
        return "delayed"
    return "healthy"


def _source_payloads(db: Session, current: datetime) -> dict[str, dict[str, Any]]:
    withings = db.get(SyncState, "withings")
    withings_success = withings.last_success_at if withings else None

    devices = list(
        db.scalars(
            select(HealthConnectDevice).where(HealthConnectDevice.status == "approved")
        )
    )
    health_success = _max_time([device.last_sync_at for device in devices])
    health_data_as_of = _max_time([device.data_as_of for device in devices])

    cloud_sources = list(db.scalars(select(MiFitnessSource)))
    enabled_cloud = [source for source in cloud_sources if source.enabled]
    active_cloud = [
        source
        for source in enabled_cloud
        if source.activated_at is not None and source.account_fingerprint is not None
    ]
    cloud_success = _max_time([source.last_success_at for source in active_cloud])
    cloud_data_as_of = _max_time([source.data_as_of for source in active_cloud])

    return {
        "withings": {
            "status": _source_status(
                configured=withings is not None,
                pending=bool(withings and not withings.initial_import_done),
                last_success_at=withings_success,
                has_error=bool(withings and withings.last_error),
                current=current,
                delayed_after=timedelta(minutes=15),
            ),
            "last_success_at": _iso(withings_success),
            "data_as_of": _iso(withings_success),
        },
        "health_connect": {
            "status": _source_status(
                configured=bool(devices),
                pending=bool(devices and health_success is None),
                last_success_at=health_success,
                has_error=any(bool(device.last_error) for device in devices),
                current=current,
                delayed_after=timedelta(hours=2),
            ),
            "last_success_at": _iso(health_success),
            "data_as_of": _iso(health_data_as_of),
        },
        "mi_fitness": {
            "status": _source_status(
                configured=bool(enabled_cloud),
                pending=bool(enabled_cloud and not active_cloud),
                last_success_at=cloud_success,
                has_error=any(bool(source.last_error_code) for source in enabled_cloud),
                current=current,
                delayed_after=timedelta(hours=2),
            ),
            "last_success_at": _iso(cloud_success),
            "data_as_of": _iso(cloud_data_as_of),
        },
    }


def _days(start: date, end: date) -> list[date]:
    result: list[date] = []
    cursor = start
    while cursor <= end:
        result.append(cursor)
        cursor += timedelta(days=1)
    return result


def _day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=tz).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz).astimezone(
        timezone.utc
    )
    return start, end


def _full_coverage(
    coverages: list[MiFitnessCoverage | HealthConnectBatch],
    day: date,
    tz: ZoneInfo,
) -> MiFitnessCoverage | HealthConnectBatch | None:
    start, end = _day_bounds(day, tz)
    candidates = [
        coverage
        for coverage in coverages
        if coverage.range_start is not None
        and coverage.range_end is not None
        and _aware(coverage.range_start) <= start
        and _aware(coverage.range_end) >= end
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (
            _aware(
                item.finalised_at
                if isinstance(item, MiFitnessCoverage)
                else item.accepted_at
            ),
            item.id,
        ),
    )


def _metric_payload(
    *,
    key: str,
    family: str,
    source_policy: str,
    day_rows: list[dict[str, str | None]],
) -> dict[str, Any]:
    available = [row for row in day_rows if row["state"] == "available"]
    confirmed_empty = [
        row for row in day_rows if row["state"] == "confirmed_empty"
    ]
    missing = [row for row in day_rows if row["state"] == "missing"]
    known = [row for row in day_rows if row["state"] != "missing"]
    latest = available[-1] if available else None
    if len(available) == len(day_rows):
        status = "available"
    elif len(confirmed_empty) == len(day_rows):
        status = "confirmed_empty"
    elif not known:
        status = "missing"
    else:
        status = "partial"
    return {
        "key": key,
        "family": family,
        "source_policy": source_policy,
        "status": status,
        "latest_date": latest["date"] if latest else None,
        "latest_source": latest["source"] if latest else None,
        "observation_days": len(available),
        "coverage": {
            "known": len(known),
            "with_values": len(available),
            "withings": sum(row["source"] == "withings" for row in known),
            "mi_fitness": sum(row["source"] == "mi_fitness" for row in known),
            "health_connect": sum(
                row["source"] == "health_connect" for row in known
            ),
            "confirmed_empty": len(confirmed_empty),
            "missing": len(missing),
        },
        "days": day_rows,
    }


def _withings_metrics(
    db: Session,
    tz: ZoneInfo,
    days: list[date],
) -> list[dict[str, Any]]:
    first_start, _ = _day_bounds(days[0], tz)
    _, last_end = _day_bounds(days[-1], tz)
    kinds_by_day: dict[date, set[str]] = {}
    rows = db.execute(
        select(MeasurementGroup.measured_at, Measurement.kind)
        .join(Measurement, Measurement.group_id == MeasurementGroup.id)
        .where(
            MeasurementGroup.measured_at >= first_start,
            MeasurementGroup.measured_at < last_end,
            Measurement.kind.in_(
                frozenset().union(*(metric.kinds for metric in _WITHINGS_METRICS))
            ),
        )
    )
    for measured_at, kind in rows:
        local_day = _aware(measured_at).astimezone(tz).date()
        kinds_by_day.setdefault(local_day, set()).add(kind)

    result: list[dict[str, Any]] = []
    for metric in _WITHINGS_METRICS:
        day_rows = []
        for day in days:
            present = kinds_by_day.get(day, set())
            available = (
                metric.kinds.issubset(present)
                if metric.require_all
                else bool(metric.kinds & present)
            )
            day_rows.append(
                {
                    "date": day.isoformat(),
                    "state": "available" if available else "missing",
                    "source": "withings" if available else None,
                }
            )
        result.append(
            _metric_payload(
                key=metric.key,
                family=metric.family,
                source_policy="withings_only",
                day_rows=day_rows,
            )
        )
    return result


def _record_days(
    row: Any,
    tz: ZoneInfo,
) -> list[date]:
    start = _aware(row.start_time)
    end = _aware(row.end_time or row.start_time)
    if row.record_type == "sleep":
        return [end.astimezone(tz).date()]
    if row.record_type != "exercise":
        return [start.astimezone(tz).date()]
    first = start.astimezone(tz).date()
    last = max(start, end - timedelta(microseconds=1)).astimezone(tz).date()
    return _days(first, last)


def _health_metrics(
    db: Session,
    tz: ZoneInfo,
    days: list[date],
) -> list[dict[str, Any]]:
    first_start, _ = _day_bounds(days[0], tz)
    _, last_end = _day_bounds(days[-1], tz)
    selected = _records(db, _HEALTH_TYPES, tz, days[0])
    value_sources: dict[tuple[str, date], set[str]] = {}
    for row in selected:
        source = "mi_fitness" if isinstance(row, MiFitnessRecord) else "health_connect"
        for local_day in _record_days(row, tz):
            if days[0] <= local_day <= days[-1]:
                value_sources.setdefault((row.record_type, local_day), set()).add(source)

    sources = list(
        db.scalars(
            select(MiFitnessSource).where(
                MiFitnessSource.enabled.is_(True),
                MiFitnessSource.activated_at.is_not(None),
                MiFitnessSource.account_fingerprint.is_not(None),
            )
        )
    )
    source_by_device = {source.device_id: source for source in sources}
    cloud_by_type: dict[str, list[MiFitnessCoverage]] = {
        record_type: [] for record_type in _HEALTH_TYPES
    }
    if source_by_device:
        coverages = db.scalars(
            select(MiFitnessCoverage).where(
                MiFitnessCoverage.device_id.in_(source_by_device),
                MiFitnessCoverage.record_type.in_(_HEALTH_TYPES),
                MiFitnessCoverage.range_end > first_start,
                MiFitnessCoverage.range_start < last_end,
            )
        )
        for coverage in coverages:
            source = source_by_device.get(coverage.device_id)
            if source and coverage.account_fingerprint == source.account_fingerprint:
                cloud_by_type[coverage.record_type].append(coverage)

    health_by_type: dict[str, list[HealthConnectBatch]] = {
        record_type: [] for record_type in _HEALTH_TYPES
    }
    completed_health = db.scalars(
        select(HealthConnectBatch)
        .join(
            HealthConnectDevice,
            HealthConnectBatch.device_id == HealthConnectDevice.id,
        )
        .where(
            HealthConnectDevice.status == "approved",
            HealthConnectBatch.final_page.is_(True),
            HealthConnectBatch.snapshot_id.is_not(None),
            HealthConnectBatch.range_start.is_not(None),
            HealthConnectBatch.range_end.is_not(None),
            HealthConnectBatch.record_type.in_(_HEALTH_TYPES - {"steps"}),
            HealthConnectBatch.range_end > first_start,
            HealthConnectBatch.range_start < last_end,
        )
    )
    for coverage in completed_health:
        health_by_type[coverage.record_type].append(coverage)

    result: list[dict[str, Any]] = []
    for record_type in sorted(_HEALTH_TYPES):
        day_rows: list[dict[str, str | None]] = []
        for day in days:
            available_sources = value_sources.get((record_type, day), set())
            if available_sources:
                source = (
                    "mi_fitness"
                    if "mi_fitness" in available_sources
                    else "health_connect"
                )
                state = "available"
            elif _full_coverage(cloud_by_type[record_type], day, tz) is not None:
                source = "mi_fitness"
                state = "confirmed_empty"
            elif (
                record_type != "steps"
                and _full_coverage(health_by_type[record_type], day, tz) is not None
            ):
                source = "health_connect"
                state = "confirmed_empty"
            else:
                source = None
                state = "missing"
            day_rows.append(
                {"date": day.isoformat(), "state": state, "source": source}
            )
        result.append(
            _metric_payload(
                key=record_type,
                family=_HEALTH_FAMILIES[record_type],
                source_policy=(
                    "xiaomi_finalized_only"
                    if record_type == "steps"
                    else "finalized_xiaomi_then_health_connect"
                ),
                day_rows=day_rows,
            )
        )
    return result


def data_quality(
    db: Session,
    tz: ZoneInfo,
    range_name: DataQualityRange = "30d",
    now: datetime | None = None,
) -> dict[str, Any]:
    current = _aware(now or datetime.now(timezone.utc))
    today = current.astimezone(tz).date()
    length = 30 if range_name == "30d" else 90
    completed_days = _days(today - timedelta(days=length), today - timedelta(days=1))
    metrics = [
        *_withings_metrics(db, tz, completed_days),
        *_health_metrics(db, tz, completed_days),
    ]
    return {
        "range": range_name,
        "from": completed_days[0].isoformat(),
        "to": completed_days[-1].isoformat(),
        "timezone": tz.key,
        "generated_at": _iso(current),
        "sources": _source_payloads(db, current),
        "metrics": metrics,
    }
