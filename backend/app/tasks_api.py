from __future__ import annotations

import calendar
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .ai_queue import latest_analysis
from .auth import AuthContext, require_csrf
from .db import get_db
from .evidence import snapshot_evidence_descriptors
from .feature_models import HealthTask, HealthTaskEvent


router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])
Recurrence = Literal["once", "daily", "weekly", "monthly"]


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TaskCreate(StrictModel):
    title: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    note: Annotated[str | None, StringConstraints(max_length=2_000)] = None
    next_due_at: datetime
    recurrence: Recurrence = "once"
    telegram_enabled: bool = True
    source_analysis_id: int | None = Field(default=None, ge=1)
    source_item_id: Annotated[str | None, StringConstraints(min_length=1, max_length=80)] = None

    @model_validator(mode="after")
    def source_is_complete(self) -> "TaskCreate":
        if (self.source_analysis_id is None) != (self.source_item_id is None):
            raise ValueError("source_analysis_id and source_item_id must be supplied together")
        if self.next_due_at.tzinfo is None:
            raise ValueError("timezone-aware datetime required")
        return self


class TaskPatch(StrictModel):
    title: Annotated[str | None, StringConstraints(min_length=1, max_length=200)] = None
    note: Annotated[str | None, StringConstraints(max_length=2_000)] = None
    next_due_at: datetime | None = None
    recurrence: Recurrence | None = None
    telegram_enabled: bool | None = None

    @model_validator(mode="after")
    def at_least_one_field(self) -> "TaskPatch":
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        if "next_due_at" in self.model_fields_set and self.next_due_at is None:
            raise ValueError("next_due_at cannot be null")
        if self.next_due_at is not None:
            if self.next_due_at.tzinfo is None:
                raise ValueError("timezone-aware datetime required")
        return self


def _task(row: HealthTask, now: datetime | None = None) -> dict:
    current = now or datetime.now(timezone.utc)
    due = _aware(row.next_due_at) if row.next_due_at is not None else None
    return {
        "id": row.id,
        "title": row.title,
        "note": row.note,
        "next_due_at": due,
        "recurrence": row.recurrence,
        "telegram_enabled": row.telegram_enabled,
        "status": row.status,
        "overdue": row.status == "active" and due is not None and due < current,
        "source_analysis_id": row.source_analysis_result_id,
        "source_item_id": row.source_item_id,
        "source": row.source_snapshot,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "completed_at": row.completed_at,
        "cancelled_at": row.cancelled_at,
    }


def _source_snapshot(db: Session, result_id: int, item_id: str) -> dict:
    state = latest_analysis(db)
    if state.result_id is None:
        raise HTTPException(status_code=404, detail="analysis_not_found")
    if (
        state.result_id != result_id
        or state.status not in {"ready", "stale"}
        or state.analysis is None
        or state.snapshot is None
        or state.generated_at is None
    ):
        raise HTTPException(status_code=409, detail="analysis_unavailable")
    analysis = state.analysis
    snapshot = state.snapshot

    prefix = "recommendation-"
    suffix = item_id[len(prefix) :] if item_id.startswith(prefix) else ""
    if not suffix.isascii() or not suffix.isdigit() or str(int(suffix)) != suffix:
        raise HTTPException(status_code=404, detail="recommendation_not_found")
    index = int(suffix) - 1
    recommendations = analysis.recommendations
    if index < 0 or index >= len(recommendations):
        raise HTTPException(status_code=404, detail="recommendation_not_found")
    item = recommendations[index]
    evidence_ids = list(item.evidence_keys)
    return {
        "kind": "ai_recommendation",
        "title": item.title,
        "text": item.text,
        "scope": item.scope,
        "evidence_ids": evidence_ids,
        "evidence": snapshot_evidence_descriptors(snapshot, evidence_ids, db=db),
        "generated_at": state.generated_at.isoformat(),
    }


def _next_occurrence(value: datetime, recurrence: str) -> datetime:
    from datetime import timedelta

    if recurrence == "daily":
        return value + timedelta(days=1)
    if recurrence == "weekly":
        return value + timedelta(days=7)
    if recurrence != "monthly":
        raise ValueError("one-time tasks do not recur")
    local = value
    year = local.year + (1 if local.month == 12 else 0)
    month = 1 if local.month == 12 else local.month + 1
    day = min(local.day, calendar.monthrange(year, month)[1])
    return local.replace(year=year, month=month, day=day)


@router.get("")
def list_tasks(
    state: Annotated[Literal["open", "completed", "all"], Query()] = "open",
    db: Session = Depends(get_db),
) -> dict:
    query = select(HealthTask)
    if state == "open":
        query = query.where(HealthTask.status == "active")
    elif state == "completed":
        query = query.where(HealthTask.status == "completed")
    rows = list(db.scalars(query.order_by(HealthTask.next_due_at, HealthTask.created_at)))
    current = datetime.now(timezone.utc)
    items = [_task(row, current) for row in rows]
    return {
        "items": items,
        "open_count": int(
            db.scalar(
                select(func.count()).select_from(HealthTask).where(
                    HealthTask.status == "active"
                )
            )
            or 0
        ),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_task(
    payload: TaskCreate,
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    current = datetime.now(timezone.utc)
    due = _aware(payload.next_due_at)
    if due <= current:
        raise HTTPException(status_code=422, detail="next_due_at_must_be_future")
    source = None
    if payload.source_analysis_id is not None and payload.source_item_id is not None:
        source = _source_snapshot(db, payload.source_analysis_id, payload.source_item_id)
    row = HealthTask(
        id=str(uuid4()),
        title=payload.title,
        note=payload.note or None,
        next_due_at=due,
        recurrence=payload.recurrence,
        telegram_enabled=payload.telegram_enabled,
        status="active",
        source_analysis_result_id=payload.source_analysis_id,
        source_item_id=payload.source_item_id,
        source_snapshot=source,
        created_at=current,
        updated_at=current,
    )
    db.add(row)
    db.flush()
    db.add(
        HealthTaskEvent(
            task_id=row.id,
            event_type="created",
            occurrence_at=due,
            payload={"recurrence": row.recurrence, "telegram_enabled": row.telegram_enabled},
            created_at=current,
        )
    )
    db.commit()
    db.refresh(row)
    return _task(row, current)


def _active_task(db: Session, task_id: str) -> HealthTask:
    row = db.get(HealthTask, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="task_not_found")
    if row.status != "active":
        raise HTTPException(status_code=409, detail="task_not_active")
    return row


@router.patch("/{task_id}")
def update_task(
    task_id: str,
    payload: TaskPatch,
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    row = _active_task(db, task_id)
    current = datetime.now(timezone.utc)
    if "next_due_at" in payload.model_fields_set:
        due = _aware(payload.next_due_at)  # type: ignore[arg-type]
        if due <= current:
            raise HTTPException(status_code=422, detail="next_due_at_must_be_future")
        row.next_due_at = due
    for field in ("title", "recurrence", "telegram_enabled"):
        if field in payload.model_fields_set:
            setattr(row, field, getattr(payload, field))
    if "note" in payload.model_fields_set:
        row.note = payload.note or None
    row.updated_at = current
    db.add(
        HealthTaskEvent(
            task_id=row.id,
            event_type="updated",
            occurrence_at=row.next_due_at,
            payload={"recurrence": row.recurrence, "telegram_enabled": row.telegram_enabled},
            created_at=current,
        )
    )
    db.commit()
    db.refresh(row)
    return _task(row, current)


@router.post("/{task_id}/complete")
def complete_task(
    task_id: str,
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    row = _active_task(db, task_id)
    current = datetime.now(timezone.utc)
    occurrence = _aware(row.next_due_at) if row.next_due_at is not None else current
    db.add(
        HealthTaskEvent(
            task_id=row.id,
            event_type="completed",
            occurrence_at=occurrence,
            payload={},
            created_at=current,
        )
    )
    if row.recurrence == "once":
        row.status = "completed"
        row.completed_at = current
        row.next_due_at = None
    else:
        next_due = _next_occurrence(occurrence, row.recurrence)
        while next_due <= current:
            next_due = _next_occurrence(next_due, row.recurrence)
        row.next_due_at = next_due
    row.updated_at = current
    db.commit()
    db.refresh(row)
    return _task(row, current)


@router.post("/{task_id}/cancel")
def cancel_task(
    task_id: str,
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    row = _active_task(db, task_id)
    current = datetime.now(timezone.utc)
    occurrence = row.next_due_at
    row.status = "cancelled"
    row.cancelled_at = current
    row.next_due_at = None
    row.updated_at = current
    db.add(
        HealthTaskEvent(
            task_id=row.id,
            event_type="cancelled",
            occurrence_at=occurrence,
            payload={},
            created_at=current,
        )
    )
    db.commit()
    db.refresh(row)
    return _task(row, current)
