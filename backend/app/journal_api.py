from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .analytics import local_range_start
from .auth import AuthContext, require_csrf
from .auth_models import UserProfile
from .config import Settings, get_settings
from .db import get_db
from .health_analytics import activity_series, recovery_series
from .journal import (
    CHECKIN_CORRELATION_MINIMUM_DAYS,
    checkin_correlations,
    checkin_payload,
    list_checkins,
    list_notes,
    milestones,
    note_payload,
)
from .journal_models import NOTE_CATEGORIES, DailyCheckin, DayNote
from .service import active_plan, weight_daily


router = APIRouter(prefix="/api/v1")
JournalRange = Annotated[Literal["30d", "90d", "1y", "all"], Query()]
NoteCategory = Literal["illness", "holiday", "travel", "medication", "stress", "other"]
NoteId = Annotated[str, Path(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")]
MAX_NOTES = 2_000
assert set(NoteCategory.__args__) == set(NOTE_CATEGORIES)  # type: ignore[attr-defined]


def _today(settings: Settings) -> date:
    return datetime.now(timezone.utc).astimezone(settings.tz).date()


def _check_not_future(day: date, settings: Settings) -> None:
    if day > _today(settings):
        raise HTTPException(status_code=422, detail="future_day")


class NoteInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    day: date
    category: NoteCategory
    text: str = Field(min_length=1, max_length=280)


class NotePatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    day: date | None = None
    category: NoteCategory | None = None
    text: str | None = Field(default=None, min_length=1, max_length=280)

    @model_validator(mode="after")
    def has_value(self) -> "NotePatch":
        if not self.model_fields_set:
            raise ValueError("at least one note field is required")
        for name in self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class CheckinInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    energy: int | None = Field(default=None, ge=1, le=5)
    hunger: int | None = Field(default=None, ge=1, le=5)
    mood: int | None = Field(default=None, ge=1, le=5)

    @model_validator(mode="after")
    def has_value(self) -> "CheckinInput":
        if self.energy is None and self.hunger is None and self.mood is None:
            raise ValueError("at least one self-assessment is required")
        return self


@router.get("/notes")
def get_notes(
    range: JournalRange = "all",
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    start = local_range_start(range, settings.tz)
    return {"range": range, "items": [note_payload(row) for row in list_notes(db, start)]}


@router.post("/notes", status_code=status.HTTP_201_CREATED)
def create_note(
    payload: NoteInput,
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    _check_not_future(payload.day, settings)
    if (db.scalar(select(func.count()).select_from(DayNote)) or 0) >= MAX_NOTES:
        raise HTTPException(status_code=422, detail="too_many_notes")
    row = DayNote(day=payload.day, category=payload.category, text=payload.text)
    db.add(row)
    db.commit()
    db.refresh(row)
    return note_payload(row)


@router.patch("/notes/{note_id}")
def patch_note(
    note_id: NoteId,
    payload: NotePatch,
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    row = db.get(DayNote, note_id)
    if row is None:
        raise HTTPException(status_code=404, detail="note_not_found")
    values = payload.model_dump(exclude_unset=True)
    if "day" in values:
        _check_not_future(values["day"], settings)
        row.day = values["day"]
    if "category" in values:
        row.category = values["category"]
    if "text" in values:
        row.text = values["text"]
    db.commit()
    db.refresh(row)
    return note_payload(row)


@router.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(
    note_id: NoteId,
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> Response:
    row = db.get(DayNote, note_id)
    if row is None:
        raise HTTPException(status_code=404, detail="note_not_found")
    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/checkins")
def get_checkins(
    range: JournalRange = "90d",
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    start = local_range_start(range, settings.tz)
    rows = list_checkins(db, start)
    # Correlations always use the complete history so a short display range
    # cannot hide a sufficiently long paired sample.
    all_rows = rows if start is None else list_checkins(db)
    correlations = []
    # Skip the full recovery/activity reads until a correlation is possible;
    # the overview loads this route on every visit on a weak host.
    if len(all_rows) >= CHECKIN_CORRELATION_MINIMUM_DAYS:
        correlations = checkin_correlations(
            all_rows,
            recovery_series(db, settings.tz, "all")["daily"],
            activity_series(db, settings.tz, "all")["daily"],
        )
    return {
        "range": range,
        "today": _today(settings).isoformat(),
        "items": [checkin_payload(row) for row in rows],
        "correlations": correlations,
    }


@router.put("/checkins/{day}")
def put_checkin(
    day: date,
    payload: CheckinInput,
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    _check_not_future(day, settings)
    row = db.get(DailyCheckin, day)
    if row is None:
        row = DailyCheckin(day=day)
        db.add(row)
    # PUT replaces the complete assessment for that day.
    row.energy = payload.energy
    row.hunger = payload.hunger
    row.mood = payload.mood
    db.commit()
    db.refresh(row)
    return checkin_payload(row)


@router.delete("/checkins/{day}", status_code=status.HTTP_204_NO_CONTENT)
def delete_checkin(
    day: date,
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> Response:
    row = db.get(DailyCheckin, day)
    if row is None:
        raise HTTPException(status_code=404, detail="checkin_not_found")
    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/milestones")
def get_milestones(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    plan = active_plan(db)
    today = _today(settings)
    daily = [point for point in weight_daily(db, settings.tz, plan.start_date) if point.day <= today]
    profile = db.get(UserProfile, 1)
    height = float(profile.height_cm) if profile is not None else settings.user_height_cm
    return milestones(daily, plan, height, today)
