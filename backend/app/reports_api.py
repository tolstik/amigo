from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from html import escape
import json
from pathlib import Path
import textwrap
from typing import Annotated, Literal
from uuid import uuid4

import fitz
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from .ai_queue import public_analysis_payload
from .auth import AuthContext, require_csrf
from .config import Settings, get_settings
from .db import get_db
from .feature_models import DoctorReportSnapshot
from .health_analytics import activity_series, recovery_series
from .lab_models import LabReport, LabResult, StudyDocument
from .service import circumference_series, overview, pressure_series, weight_series


router = APIRouter(prefix="/api/v1/reports/doctor", tags=["reports"])
Period = Literal["30d", "90d", "1y"]
Section = Literal[
    "summary",
    "weight",
    "circumference",
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
    "circumference",
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
    if "circumference" in sections:
        payload["sections"]["circumference"] = circumference_series(db, settings.tz, request.period, now)
    if "pressure" in sections:
        payload["sections"]["pressure"] = pressure_series(db, settings.tz, request.period, now)
    if "activity" in sections:
        payload["sections"]["activity"] = activity_series(db, settings.tz, request.period, now)
    if "recovery" in sections:
        payload["sections"]["recovery"] = recovery_series(db, settings.tz, request.period, now)
    if "labs" in sections:
        effective_lab_date = or_(
            and_(
                LabResult.observed_on.is_not(None),
                LabResult.observed_on >= start,
                LabResult.observed_on <= local_today,
            ),
            and_(
                LabResult.observed_on.is_(None),
                LabReport.observed_on >= start,
                LabReport.observed_on <= local_today,
            ),
        )
        excluded_unverified = db.scalar(
            select(func.count(LabResult.id))
            .outerjoin(LabReport, LabResult.report_id == LabReport.id)
            .where(
                LabResult.deleted.is_(False),
                ~LabResult.verification_status.in_(("verified", "corrected")),
                effective_lab_date,
            )
        ) or 0
        payload["meta"]["labs_unverified_count"] = int(excluded_unverified)
        rows = list(
            db.scalars(
                select(LabResult)
                .outerjoin(LabReport, LabResult.report_id == LabReport.id)
                .where(
                    LabResult.deleted.is_(False),
                    effective_lab_date,
                )
                .order_by(LabResult.observed_on, LabResult.analyte_name, LabResult.id)
            )
        )
        report_dates = {
            report_id: observed_on
            for report_id, observed_on in db.execute(
                select(LabReport.id, LabReport.observed_on).where(
                    LabReport.id.in_({row.report_id for row in rows if row.report_id is not None})
                )
            )
        }
        rows.sort(
            key=lambda row: (
                row.observed_on or report_dates.get(row.report_id) or date.min,
                row.analyte_name,
                row.id,
            )
        )
        payload["sections"]["labs"] = [
            {
                "analyte": row.analyte_name,
                "value": _lab_value(row),
                "observed_on": _iso(row.observed_on or report_dates.get(row.report_id)),
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


HTML_MAX_BYTES = 10 * 1024 * 1024


def _html(value: object) -> str:
    return escape("" if value is None else str(value), quote=True)


def _svg_chart(
    title: str,
    rows: list[dict],
    series: list[tuple[str, str, str]],
    *,
    date_key: str = "measured_at",
    unit: str,
) -> str:
    width, height = 760, 250
    left, right, top, bottom = 52, 18, 30, 42
    values = [
        float(row[field])
        for row in rows
        for field, _label, _color in series
        if isinstance(row.get(field), (int, float))
    ]
    if not values:
        return f'<section class="report-card report-chart"><h2>{_html(title)}</h2><p class="muted">Нет данных за выбранный период.</p></section>'
    low, high = min(values), max(values)
    pad = max((high - low) * 0.12, 1.0)
    low -= pad
    high += pad
    plot_width = width - left - right
    plot_height = height - top - bottom
    count = max(1, len(rows) - 1)

    def point(index: int, value: float) -> tuple[float, float]:
        x = left + plot_width * index / count
        y = top + plot_height * (high - value) / (high - low)
        return x, y

    grid = []
    for fraction in (0, 0.5, 1):
        y = top + plot_height * fraction
        label = high - (high - low) * fraction
        grid.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" class="grid"/>'
            f'<text x="{left - 8}" y="{y + 4:.1f}" text-anchor="end" class="axis">{label:.1f}</text>'
        )

    paths: list[str] = []
    legend: list[str] = []
    for field, label, color in series:
        segments: list[list[str]] = []
        current: list[str] = []
        for index, row in enumerate(rows):
            value = row.get(field)
            if isinstance(value, (int, float)):
                x, y = point(index, float(value))
                current.append(f"{x:.1f},{y:.1f}")
            elif current:
                segments.append(current)
                current = []
        if current:
            segments.append(current)
        for segment in segments:
            if len(segment) == 1:
                x, y = segment[0].split(",")
                paths.append(f'<circle cx="{x}" cy="{y}" r="3.5" fill="{color}"/>')
            else:
                paths.append(f'<polyline points="{" ".join(segment)}" fill="none" stroke="{color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>')
        legend.append(f'<span><i style="background:{color}"></i>{_html(label)}</span>')

    first = _html(rows[0].get(date_key) if rows else "")
    last = _html(rows[-1].get(date_key) if rows else "")
    return (
        f'<section class="report-card report-chart"><div class="report-card__head"><h2>{_html(title)}</h2>'
        f'<span class="unit">{_html(unit)}</span></div>'
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{_html(title)}">'
        f'<rect x="{left}" y="{top}" width="{plot_width}" height="{plot_height}" class="plot"/>'
        f'{"".join(grid)}{"".join(paths)}'
        f'<text x="{left}" y="{height - 12}" class="axis">{first}</text>'
        f'<text x="{width - right}" y="{height - 12}" text-anchor="end" class="axis">{last}</text>'
        '</svg>'
        f'<div class="legend">{"".join(legend)}</div></section>'
    )


def _html_table(headers: list[str], rows: list[list[object]]) -> str:
    head = "".join(f"<th>{_html(value)}</th>" for value in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{_html(value if value not in (None, '') else '—')}</td>" for value in row) + "</tr>"
        for row in rows
    )
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _echarts_runtime(static_dir: Path | None) -> str | None:
    candidate = (static_dir or Path("/app/static")) / "echarts.min.js"
    try:
        runtime = candidate.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return runtime.replace("</script", "<\\/script") if runtime else None


def _render_echarts_report_html(payload: dict, runtime: str) -> bytes:
    meta = payload.get("meta") or {}
    sections = payload.get("sections") or {}
    lab_status_labels = {
        "within_reference": "В референсе",
        "below_reference": "Ниже референса",
        "above_reference": "Выше референса",
        "outside_reference": "Вне референса",
        "indeterminate": "Без оценки",
    }
    modality_labels = {
        "ultrasound": "УЗИ", "mri": "МРТ", "ct": "КТ", "xray": "Рентген",
        "ecg": "ЭКГ", "other": "Исследование",
    }
    blocks: list[str] = []

    def chart(chart_id: str, title: str, unit: str) -> None:
        blocks.append(
            f'<section class="report-card report-chart"><div class="report-card__head"><h2>{_html(title)}</h2>'
            f'<span class="unit">{_html(unit)}</span></div><div class="echart" id="{_html(chart_id)}" role="img" aria-label="{_html(title)}"></div></section>'
        )

    if isinstance(sections.get("summary"), dict):
        summary = sections["summary"]
        weight = summary.get("weight") or {}
        pressure = summary.get("pressure") or {}
        blocks.append(
            '<section class="report-card summary"><h2>Краткая сводка</h2><div class="summary-grid">'
            f'<div><span>Рост</span><strong>{_html(summary.get("height_cm") or "—")} см</strong></div>'
            f'<div><span>Последний вес</span><strong>{_html(weight.get("latest_kg") or "—")} кг</strong></div>'
            f'<div><span>Последнее давление</span><strong>{_html(pressure.get("latest_systolic") or "—")} / {_html(pressure.get("latest_diastolic") or "—")}</strong></div>'
            '</div></section>'
        )
    if isinstance(sections.get("weight"), dict):
        chart("chart-weight", "Вес", "кг")
    if isinstance(sections.get("circumference"), dict):
        chart("chart-circumference", "Обхваты тела", "см")
        rows = list(sections["circumference"].get("points") or [])
        blocks.append('<section class="report-card"><h2>Дневные обхваты</h2>' + _html_table(
            ["Дата", "Талия, см", "Бёдра, см"],
            [[row.get("measured_on"), row.get("waist_cm"), row.get("hip_cm")] for row in rows],
        ) + '</section>')
    if isinstance(sections.get("pressure"), dict):
        chart("chart-pressure", "Давление", "мм рт. ст.")
    if isinstance(sections.get("activity"), dict):
        chart("chart-activity", "Шаги · только Xiaomi Cloud", "шаги")
    if isinstance(sections.get("recovery"), dict):
        chart("chart-recovery", "Продолжительность сна", "часы")
        recovery_daily = list(sections["recovery"].get("daily") or [])
        if any(
            isinstance(row, dict)
            and any(
                row.get(key) is not None
                for key in ("minimum_heart_rate_bpm", "average_heart_rate_bpm", "maximum_heart_rate_bpm")
            )
            for row in recovery_daily
        ):
            chart("chart-watch-heart-rate", "Пульс с часов", "уд/мин")
        else:
            blocks.append('<section class="report-card"><h2>Пульс с часов</h2><p class="muted">Нет данных с часов за выбранный период.</p></section>')
    labs = sections.get("labs")
    if isinstance(labs, list):
        blocks.append('<section class="report-card"><h2>Лабораторные результаты</h2>' + (
            _html_table(
                ["Дата", "Показатель", "Значение", "Референс", "Статус", "Проверка"],
                [[item.get("observed_on"), item.get("analyte"), item.get("value"), item.get("reference"), lab_status_labels.get(str(item.get("status")), "Без оценки"), "Не проверено" if item.get("verification_status") == "unverified" else "Исправлено" if item.get("verification_status") == "corrected" else "Проверено"] for item in labs if isinstance(item, dict)],
            ) if labs else '<p class="muted">Нет результатов за выбранный период.</p>'
        ) + '</section>')
        if int(meta.get("labs_unverified_count") or 0) > 0:
            blocks.append(
                '<aside class="report-card report-warning"><strong>Есть неподтверждённые результаты</strong>'
                f'<p>{int(meta.get("labs_unverified_count") or 0)} строк отмечены как «Не проверено». '
                'Сверьте их с бланком; статус сохранён в таблице.</p></aside>'
            )
    studies = sections.get("studies")
    if isinstance(studies, list):
        study_rows = []
        for item in studies:
            if not isinstance(item, dict):
                continue
            details = " ".join([*(str(value) for value in item.get("findings") or []), str(item.get("conclusion") or "")]).strip()
            study_rows.append([item.get("observed_on"), modality_labels.get(str(item.get("modality")), "Исследование"), details])
        blocks.append('<section class="report-card"><h2>Подтверждённые исследования</h2>' + (
            _html_table(["Дата", "Тип", "Описание"], study_rows) if study_rows else '<p class="muted">Нет исследований за выбранный период.</p>'
        ) + '</section>')
    ai = sections.get("ai")
    if isinstance(ai, list):
        blocks.append('<section class="report-card"><h2>Валидированные AI-рекомендации</h2>' + (
            "".join(f'<article class="recommendation"><strong>{_html(item.get("title"))}</strong><p>{_html(item.get("text"))}</p></article>' for item in ai if isinstance(item, dict))
            if ai else '<p class="muted">Готовых рекомендаций нет.</p>'
        ) + '</section>')

    json_payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).replace("</", "<\\/")
    chart_script = f'''<script>{runtime}</script><script>
(function() {{
  const data = {json_payload};
  const palette = {{ green: "#2d9365", greenDeep: "#1c6f4a", blue: "#4b7bec", violet: "#8068dd", coral: "#dd755e", muted: "#657168", grid: "rgba(128,145,134,.16)" }};
  const charts = [];
  const rows = (section, key) => Array.isArray(section?.[key]) ? section[key] : [];
  const line = (name, values, color, extra) => Object.assign({{ name, type: "line", data: values, showSymbol: values.length < 40, connectNulls: false, smooth: .2, lineStyle: {{ width: 3, color }}, itemStyle: {{ color }} }}, extra || {{}});
  const base = (unit) => ({{ animation: false, grid: {{ left: 58, right: 20, top: 42, bottom: 48, containLabel: true }}, tooltip: {{ trigger: "axis", confine: true }}, legend: {{ top: 8, left: 0 }}, xAxis: {{ type: "time", axisLabel: {{ color: palette.muted }}, axisLine: {{ lineStyle: {{ color: palette.grid }} }}, splitLine: {{ show: false }} }}, yAxis: {{ type: "value", scale: true, name: unit, nameTextStyle: {{ color: palette.muted }}, axisLabel: {{ color: palette.muted }}, splitLine: {{ lineStyle: {{ color: palette.grid }} }} }}, dataZoom: [{{ type: "inside", filterMode: "none" }}] }});
  const render = (id, option) => {{ const node = document.getElementById(id); if (!node) return; const chart = echarts.init(node, null, {{ renderer: "svg" }}); chart.setOption(option); charts.push(chart); }};
  const pointData = (items, dateKey, valueKey) => items.map(item => [item[dateKey], item[valueKey] == null ? null : Number(item[valueKey])]);
  const weight = data.sections?.weight; if (weight) {{ const actual = Array.isArray(weight.raw) ? weight.raw : []; const o = base("кг"); o.series = [line("Реальные измерения", pointData(actual, "measured_at", "value"), palette.green, {{ showSymbol: true, smooth: false }})]; render("chart-weight", o); }}
  const circumference = data.sections?.circumference; if (circumference) {{ const o = base("см"); o.series = [line("Талия", pointData(rows(circumference, "points"), "measured_on", "waist_cm"), palette.coral), line("Бёдра", pointData(rows(circumference, "points"), "measured_on", "hip_cm"), palette.violet)]; render("chart-circumference", o); }}
  const pressure = data.sections?.pressure; if (pressure) {{ const o = base("мм рт. ст."); o.series = [line("Систолическое", pointData(rows(pressure, "points"), "measured_at", "systolic"), palette.coral), line("Диастолическое", pointData(rows(pressure, "points"), "measured_at", "diastolic"), palette.blue)]; render("chart-pressure", o); }}
  const activity = data.sections?.activity; if (activity) {{ const o = base("шаги"); o.series = [{{ name: "Шаги", type: "bar", data: pointData(rows(activity, "daily"), "date", "steps"), itemStyle: {{ color: palette.green }}, barMaxWidth: 18 }}]; render("chart-activity", o); }}
  const recovery = data.sections?.recovery; if (recovery) {{ const values = rows(recovery, "daily").map(item => [item.date, item.sleep_minutes == null ? null : Number(item.sleep_minutes) / 60]); const o = base("часы"); o.series = [{{ name: "Сон", type: "bar", data: values, itemStyle: {{ color: palette.violet }}, barMaxWidth: 18 }}]; render("chart-recovery", o); }}
  if (recovery) {{ const daily = rows(recovery, "daily"); const o = base("уд/мин"); o.series = [line("Минимум", pointData(daily, "date", "minimum_heart_rate_bpm"), palette.blue, {{ smooth: false, lineStyle: {{ width: 1.5, type: "dashed", color: palette.blue }} }}), line("Средний", pointData(daily, "date", "average_heart_rate_bpm"), palette.coral, {{ smooth: false }}), line("Максимум", pointData(daily, "date", "maximum_heart_rate_bpm"), palette.violet, {{ smooth: false, lineStyle: {{ width: 1.5, type: "dashed", color: palette.violet }} }})]; render("chart-watch-heart-rate", o); }}
  window.addEventListener("beforeprint", () => charts.forEach(chart => chart.resize()));
}})();
</script>'''
    document = f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Amigo — пакет для врача</title><style>
:root {{ color-scheme: light; font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #18221c; background: #f4f6f1; }} * {{ box-sizing: border-box; }} body {{ margin: 0; background: #f4f6f1; color: #18221c; }} .report {{ width: min(100% - 32px, 1040px); margin: 0 auto; padding: 34px 0 48px; }} .report-header {{ padding: 28px 30px; border-radius: 20px; background: linear-gradient(130deg,#1c6f4a,#358c66); color: #fff; margin-bottom: 18px; }} .kicker {{ margin: 0 0 8px; font-size: 11px; letter-spacing: .15em; text-transform: uppercase; opacity: .78; }} h1 {{ margin: 0; font-size: clamp(28px,4vw,44px); letter-spacing: -.04em; }} .period {{ margin: 12px 0 0; font-size: 14px; opacity: .9; }} .notice {{ margin: 12px 0 0; font-size: 11px; opacity: .78; }} .report-card {{ margin: 18px 0; padding: 22px 24px; border: 1px solid #dbe4dc; border-radius: 16px; background: #fff; break-inside: avoid; }} .report-card h2 {{ margin: 0 0 16px; font-size: 19px; letter-spacing: -.02em; }} .report-card__head {{ display:flex; justify-content:space-between; align-items:baseline; gap:12px; }} .unit,.muted {{ color:#657168; font-size:12px; }} .summary-grid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:10px; }} .summary-grid div {{ padding:14px; border-radius:12px; background:#f0f4ef; }} .summary-grid span {{ display:block; color:#657168; font-size:11px; }} .summary-grid strong {{ display:block; margin-top:5px; font-size:20px; }} .echart {{ width:100%; height:280px; }} .table-wrap {{ overflow-x:auto; }} table {{ width:100%; border-collapse:collapse; font-size:12px; }} th,td {{ padding:9px 8px; border-bottom:1px solid #e7ede8; text-align:left; vertical-align:top; }} th {{ color:#657168; font-size:10px; text-transform:uppercase; letter-spacing:.06em; }} .recommendation {{ padding:12px 0; border-top:1px solid #e7ede8; }} .recommendation:first-of-type {{ border-top:0; }} .recommendation p {{ margin:6px 0 0; color:#46564b; line-height:1.55; }} .report-warning {{ border-color:#ead7a8; background:#fff8e8; }} .report-warning strong {{ font-size:13px; }} .report-warning p {{ margin:6px 0 0; color:#6f634a; font-size:12px; }} .report-footer {{ margin-top:22px; color:#657168; font-size:10px; }} @page {{ size:A4; margin:14mm; }} @media print {{ body {{ background:#fff; }} .report {{ width:100%; padding:0; }} .report-header {{ color-adjust:exact; -webkit-print-color-adjust:exact; print-color-adjust:exact; }} }} @media(max-width:620px) {{ .report {{ width:min(100% - 20px,1040px); padding-top:16px; }} .report-header,.report-card {{ padding:18px; border-radius:14px; }} .summary-grid {{ grid-template-columns:1fr; }} }}
</style></head><body><main class="report"><header class="report-header"><p class="kicker">Amigo · пакет для врача</p><h1>Сводка здоровья</h1><p class="period">Период: {_html(meta.get('from','—'))} — {_html(meta.get('to','—'))} · {_html(meta.get('timezone','Europe/Moscow'))}</p><p class="notice">Информационная сводка измерений; не диагноз и не назначение лечения.</p></header>{"".join(blocks)}<footer class="report-footer">Сформировано {_html(meta.get('created_at','—'))}. Данные зафиксированы на момент формирования пакета.</footer></main>{chart_script}</body></html>'''
    encoded = document.encode("utf-8")
    if len(encoded) > HTML_MAX_BYTES:
        raise HTTPException(status_code=422, detail="report_html_too_large")
    return encoded


def render_doctor_report_html(payload: dict, static_dir: Path | None = None) -> bytes:
    """Render a self-contained, print-oriented dashboard from an immutable payload."""

    runtime = _echarts_runtime(static_dir)
    if runtime is not None:
        return _render_echarts_report_html(payload, runtime)

    meta = payload.get("meta") or {}
    sections = payload.get("sections") or {}
    lab_status_labels = {
        "within_reference": "В референсе",
        "below_reference": "Ниже референса",
        "above_reference": "Выше референса",
        "outside_reference": "Вне референса",
        "indeterminate": "Без оценки",
    }
    modality_labels = {
        "ultrasound": "УЗИ",
        "mri": "МРТ",
        "ct": "КТ",
        "xray": "Рентген",
        "ecg": "ЭКГ",
        "other": "Исследование",
    }
    blocks: list[str] = []
    summary = sections.get("summary")
    if isinstance(summary, dict):
        weight = summary.get("weight") or {}
        pressure = summary.get("pressure") or {}
        cards = [
            ("Рост", f"{_html(summary.get('height_cm') or '—')} см"),
            ("Последний вес", f"{_html(weight.get('latest_kg') or '—')} кг"),
            ("Последнее давление", f"{_html(pressure.get('latest_systolic') or '—')} / {_html(pressure.get('latest_diastolic') or '—')}"),
        ]
        blocks.append(
            '<section class="report-card summary"><h2>Краткая сводка</h2><div class="summary-grid">'
            + "".join(f'<div><span>{_html(label)}</span><strong>{value}</strong></div>' for label, value in cards)
            + "</div></section>"
        )

    weight = sections.get("weight")
    if isinstance(weight, dict):
        raw = list(weight.get("raw") or [])
        blocks.append(_svg_chart("Вес · реальные измерения", raw, [("value", "Реальные измерения", "#2d9365")], unit="кг"))
    circumference = sections.get("circumference")
    if isinstance(circumference, dict):
        points = list(circumference.get("points") or [])
        blocks.append(_svg_chart("Обхваты тела", points, [("waist_cm", "Талия", "#dd755e"), ("hip_cm", "Бёдра", "#8068dd")], date_key="measured_on", unit="см"))
        blocks.append('<section class="report-card"><h2>Дневные обхваты</h2>' + _html_table(
            ["Дата", "Талия, см", "Бёдра, см"],
            [[row.get("measured_on"), row.get("waist_cm"), row.get("hip_cm")] for row in points],
        ) + "</section>")
    pressure = sections.get("pressure")
    if isinstance(pressure, dict):
        blocks.append(_svg_chart("Давление", list(pressure.get("points") or []), [("systolic", "Систолическое", "#dd755e"), ("diastolic", "Диастолическое", "#4b7bec")], unit="мм рт. ст."))
    activity = sections.get("activity")
    if isinstance(activity, dict):
        blocks.append(_svg_chart("Шаги · только Xiaomi Cloud", list(activity.get("daily") or []), [("steps", "Шаги", "#2d9365")], date_key="date", unit="шаги"))
    recovery = sections.get("recovery")
    if isinstance(recovery, dict):
        sleep_rows = [row for row in list(recovery.get("daily") or []) if isinstance(row, dict)]
        sleep_points = [dict(row, sleep_hours=(row.get("sleep_minutes") / 60 if isinstance(row.get("sleep_minutes"), (int, float)) else None)) for row in sleep_rows]
        blocks.append(_svg_chart("Продолжительность сна", sleep_points, [("sleep_hours", "Сон", "#8068dd")], date_key="date", unit="часы"))
        blocks.append(_svg_chart(
            "Пульс с часов",
            sleep_rows,
            [
                ("minimum_heart_rate_bpm", "Минимум", "#4b7bec"),
                ("average_heart_rate_bpm", "Средний", "#dd755e"),
                ("maximum_heart_rate_bpm", "Максимум", "#8068dd"),
            ],
            date_key="date",
            unit="уд/мин",
        ))
    labs = sections.get("labs")
    if isinstance(labs, list):
        blocks.append('<section class="report-card"><h2>Лабораторные результаты</h2>' + (
            _html_table(
                ["Дата", "Показатель", "Значение", "Референс", "Статус", "Проверка"],
                [[item.get("observed_on"), item.get("analyte"), item.get("value"), item.get("reference"), lab_status_labels.get(str(item.get("status")), "Без оценки"), "Не проверено" if item.get("verification_status") == "unverified" else "Исправлено" if item.get("verification_status") == "corrected" else "Проверено"] for item in labs if isinstance(item, dict)],
            ) if labs else '<p class="muted">Нет результатов за выбранный период.</p>'
        ) + "</section>")
        if int(meta.get("labs_unverified_count") or 0) > 0:
            blocks.append(
                '<aside class="report-card report-warning"><strong>Есть неподтверждённые результаты</strong>'
                f'<p>{int(meta.get("labs_unverified_count") or 0)} строк отмечены как «Не проверено». '
                'Сверьте их с бланком; статус сохранён в таблице.</p></aside>'
            )
    studies = sections.get("studies")
    if isinstance(studies, list):
        study_rows = []
        for item in studies:
            if not isinstance(item, dict):
                continue
            details = " ".join([*(str(value) for value in item.get("findings") or []), str(item.get("conclusion") or "")]).strip()
            study_rows.append([item.get("observed_on"), modality_labels.get(str(item.get("modality")), "Исследование"), details])
        blocks.append('<section class="report-card"><h2>Подтверждённые исследования</h2>' + (
            _html_table(["Дата", "Тип", "Описание"], study_rows) if study_rows else '<p class="muted">Нет исследований за выбранный период.</p>'
        ) + "</section>")
    ai = sections.get("ai")
    if isinstance(ai, list):
        blocks.append('<section class="report-card"><h2>Валидированные AI-рекомендации</h2>' + (
            "".join(f'<article class="recommendation"><strong>{_html(item.get("title"))}</strong><p>{_html(item.get("text"))}</p></article>' for item in ai if isinstance(item, dict))
            if ai else '<p class="muted">Готовых рекомендаций нет.</p>'
        ) + "</section>")

    document = f'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Amigo — пакет для врача</title>
<style>
:root {{ color-scheme: light; font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #18221c; background: #f4f6f1; }}
* {{ box-sizing: border-box; }} body {{ margin: 0; background: #f4f6f1; color: #18221c; }}
.report {{ width: min(100% - 32px, 1040px); margin: 0 auto; padding: 34px 0 48px; }}
.report-header {{ padding: 28px 30px; border-radius: 20px; background: linear-gradient(130deg,#1c6f4a,#358c66); color: #fff; margin-bottom: 18px; }}
.kicker {{ margin: 0 0 8px; font-size: 11px; letter-spacing: .15em; text-transform: uppercase; opacity: .78; }}
h1 {{ margin: 0; font-size: clamp(28px,4vw,44px); letter-spacing: -.04em; }} .period {{ margin: 12px 0 0; font-size: 14px; opacity: .9; }}
.notice {{ margin: 12px 0 0; font-size: 11px; opacity: .78; }} .report-card {{ margin: 18px 0; padding: 22px 24px; border: 1px solid #dbe4dc; border-radius: 16px; background: #fff; break-inside: avoid; }}
.report-card h2 {{ margin: 0 0 16px; font-size: 19px; letter-spacing: -.02em; }} .report-card__head {{ display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }}
.unit, .muted {{ color: #657168; font-size: 12px; }} .summary-grid {{ display: grid; grid-template-columns: repeat(3,1fr); gap: 10px; }}
.summary-grid div {{ padding: 14px; border-radius: 12px; background: #f0f4ef; }} .summary-grid span {{ display: block; color: #657168; font-size: 11px; }} .summary-grid strong {{ display: block; margin-top: 5px; font-size: 20px; }}
.report-chart svg {{ display: block; width: 100%; height: auto; margin-top: 6px; }} .plot {{ fill: #fbfdfb; stroke: #dbe4dc; }} .grid {{ stroke: #e7ede8; stroke-width: 1; }} .axis {{ fill: #657168; font-size: 11px; }}
.legend {{ display: flex; flex-wrap: wrap; gap: 14px; margin-top: 5px; color: #657168; font-size: 11px; }} .legend span {{ display: inline-flex; align-items: center; gap: 6px; }} .legend i {{ width: 9px; height: 9px; display: inline-block; border-radius: 50%; }}
.table-wrap {{ overflow-x: auto; }} table {{ width: 100%; border-collapse: collapse; font-size: 12px; }} th, td {{ padding: 9px 8px; border-bottom: 1px solid #e7ede8; text-align: left; vertical-align: top; }} th {{ color: #657168; font-size: 10px; text-transform: uppercase; letter-spacing: .06em; }}
.recommendation {{ padding: 12px 0; border-top: 1px solid #e7ede8; }} .recommendation:first-of-type {{ border-top: 0; }} .recommendation p {{ margin: 6px 0 0; color: #46564b; line-height: 1.55; }}
.report-warning {{ border-color: #ead7a8; background: #fff8e8; }} .report-warning strong {{ font-size: 13px; }} .report-warning p {{ margin: 6px 0 0; color: #6f634a; font-size: 12px; }}
.report-footer {{ margin-top: 22px; color: #657168; font-size: 10px; }}
@page {{ size: A4; margin: 14mm; }} @media print {{ body {{ background: #fff; }} .report {{ width: 100%; padding: 0; }} .report-header {{ color-adjust: exact; -webkit-print-color-adjust: exact; print-color-adjust: exact; }} .report-card {{ box-shadow: none; }} }}
@media (max-width: 620px) {{ .report {{ width: min(100% - 20px, 1040px); padding-top: 16px; }} .report-header, .report-card {{ padding: 18px; border-radius: 14px; }} .summary-grid {{ grid-template-columns: 1fr; }} }}
</style></head><body><main class="report">
<header class="report-header"><p class="kicker">Amigo · пакет для врача</p><h1>Сводка здоровья</h1><p class="period">Период: {_html(meta.get('from', '—'))} — {_html(meta.get('to', '—'))} · {_html(meta.get('timezone', 'Europe/Moscow'))}</p><p class="notice">Информационная сводка измерений; не диагноз и не назначение лечения.</p></header>
{"".join(blocks)}<footer class="report-footer">Сформировано {_html(meta.get('created_at', '—'))}. Данные зафиксированы на момент формирования пакета.</footer>
</main></body></html>'''
    encoded = document.encode("utf-8")
    if len(encoded) > HTML_MAX_BYTES:
        raise HTTPException(status_code=422, detail="report_html_too_large")
    return encoded


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
        raw = list(weight.get("raw") or [])
        writer.chart("Вес · реальные измерения", raw, "value", unit="кг")
    circumference = sections.get("circumference")
    if isinstance(circumference, dict):
        points = list(circumference.get("points") or [])
        writer.chart("Обхват талии", points, "waist_cm", unit="см")
        writer.chart("Обхват бёдер", points, "hip_cm", unit="см")
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
        writer.chart(
            "Пульс с часов · среднее за день",
            list(recovery.get("daily") or []),
            "average_heart_rate_bpm",
            unit="уд/мин",
        )
    labs = sections.get("labs")
    if isinstance(labs, list):
        writer.heading("Лабораторные результаты")
        if not labs:
            writer.text("Нет результатов за выбранный период.")
        for item in labs:
            if not isinstance(item, dict):
                continue
            reference = f" · референс {item['reference']}" if item.get("reference") else ""
            verification = (
                "Не проверено"
                if item.get("verification_status") == "unverified"
                else "Исправлено"
                if item.get("verification_status") == "corrected"
                else "Проверено"
            )
            writer.text(
                f"{item.get('observed_on') or 'Дата не указана'} · {item.get('analyte')}: "
                f"{item.get('value')}{reference} · {item.get('status')} · {verification}",
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
        "html_size_bytes": row.html_size_bytes,
        "created_at": row.created_at,
        "expires_at": row.expires_at,
        "download_url": f"/amigo/api/v1/reports/doctor/{row.id}.pdf",
        "html_download_url": f"/amigo/api/v1/reports/doctor/{row.id}.html",
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
    rendered_html = render_doctor_report_html(payload, settings.static_dir)
    # Page count is validated from the exact same immutable view model used by GET.
    with fitz.open(stream=rendered, filetype="pdf") as document:
        page_count = document.page_count
    row = DoctorReportSnapshot(
        id=str(uuid4()),
        options=request.model_dump(mode="json"),
        payload=payload,
        page_count=page_count,
        size_bytes=len(rendered),
        html_size_bytes=len(rendered_html),
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


@router.get("/{report_id}.html")
def download_doctor_report_html(
    report_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    row = _get_snapshot(db, report_id, datetime.now(timezone.utc))
    rendered = render_doctor_report_html(row.payload, settings.static_dir)
    if len(rendered) > HTML_MAX_BYTES:
        raise HTTPException(status_code=422, detail="report_html_too_large")
    return Response(
        rendered,
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="amigo-doctor-report.html"',
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
