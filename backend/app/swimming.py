"""Pool-only Xiaomi summaries. No provider payload or identifying metadata leaves here."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .health_analytics import HealthRange, _aware, _range_start, _records, _utc_start
from .health_models import HealthConnectDevice
from .mi_fitness_models import MiFitnessCoverage, MiFitnessRecord, MiFitnessSource


POOL_TYPES = frozenset({"pool_swimming", "indoor_swimming", "swimming_pool"})
SWIMMING_NUMBERS = {
    "distance_meters": (0, 1_000_000),
    "active_duration_seconds": (0, 604_800),
    "kilocalories": (0, 100_000),
    "average_bpm": (20, 300),
    "minimum_bpm": (20, 300),
    "maximum_bpm": (20, 300),
    "pool_length_meters": (1, 200),
    "pool_lengths": (0, 100_000),
}
SWIMMING_STYLES = frozenset({"freestyle", "breaststroke", "backstroke", "butterfly", "medley"})


def normalise_swimming(value: object, duration: float) -> dict:
    # Imported lazily to keep the common Health Connect normaliser independent.
    from .health_ingest import HealthIngestError

    if not isinstance(value, dict) or not set(value) <= {*SWIMMING_NUMBERS, "stroke_style"}:
        raise HealthIngestError(422, "invalid_swimming_values")
    result: dict = {}
    for key, item in value.items():
        if key == "stroke_style":
            if not isinstance(item, str) or item not in SWIMMING_STYLES:
                raise HealthIngestError(422, "invalid_swimming_style")
            result[key] = item
            continue
        low, high = SWIMMING_NUMBERS[key]
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item) or not low <= item <= high:
            raise HealthIngestError(422, "invalid_swimming_value")
        if key == "pool_lengths" and item != int(item):
            raise HealthIngestError(422, "invalid_swimming_value")
        result[key] = int(item) if key == "pool_lengths" else round(item, 3)
    if result.get("active_duration_seconds", 0) > duration + 2:
        raise HealthIngestError(422, "invalid_swimming_duration")
    heart = [result[key] for key in ("minimum_bpm", "average_bpm", "maximum_bpm") if key in result]
    if heart != sorted(heart):
        raise HealthIngestError(422, "invalid_swimming_heart_rate")
    return result


def _iso(value: datetime) -> str:
    return _aware(value).isoformat().replace("+00:00", "Z")


def _session(row: MiFitnessRecord) -> dict:
    stored = row.metrics.get("swimming", {})
    # Explicit projection protects this API even if storage gains new fields later.
    details = {key: stored.get(key) for key in (*SWIMMING_NUMBERS, "stroke_style")}
    duration = row.metrics.get("duration_seconds", float(row.primary_value) if row.primary_value is not None else None)
    distance = details["distance_meters"]
    active = details["active_duration_seconds"]
    pace = round(active * 100 / distance, 3) if active is not None and active > 0 and distance is not None and distance > 0 else None
    return {
        "id": str(row.id),
        "start_time": _iso(row.start_time),
        "end_time": _iso(row.end_time),
        "duration_seconds": duration,
        "pace_seconds_per_100m": pace,
        **details,
    }


def swimming_series(
    db: Session,
    tz: ZoneInfo,
    range_name: HealthRange = "90d",
    now: datetime | None = None,
    *,
    offset: int = 0,
) -> dict:
    current = _aware(now or datetime.now(timezone.utc))
    today = current.astimezone(tz).date()
    start_day = _range_start(range_name, today)
    boundary = _utc_start(start_day, tz) if start_day else None
    rows = [
        row for row in _records(db, frozenset({"exercise"}), tz, start_day, cloud_only=True)
        if isinstance(row, MiFitnessRecord) and row.subtype in POOL_TYPES
        and _aware(row.end_time) <= current
        and (boundary is None or _aware(row.start_time) >= boundary)
    ]
    rows.sort(key=lambda row: (_aware(row.start_time), row.id), reverse=True)
    sessions = [_session(row) for row in rows]
    summary: dict = {"workouts": len(sessions)}
    for key in ("duration_seconds", "distance_meters", "kilocalories"):
        values = [item[key] for item in sessions if item[key] is not None]
        summary[key] = round(sum(values), 3) if values else None
        summary[f"{key}_count"] = len(values)

    coverages = list(db.scalars(
        select(MiFitnessCoverage)
        .join(MiFitnessSource, MiFitnessSource.device_id == MiFitnessCoverage.device_id)
        .join(HealthConnectDevice, HealthConnectDevice.id == MiFitnessSource.device_id)
        .where(
            HealthConnectDevice.status == "approved",
            MiFitnessSource.enabled.is_(True),
            MiFitnessSource.activated_at.is_not(None),
            MiFitnessCoverage.account_fingerprint == MiFitnessSource.account_fingerprint,
            MiFitnessCoverage.record_type == "exercise",
            MiFitnessCoverage.range_start < current,
            MiFitnessCoverage.range_end > (boundary or datetime(2000, 1, 1, tzinfo=timezone.utc)),
        )
    ))
    coverage_start = boundary or datetime(2000, 1, 1, tzinfo=timezone.utc)
    # Completeness is measured through the last finalized exercise window.
    # The time since that synchronization is freshness, not a historical gap.
    coverage_end = min(current, max((_aware(item.range_end) for item in coverages), default=current))
    cursor = coverage_start
    covered = 0.0
    for item in sorted(coverages, key=lambda item: _aware(item.range_start)):
        left = max(cursor, coverage_start, _aware(item.range_start))
        right = min(coverage_end, _aware(item.range_end))
        if right > left:
            covered += (right - left).total_seconds()
            cursor = right
    total = max(0.0, (coverage_end - coverage_start).total_seconds())
    coverage_status = "missing" if not coverages else "partial" if covered < total else "available" if sessions else "confirmed_empty"
    as_of = max((_aware(item.finalised_at) for item in coverages), default=None)
    return {
        "range": range_name,
        "summary": summary,
        "points": [
            {key: item[key] for key in ("start_time", "duration_seconds", "distance_meters")}
            for item in reversed(sessions)
        ],
        "sessions": sessions[offset:offset + 50],
        "next_offset": offset + 50 if offset + 50 < len(sessions) else None,
        "coverage": {
            "status": coverage_status,
            "from": _iso(coverage_start) if coverages or boundary else None,
            "to": _iso(coverage_end),
            "data_as_of": _iso(as_of) if as_of else None,
            "covered_days": round(covered / timedelta(days=1).total_seconds(), 2),
            "total_days": round(total / timedelta(days=1).total_seconds(), 2),
        },
    }
