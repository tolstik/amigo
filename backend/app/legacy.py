from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from sqlalchemy import MetaData, Table, create_engine, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .models import Measurement, MeasurementGroup


TIME_COLUMNS = ("measured_at", "datetime", "created_at", "date", "timestamp", "time", "unixtime")
KIND_COLUMNS: dict[str, tuple[str, ...]] = {
    "weight": ("weight", "ves", "mass", "value"),
    "systolic": ("systolic", "sys", "pressure_high", "upper_pressure"),
    "diastolic": ("diastolic", "dia", "pressure_low", "lower_pressure"),
    "pulse": ("pulse", "heart_rate", "hr"),
    "fat_percent": ("fat_percent", "fat_ratio", "fat"),
    "fat_mass": ("fat_mass",),
    "fat_free_mass": ("fat_free_mass", "lean_mass"),
}
UNITS = {
    "weight": "kg",
    "systolic": "mmHg",
    "diastolic": "mmHg",
    "pulse": "bpm",
    "fat_percent": "%",
    "fat_mass": "kg",
    "fat_free_mass": "kg",
}


@dataclass(frozen=True)
class LegacyImportResult:
    tables_scanned: int
    rows_seen: int
    groups_created: int
    duplicates_skipped: int


def _first_present(columns: set[str], candidates: tuple[str, ...]) -> str | None:
    lowered = {column.lower(): column for column in columns}
    return next((lowered[name] for name in candidates if name in lowered), None)


def _datetime(value: Any, tz: ZoneInfo) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    elif isinstance(value, (int, float, Decimal)) or (isinstance(value, str) and value.isdigit()):
        parsed = datetime.fromtimestamp(int(value), timezone.utc)
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("unsupported legacy timestamp")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(timezone.utc)


def _already_present(db: Session, measured_at: datetime, values: Mapping[str, float]) -> bool:
    for kind, value in values.items():
        found = db.scalar(
            select(Measurement.id)
            .join(MeasurementGroup, Measurement.group_id == MeasurementGroup.id)
            .where(
                MeasurementGroup.measured_at == measured_at,
                Measurement.kind == kind,
                Measurement.value.between(Decimal(str(value - 0.0005)), Decimal(str(value + 0.0005))),
            )
            .limit(1)
        )
        if found is not None:
            return True
    return False


def _normalize_legacy_value(kind: str, value: Any) -> float:
    number = float(value)
    if kind == "weight" and number > 1000:
        return number / 1000
    return number


def import_legacy(
    db: Session,
    legacy_url: str,
    tz: ZoneInfo,
    *,
    only_table: str | None = None,
    time_column: str | None = None,
    kind_columns: Mapping[str, str] | None = None,
    engine: Engine | None = None,
) -> LegacyImportResult:
    source = engine or create_engine(legacy_url, pool_pre_ping=True)
    inspector = inspect(source)
    available_tables = inspector.get_table_names()
    if only_table:
        if only_table not in available_tables:
            raise ValueError(f"legacy table {only_table!r} does not exist")
        available_tables = [only_table]
    scanned = rows_seen = created = skipped = 0
    metadata = MetaData()
    try:
        for table_name in available_tables:
            table = Table(table_name, metadata, autoload_with=source)
            names = {column.name for column in table.columns}
            timestamp_name = time_column if time_column in names else _first_present(names, TIME_COLUMNS)
            mapped = dict(kind_columns or {})
            for kind, candidates in KIND_COLUMNS.items():
                if kind not in mapped:
                    candidate = _first_present(names, candidates)
                    if candidate:
                        mapped[kind] = candidate
            mapped = {kind: column for kind, column in mapped.items() if column in names}
            if not timestamp_name or not mapped:
                continue
            scanned += 1
            primary_keys = [column.name for column in table.primary_key.columns]
            with source.connect() as connection:
                for row in connection.execute(select(table)).mappings():
                    rows_seen += 1
                    try:
                        measured_at = _datetime(row[timestamp_name], tz)
                        values = {
                            kind: _normalize_legacy_value(kind, row[column])
                            for kind, column in mapped.items()
                            if row[column] is not None
                        }
                    except (TypeError, ValueError, OverflowError):
                        continue
                    if not values:
                        continue
                    if _already_present(db, measured_at, values):
                        skipped += 1
                        continue
                    identity = {key: row[key] for key in primary_keys}
                    if not identity:
                        identity = {"at": measured_at.isoformat(), "values": values}
                    digest = hashlib.sha256(
                        json.dumps(identity, sort_keys=True, default=str).encode()
                    ).hexdigest()[:32]
                    group = MeasurementGroup(
                        provider="legacy",
                        provider_group_id=f"{table_name}:{digest}",
                        measured_at=measured_at,
                        source_timezone=getattr(tz, "key", str(tz)),
                        source="legacy",
                        raw_payload={"table": table_name, "identity": identity},
                    )
                    db.add(group)
                    db.flush()
                    for kind, value in values.items():
                        db.add(
                            Measurement(
                                group_id=group.id,
                                kind=kind,
                                value=Decimal(str(value)),
                                unit=UNITS.get(kind, "raw"),
                            )
                        )
                    created += 1
            db.commit()
    finally:
        if engine is None:
            source.dispose()
    return LegacyImportResult(scanned, rows_seen, created, skipped)


def import_legacy_weight_file(
    db: Session,
    file_path: str | Path,
    tz: ZoneInfo,
    *,
    scale: float = 0.001,
) -> LegacyImportResult:
    """Import headerless `date_creat<TAB>weight` output from the legacy MariaDB."""
    seen = created = skipped = 0
    path = Path(file_path)
    with path.open("r", encoding="utf-8-sig") as source:
        for line_number, line in enumerate(source, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            parts = stripped.split("\t") if "\t" in stripped else stripped.rsplit(",", 1)
            if len(parts) != 2:
                continue
            seen += 1
            try:
                measured_at = _datetime(parts[0].strip(), tz)
                weight = float(parts[1].strip().replace(",", ".")) * scale
            except (TypeError, ValueError, OverflowError):
                continue
            if weight <= 0:
                continue
            if _already_present(db, measured_at, {"weight": weight}):
                skipped += 1
                continue
            digest = hashlib.sha256(f"{line_number}:{measured_at.isoformat()}:{weight}".encode()).hexdigest()[:32]
            group = MeasurementGroup(
                provider="legacy",
                provider_group_id=f"weight-file:{digest}",
                measured_at=measured_at,
                source_timezone=getattr(tz, "key", str(tz)),
                source="legacy",
                raw_payload={"file_import": True, "line": line_number},
            )
            db.add(group)
            db.flush()
            db.add(
                Measurement(
                    group_id=group.id,
                    kind="weight",
                    value=Decimal(str(weight)),
                    unit="kg",
                )
            )
            created += 1
    db.commit()
    return LegacyImportResult(1, seen, created, skipped)
