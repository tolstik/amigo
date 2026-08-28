from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, Integer, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .models import utcnow


class BodyCircumference(Base):
    """User-entered daily waist and hip measurements in centimetres."""

    __tablename__ = "body_circumference_measurements"
    __table_args__ = (
        UniqueConstraint("measured_on", name="uq_body_circumference_date"),
        CheckConstraint(
            "waist_cm IS NOT NULL OR hip_cm IS NOT NULL",
            name="ck_body_circumference_has_value",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    measured_on: Mapped[date] = mapped_column(Date, nullable=False)
    waist_cm: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    hip_cm: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
