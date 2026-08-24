from __future__ import annotations

from hashlib import sha256
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .ai_contracts import AnalysisSnapshot, SnapshotFact, SnapshotLabResult, SnapshotSeries
from .lab_models import LabResult


_SCOPE_LABELS = {
    "profile": "Профиль",
    "weight": "Вес",
    "composition": "Состав тела",
    "activity": "Активность",
    "sleep": "Сон",
    "recovery": "Восстановление",
    "heart": "Пульс и сердце",
    "oxygen": "Сатурация",
    "vo2": "VO₂ max",
    "pressure": "Давление",
    "quality": "Качество данных",
    "correlation": "Корреляция",
    "laboratory": "Лабораторный результат",
}


def _target_for_scope(scope: str) -> dict[str, str | bool | None]:
    path = {
        "profile": "/profile",
        "weight": "/progress",
        "composition": "/composition",
        "activity": "/activity",
        "sleep": "/recovery",
        "recovery": "/recovery",
        "heart": "/recovery",
        "oxygen": "/recovery",
        "vo2": "/recovery",
        "pressure": "/pressure",
        "quality": "/data-quality",
        "correlation": "/activity",
    }.get(scope)
    return {"path": path, "available": path is not None}


def _fact_descriptor(item: SnapshotFact) -> dict[str, Any]:
    return {
        "key": item.key,
        "kind": "fact",
        "metric": item.scope,
        "label": _SCOPE_LABELS.get(item.scope, item.scope),
        "value": item.value,
        "unit": item.unit,
        "date": item.observed_on.isoformat() if item.observed_on else None,
        "period": item.period,
        "target": _target_for_scope(item.scope),
    }


def _series_descriptor(item: SnapshotSeries) -> dict[str, Any]:
    points = sorted(item.points, key=lambda point: point.day)
    return {
        "key": item.key,
        "kind": "series",
        "metric": item.scope,
        "label": _SCOPE_LABELS.get(item.scope, item.scope),
        "unit": item.unit,
        "range": {
            "from": points[0].day.isoformat(),
            "to": points[-1].day.isoformat(),
        },
        "count": len(points),
        "target": _target_for_scope(item.scope),
    }


def _lab_key(result_id: str) -> str:
    return f"lab.{sha256(result_id.encode()).hexdigest()[:20]}"


def _lab_targets(
    db: Session | None,
    keys: set[str],
) -> dict[str, dict[str, str | bool | None]]:
    if db is None or not keys:
        return {}
    targets: dict[str, dict[str, str | bool | None]] = {}
    rows = db.execute(select(LabResult.id, LabResult.document_id, LabResult.deleted))
    for result_id, document_id, deleted in rows:
        key = _lab_key(result_id)
        if key not in keys:
            continue
        targets[key] = {
            "path": (
                f"/labs/documents/{document_id}#result-{result_id}"
                if not deleted
                else None
            ),
            "available": not deleted,
        }
    return targets


def _lab_descriptor(
    item: SnapshotLabResult,
    target: dict[str, str | bool | None] | None,
) -> dict[str, Any]:
    return {
        "key": item.key,
        "kind": "laboratory",
        "metric": "laboratory",
        "label": item.analyte,
        "value": item.value_numeric,
        "text": item.value_text,
        "comparator": item.comparator,
        "unit": item.unit,
        "date": item.observed_on.isoformat(),
        "range": {
            "low": item.reference_low,
            "high": item.reference_high,
            "text": item.reference_text,
            "source": item.reference_source,
        },
        "reference_status": item.status,
        "verification": "verified" if item.verified else "unverified",
        "target": target or {"path": None, "available": False},
    }


def snapshot_evidence_descriptors(
    snapshot: AnalysisSnapshot,
    cited_keys: Iterable[str],
    *,
    db: Session | None = None,
) -> dict[str, dict[str, Any]]:
    """Build bounded display metadata from one immutable persisted snapshot.

    The current database is consulted only to resolve an authenticated lab deep
    link. Captured values, dates, ranges and verification always come from the
    supplied snapshot and therefore cannot drift when source rows are edited.
    """

    requested = {key for key in cited_keys if isinstance(key, str)}
    lab_targets = _lab_targets(
        db, {key for key in requested if key.startswith("lab.")}
    )
    descriptors: dict[str, dict[str, Any]] = {}
    for item in snapshot.facts:
        if item.key in requested:
            descriptors[item.key] = _fact_descriptor(item)
    for item in snapshot.series:
        if item.key in requested:
            descriptors[item.key] = _series_descriptor(item)
    for item in snapshot.labs:
        if item.key in requested:
            descriptors[item.key] = _lab_descriptor(item, lab_targets.get(item.key))
    return {key: descriptors[key] for key in sorted(descriptors)}
