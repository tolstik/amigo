from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import textwrap
from typing import Annotated, Literal
from uuid import uuid4

import fitz
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .ai_queue import public_analysis_payload
from .auth import AuthContext, require_csrf
from .config import Settings, get_settings
from .db import get_db
from .feature_models import DoctorReportSnapshot
from .health_analytics import activity_series, recovery_series
from .lab_models import LabResult, StudyDocument
from .service import overview, pressure_series, weight_series


router = APIRouter(prefix="/api/v1/reports/doctor", tags=["reports"])
Period = Literal["30d", "90d", "1y"]
Section = Literal[
    "summary",
    "weight",
    "pressure",
    "activity",
    "recovery",
    "labs",
    "studies",
    "ai",
]
DEFAULT_SECTIONS: list[Section] = [
    "summary",
    "weight",
    "pressure",
    "activity",
    "recovery",
    "labs",
    "studies",
]
MAX_REPORT_PAGES = 40
MAX_REPORT_BYTES = 10 * 1024 * 1024
REPORT_TTL = timedelta(hours=24)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DoctorReportCreate(StrictModel):
    period: Period = "90d"
    sections: list[Section] = Field(default_factory=lambda: list(DEFAULT_SECTIONS), min_length=1)

    @model_validator(mode="after")
    def unique_sections(self) -> "DoctorReportCreate":
        if len(set(self.sections)) != len(self.sections):
            raise ValueError("sections must be unique")
        return self


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _lab_value(row: LabResult) -> str:
    value = str(row.value_numeric) if row.value_numeric is not None else (row.value_text or "—")
    if row.comparator and row.comparator != "=":
        value = f"{row.comparator}{value}"
    return f"{value} {row.unit}".strip() if row.unit else value


def _lab_reference(row: LabResult) -> str | None:
    if row.reference_text:
        return row.reference_text
    if row.reference_low is not None and row.reference_high is not None:
        return f"{row.reference_low}–{row.reference_high} {row.unit or ''}".strip()
    if row.reference_low is not None:
        return f"от {row.reference_low} {row.unit or ''}".strip()
    if row.reference_high is not None:
        return f"до {row.reference_high} {row.unit or ''}".strip()
    return None


def build_doctor_report_payload(
    db: Session,
    settings: Settings,
    request: DoctorReportCreate,
    now: datetime,
) -> dict:
    local_today = now.astimezone(settings.tz).date()
    days = {"30d": 30, "90d": 90, "1y": 365}[request.period]
    start = local_today - timedelta(days=days - 1)
    sections = set(request.sections)
    payload: dict = {
        "meta": {
            "created_at": now.isoformat(),
            "period": request.period,
            "from": start.isoformat(),
            "to": local_today.isoformat(),
            "timezone": str(settings.tz),
        },
        "sections": {},
    }
    if "summary" in sections:
        source = overview(db, settings.tz, now)
        payload["sections"]["summary"] = {
            "height_cm": settings.user_height_cm,
            "plan": source.get("plan"),
            "weight": source.get("weight"),
            "pressure": source.get("pressure"),
            "composition": source.get("composition"),
        }
    if "weight" in sections:
        payload["sections"]["weight"] = weight_series(db, settings.tz, request.period, now)
    if "pressure" in sections:
        payload["sections"]["pressure"] = pressure_series(db, settings.tz, request.period, now)
    if "activity" in sections:
        payload["sections"]["activity"] = activity_series(db, settings.tz, request.period, now)
    if "recovery" in sections:
        payload["sections"]["recovery"] = recovery_series(db, settings.tz, request.period, now)
    if "labs" in sections:
        rows = list(
            db.scalars(
                select(LabResult)
                .where(
                    LabResult.deleted.is_(False),
                    LabResult.verification_status.in_(("verified", "corrected")),
                    LabResult.observed_on >= start,
                    LabResult.observed_on <= local_today,
                )
                .order_by(LabResult.observed_on, LabResult.analyte_name, LabResult.id)
            )
        )
        payload["sections"]["labs"] = [
            {
                "analyte": row.analyte_name,
                "value": _lab_value(row),
                "observed_on": _iso(row.observed_on),
                "reference": _lab_reference(row),
                "status": row.status,
                "verification_status": row.verification_status,
            }
            for row in rows
        ]
    if "studies" in sections:
        rows = list(
            db.scalars(
                select(StudyDocument)
                .where(
                    StudyDocument.status == "complete",
                    StudyDocument.verified.is_(True),
                    StudyDocument.observed_on >= start,
                    StudyDocument.observed_on <= local_today,
                )
                .order_by(StudyDocument.observed_on, StudyDocument.id)
            )
        )
        payload["sections"]["studies"] = [
            {
                "modality": row.modality,
                "observed_on": _iso(row.observed_on),
                "findings": list(row.findings or []),
                "conclusion": row.conclusion,
            }
            for row in rows
        ]
    if "ai" in sections:
        cached = public_analysis_payload(db)
        analysis = cached.get("analysis") if cached.get("status") == "ready" else None
        recommendations = analysis.get("recommendations") if isinstance(analysis, dict) else []
        payload["sections"]["ai"] = [
            {
                "title": str(item.get("title") or "Рекомендация"),
                "text": item["text"],
                "evidence_ids": [
                    key for key in item.get("evidence_keys", []) if isinstance(key, str)
                ],
            }
            for item in recommendations or []
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        ]
    # This snapshot is deliberately structured and bounded. Never add originals,
    # filenames, OCR, chat, device/account identity, or provider payloads here.
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode()
    if len(encoded) > 5 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="report_snapshot_too_large")
    return payload


class _PdfWriter:
    def __init__(self, payload: dict):
        self.payload = payload
        self.document = fitz.open()
        self.page: fitz.Page | None = None
        self.y = 0.0
        self.font_name = "helv"
        self.font_file = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
        self._new_page()

    def _new_page(self) -> None:
        if self.document.page_count >= MAX_REPORT_PAGES:
            raise HTTPException(status_code=422, detail="report_too_many_pages")
        self.page = self.document.new_page(width=595, height=842)
        if self.font_file.is_file():
            self.page.insert_font(fontname="amigo", fontfile=str(self.font_file))
            self.font_name = "amigo"
        self.y = 48

    def _ensure(self, height: float) -> None:
        if self.y + height > 800:
            self._new_page()

    def text(self, value: object, size: float = 10, *, bold: bool = False, gap: float = 4) -> None:
        clean = " ".join(str(value).replace("\x00", " ").split())
        width = max(20, int(92 * 10 / max(size, 1)))
        lines = textwrap.wrap(clean, width=width, break_long_words=True) or [""]
        line_height = size * 1.45
        for line in lines:
            self._ensure(line_height)
            assert self.page is not None
            self.page.insert_text(
                (46, self.y),
                line,
                fontsize=size,
                fontname=self.font_name,
                color=(0.05, 0.08, 0.12),
            )
            self.y += line_height
        if self.y + gap <= 800:
            self.y += gap

    def heading(self, value: str) -> None:
        self.y += 5
        self.text(value, 15, bold=True, gap=7)

    def chart(self, title: str, rows: list[dict], key: str, *, divisor: float = 1.0, unit: str) -> None:
        points = []
        for index, row in enumerate(rows):
            value = row.get(key)
            if isinstance(value, (int, float)):
                points.append((index, float(value) / divisor))
        self.heading(title)
        if not points:
            self.text("Нет данных.")
            return
        self._ensure(150)
        assert self.page is not None
        left, top, right, bottom = 60.0, self.y + 8, 545.0, self.y + 128
        self.page.draw_rect(fitz.Rect(left, top, right, bottom), color=(0.75, 0.78, 0.82))
        low, high = min(value for _, value in points), max(value for _, value in points)
        if high == low:
            high = low + 1
        path = []
        count = max(1, len(rows) - 1)
        for index, value in points:
            x = left + (right - left) * index / count
            y = bottom - (bottom - top) * (value - low) / (high - low)
            path.append(fitz.Point(x, y))
        for first, second in zip(path, path[1:]):
            self.page.draw_line(first, second, color=(0.14, 0.46, 0.33), width=1.8)
        self.page.insert_text(
            (left, bottom + 15),
            f"{low:.1f}–{high:.1f} {unit}",
            fontsize=8,
            fontname=self.font_name,
        )
        self.y = bottom + 28


def render_doctor_report(payload: dict) -> bytes:
    writer = _PdfWriter(payload)
    meta = payload.get("meta") or {}
    writer.text("AMIGO · ПАКЕТ ДЛЯ ВРАЧА", 20, bold=True, gap=8)
    writer.text(f"Период: {meta.get('from', '—')} — {meta.get('to', '—')}")
    writer.text("Информационная сводка измерений; не диагноз и не назначение лечения.", 9)
    sections = payload.get("sections") or {}
    summary = sections.get("summary")
    if isinstance(summary, dict):
        writer.heading("Краткая сводка")
        writer.text(f"Рост: {summary.get('height_cm', '—')} см")
        weight = summary.get("weight") or {}
        writer.text(f"Последний вес: {weight.get('latest_kg', '—')} кг")
        pressure = summary.get("pressure") or {}
        writer.text(
            f"Последнее давление: {pressure.get('latest_systolic', '—')}/"
            f"{pressure.get('latest_diastolic', '—')} мм рт. ст."
        )
    weight = sections.get("weight")
    if isinstance(weight, dict):
        writer.chart("Вес", list(weight.get("points") or []), "weight_kg", unit="кг")
    pressure = sections.get("pressure")
    if isinstance(pressure, dict):
        writer.chart("Систолическое давление", list(pressure.get("points") or []), "systolic", unit="мм рт. ст.")
        writer.chart("Диастолическое давление", list(pressure.get("points") or []), "diastolic", unit="мм рт. ст.")
    activity = sections.get("activity")
    if isinstance(activity, dict):
        writer.chart("Шаги · только Xiaomi Cloud", list(activity.get("daily") or []), "steps", unit="шаги")
    recovery = sections.get("recovery")
    if isinstance(recovery, dict):
        writer.chart(
            "Продолжительность сна",
            list(recovery.get("daily") or []),
            "sleep_minutes",
            divisor=60,
            unit="часы",
        )
    labs = sections.get("labs")
    if isinstance(labs, list):
        writer.heading("Подтверждённые лабораторные результаты")
        if not labs:
            writer.text("Нет результатов за выбранный период.")
        for item in labs:
            if not isinstance(item, dict):
                continue
            reference = f" · референс {item['reference']}" if item.get("reference") else ""
            writer.text(
                f"{item.get('observed_on') or 'Дата не указана'} · {item.get('analyte')}: "
                f"{item.get('value')}{reference} · {item.get('status')}",
                9,
            )
    studies = sections.get("studies")
    if isinstance(studies, list):
        writer.heading("Подтверждённые исследования")
        if not studies:
            writer.text("Нет исследований за выбранный период.")
        for item in studies:
            if not isinstance(item, dict):
                continue
            writer.text(f"{item.get('observed_on') or 'Дата не указана'} · {item.get('modality')}", 10)
            for finding in item.get("findings") or []:
                writer.text(f"• {finding}", 9)
            if item.get("conclusion"):
                writer.text(f"Заключение: {item['conclusion']}", 9)
    ai = sections.get("ai")
    if isinstance(ai, list):
        writer.heading("Валидированные рекомендации AI")
        if not ai:
            writer.text("Готовых рекомендаций нет.")
        for item in ai:
            if isinstance(item, dict):
                writer.text(f"{item.get('title')}: {item.get('text')}", 9)
    writer.document.set_metadata(
        {
            "title": "Amigo — пакет для врача",
            "author": "Amigo",
            "subject": f"Период {meta.get('from')} — {meta.get('to')}",
            "producer": "Amigo",
        }
    )
    output = writer.document.tobytes(garbage=4, deflate=True)
    page_count = writer.document.page_count
    writer.document.close()
    if page_count > MAX_REPORT_PAGES:
        raise HTTPException(status_code=422, detail="report_too_many_pages")
    if len(output) > MAX_REPORT_BYTES:
        raise HTTPException(status_code=422, detail="report_too_large")
    return output


def _get_snapshot(db: Session, report_id: str, now: datetime) -> DoctorReportSnapshot:
    row = db.get(DoctorReportSnapshot, report_id)
    if row is None:
        raise HTTPException(status_code=404, detail="doctor_report_not_found")
    expires = row.expires_at.replace(tzinfo=timezone.utc) if row.expires_at.tzinfo is None else row.expires_at
    if expires <= now:
        raise HTTPException(status_code=410, detail="doctor_report_expired")
    return row


def _public_snapshot(row: DoctorReportSnapshot) -> dict:
    return {
        "id": row.id,
        "options": row.options,
        "preview": row.payload,
        "page_count": row.page_count,
        "size_bytes": row.size_bytes,
        "created_at": row.created_at,
        "expires_at": row.expires_at,
        "download_url": f"/amigo/api/v1/reports/doctor/{row.id}.pdf",
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_doctor_report(
    request: DoctorReportCreate,
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    now = datetime.now(timezone.utc)
    payload = build_doctor_report_payload(db, settings, request, now)
    rendered = render_doctor_report(payload)
    # Page count is validated from the exact same immutable view model used by GET.
    with fitz.open(stream=rendered, filetype="pdf") as document:
        page_count = document.page_count
    row = DoctorReportSnapshot(
        id=str(uuid4()),
        options=request.model_dump(mode="json"),
        payload=payload,
        page_count=page_count,
        size_bytes=len(rendered),
        created_at=now,
        expires_at=now + REPORT_TTL,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _public_snapshot(row)


@router.get("/{report_id}.pdf")
def download_doctor_report(report_id: str, db: Session = Depends(get_db)) -> Response:
    row = _get_snapshot(db, report_id, datetime.now(timezone.utc))
    rendered = render_doctor_report(row.payload)
    if len(rendered) > MAX_REPORT_BYTES:
        raise HTTPException(status_code=422, detail="report_too_large")
    return Response(
        rendered,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'attachment; filename="amigo-doctor-report.pdf"',
            "Content-Length": str(len(rendered)),
        },
    )


@router.get("/{report_id}")
def get_doctor_report(report_id: str, db: Session = Depends(get_db)) -> dict:
    return _public_snapshot(_get_snapshot(db, report_id, datetime.now(timezone.utc)))


@router.delete("/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_doctor_report(
    report_id: str,
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> Response:
    row = db.get(DoctorReportSnapshot, report_id)
    if row is None:
        raise HTTPException(status_code=404, detail="doctor_report_not_found")
    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
