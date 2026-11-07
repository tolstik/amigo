from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4

from sqlalchemy import CheckConstraint, Date, DateTime, Index, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .models import utcnow


NOTE_CATEGORIES = ("illness", "holiday", "travel", "medication", "stress", "other")


class DayNote(Base):
    """A short owner-entered annotation for one Moscow calendar day.

    Notes explain measurement changes on charts. They are private dashboard
    data and are never sent to AI inference, Telegram, or doctor reports.
    """

    __tablename__ = "day_notes"
    __table_args__ = (
        CheckConstraint(
            "category IN ('illness','holiday','travel','medication','stress','other')",
            name="ck_day_notes_category",
        ),
        Index("ix_day_notes_day", "day"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    day: Mapped[date] = mapped_column(Date, nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(String(280), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class DailyCheckin(Base):
    """A one-tap subjective 1–5 self-assessment for one Moscow calendar day."""

    __tablename__ = "daily_checkins"
    __table_args__ = (
        CheckConstraint(
            "energy IS NOT NULL OR hunger IS NOT NULL OR mood IS NOT NULL",
            name="ck_daily_checkins_has_value",
        ),
        CheckConstraint("energy IS NULL OR (energy BETWEEN 1 AND 5)", name="ck_daily_checkins_energy"),
        CheckConstraint("hunger IS NULL OR (hunger BETWEEN 1 AND 5)", name="ck_daily_checkins_hunger"),
        CheckConstraint("mood IS NULL OR (mood BETWEEN 1 AND 5)", name="ck_daily_checkins_mood"),
    )

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    energy: Mapped[int | None] = mapped_column(SmallInteger)
    hunger: Mapped[int | None] = mapped_column(SmallInteger)
    mood: Mapped[int | None] = mapped_column(SmallInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
