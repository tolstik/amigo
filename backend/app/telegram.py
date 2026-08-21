from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html import escape
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select
from sqlalchemy.orm import Session

from .analytics import plan_weight, pressure_sessions, trend_change
from .ai_queue import public_analysis_payload
from .config import Settings
from .health_analytics import activity_series, recovery_series
from .lab_models import LabResult
from .models import JobRun, Measurement, MeasurementGroup, Outbox, utcnow
from .service import (
    active_plan,
    composition_series,
    overview,
    pressure_readings,
    pressure_series,
    weight_daily,
)


class TelegramError(RuntimeError):
    pass


HEALTH_SUMMARY_STALE_DAYS = 2


def _escape_limited(value: str, limit: int) -> str:
    """Escape generated text without cutting an HTML entity in half."""

    escaped = escape(value)
    if len(escaped) <= limit:
        return escaped
    suffix = "…"
    low, high = 0, len(value)
    while low < high:
        middle = (low + high + 1) // 2
        if len(escape(value[:middle].rstrip())) + len(suffix) <= limit:
            low = middle
        else:
            high = middle - 1
    return f"{escape(value[:low].rstrip())}{suffix}" if low else ""


@dataclass(frozen=True)
class DeliveryResult:
    advice_run_keys: tuple[str, ...] = ()


def _font(size: int, bold: bool = False):
    filename = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    path = Path("/usr/share/fonts/truetype/dejavu") / filename
    try:
        return ImageFont.truetype(str(path), size=size)
    except OSError:
        return ImageFont.load_default()


def render_weekly_card(
    db: Session,
    settings: Settings,
    now: datetime | None = None,
    week_ending: date | None = None,
) -> bytes:
    current = now or datetime.now(timezone.utc)
    plan = active_plan(db)
    local_today = current.astimezone(settings.tz).date()
    report_end = week_ending or local_today
    report_start = report_end - timedelta(days=6)
    chart_start = max(plan.start_date, report_end - timedelta(days=89))
    recent = [
        point
        for point in weight_daily(db, settings.tz, chart_start)
        if point.day <= report_end
    ]
    composition_points = [
        point
        for point in composition_series(db, settings.tz, "all", current)["points"]
        if datetime.fromisoformat(str(point["measured_at"]).replace("Z", "+00:00"))
        .astimezone(settings.tz)
        .date()
        <= report_end
    ]
    composition = composition_points[-1] if composition_points else {}
    canvas = Image.new("RGB", (1200, 700), "#101827")
    draw = ImageDraw.Draw(canvas)
    draw.text((64, 42), "AMIGO · ИТОГИ НЕДЕЛИ", fill="#F8FAFC", font=_font(42, True))
    draw.text(
        (66, 100),
        f"{report_start:%d.%m}–{report_end:%d.%m.%Y}",
        fill="#94A3B8",
        font=_font(24),
    )
    plot = (70, 175, 1130, 590)
    draw.rounded_rectangle(plot, radius=24, fill="#182235", outline="#273449", width=2)
    if not recent:
        draw.text((390, 350), "НЕТ ДАННЫХ О ВЕСЕ", fill="#94A3B8", font=_font(34, True))
    else:
        actual = [point.rolling_7d or point.value for point in recent]
        planned = [plan_weight(point.day, plan) for point in recent]
        values = actual + [value for value in planned if value is not None]
        low, high = min(values), max(values)
        margin = max(1.0, (high - low) * 0.15)
        low, high = low - margin, high + margin
        start_day, end_day = recent[0].day, recent[-1].day
        day_span = max(1, (end_day - start_day).days)

        def xy(day, value):
            x = plot[0] + 35 + (day - start_day).days / day_span * (plot[2] - plot[0] - 70)
            y = plot[3] - 35 - (value - low) / (high - low) * (plot[3] - plot[1] - 70)
            return (round(x), round(y))

        for part in range(5):
            y = plot[1] + 35 + part * (plot[3] - plot[1] - 70) / 4
            draw.line((plot[0] + 35, y, plot[2] - 35, y), fill="#28364D", width=1)
        actual_points = [xy(point.day, value) for point, value in zip(recent, actual, strict=True)]
        plan_points = [
            xy(point.day, value)
            for point, value in zip(recent, planned, strict=True)
            if value is not None
        ]
        if len(plan_points) > 1:
            draw.line(plan_points, fill="#F59E0B", width=4)
        if len(actual_points) > 1:
            draw.line(actual_points, fill="#38BDF8", width=6, joint="curve")
        for point in actual_points[-1:]:
            draw.ellipse((point[0] - 8, point[1] - 8, point[0] + 8, point[1] + 8), fill="#E0F2FE")
        latest = actual[-1]
        draw.text((82, 610), f"ТРЕНД  {latest:.1f} КГ", fill="#38BDF8", font=_font(28, True))
        target = planned[-1]
        if target is not None:
            draw.text((810, 610), f"ПЛАН  {target:.1f} КГ", fill="#F59E0B", font=_font(28, True))
    composition_bits = []
    if composition.get("fat_pct") is not None:
        composition_bits.append(f"жир {composition['fat_pct']:.1f}%")
    if composition.get("fat_mass_kg") is not None:
        composition_bits.append(f"жировая масса {composition['fat_mass_kg']:.1f} кг")
    if composition.get("lean_mass_kg") is not None:
        composition_bits.append(f"безжировая масса {composition['lean_mass_kg']:.1f} кг")
    footer = "BIA-оценка: " + " · ".join(composition_bits) if composition_bits else "Состав тела: нет данных"
    draw.text((70, 658), footer, fill="#94A3B8", font=_font(18))
    output = BytesIO()
    canvas.save(output, format="PNG")
    return output.getvalue()


class TelegramClient:
    def __init__(self, settings: Settings, http: httpx.Client | None = None):
        if not settings.telegram_bot_token or not settings.telegram_chat_id:
            raise TelegramError("Telegram is not configured")
        self.settings = settings
        self.http = http or httpx.Client(timeout=httpx.Timeout(30, connect=10))
        self._owns_http = http is None
        self.base = f"{settings.telegram_api_url.rstrip('/')}/bot{settings.telegram_bot_token}"

    def close(self) -> None:
        if self._owns_http:
            self.http.close()

    def _check(self, response: httpx.Response) -> None:
        try:
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise TelegramError("Telegram request failed") from exc
        if not isinstance(payload, dict) or not payload.get("ok"):
            raise TelegramError("Telegram rejected the message")

    def send_message(self, text: str) -> None:
        if len(text) > 3_900:
            raise TelegramError("Telegram message exceeds the safe HTML length")
        response = self.http.post(
            f"{self.base}/sendMessage",
            data={
                "chat_id": self.settings.telegram_chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            },
        )
        self._check(response)

    def send_photo(self, image: bytes, caption: str) -> None:
        response = self.http.post(
            f"{self.base}/sendPhoto",
            data={
                "chat_id": self.settings.telegram_chat_id,
                "caption": caption[:1024],
                "parse_mode": "HTML",
            },
            files={"photo": ("amigo-weekly.png", image, "image/png")},
        )
        self._check(response)


class TelegramNotifier:
    def __init__(self, db: Session, settings: Settings, client: TelegramClient | None = None):
        self.db = db
        self.settings = settings
        self.client = client or TelegramClient(settings)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _ai_lines(self, limit: int = 4, max_chars: int = 2_400) -> list[str]:
        payload = public_analysis_payload(self.db)
        if payload.get("status") != "ready" or not isinstance(payload.get("analysis"), dict):
            return []
        analysis = payload["analysis"]
        lines: list[str] = []
        headline = analysis.get("headline")
        summary = analysis.get("summary")
        if isinstance(headline, str):
            safe_headline = _escape_limited(headline, 220)
            if safe_headline:
                lines.append(f"<b>✨ {safe_headline}</b>")
        if isinstance(summary, str):
            safe_summary = _escape_limited(summary, 620)
            if safe_summary:
                lines.append(safe_summary)
        candidates = [
            *(analysis.get("recommendations") if isinstance(analysis.get("recommendations"), list) else []),
            *(analysis.get("observations") if isinstance(analysis.get("observations"), list) else []),
        ]
        for item in candidates[:limit]:
            if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                continue
            title = item.get("title") if isinstance(item.get("title"), str) else "ИИ-анализ"
            safe_title = _escape_limited(title, 180)
            used = sum(len(line) + 1 for line in lines)
            prefix = f"• <b>{safe_title}</b>: "
            available = min(520, max_chars - used - len(prefix) - 1)
            if available < 40:
                break
            safe_text = _escape_limited(item["text"], available)
            if safe_text:
                lines.append(f"{prefix}{safe_text}")
        return lines

    @staticmethod
    def _pack_messages(header: str, lines: list[str], limit: int = 3_800) -> list[str]:
        if not lines:
            return []
        messages: list[str] = []
        current = header
        for line in lines:
            candidate = f"{current}\n{line}"
            if len(candidate) > limit and current != header:
                messages.append(current)
                current = f"{header}\n{line}"
            else:
                current = candidate
        messages.append(current)
        return messages

    def _ai_recommendation_messages(self) -> list[str]:
        payload = public_analysis_payload(self.db)
        if payload.get("status") != "ready" or not isinstance(payload.get("analysis"), dict):
            return []
        recommendations = payload["analysis"].get("recommendations")
        if not isinstance(recommendations, list):
            return []
        lines = []
        for item in recommendations:
            if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                continue
            evidence_keys = item.get("evidence_keys")
            if isinstance(evidence_keys, list) and any(
                isinstance(key, str) and key.startswith("lab.") for key in evidence_keys
            ):
                continue
            title = item.get("title") if isinstance(item.get("title"), str) else "Рекомендация"
            lines.append(f"• <b>{escape(title)}</b>: {escape(item['text'])}")
        return self._pack_messages("<b>✨ Рекомендации Amigo</b>", lines)

    def _lab_assessment_messages(self, now: datetime) -> list[str]:
        since = now - timedelta(hours=24)
        recent_keys = {
            f"lab.{sha256(row_id.encode()).hexdigest()[:20]}"
            for row_id in self.db.scalars(
                select(LabResult.id).where(
                    LabResult.deleted.is_(False),
                    LabResult.created_at >= since,
                )
            )
        }
        if not recent_keys:
            return []
        payload = public_analysis_payload(self.db)
        if payload.get("status") != "ready" or not isinstance(payload.get("analysis"), dict):
            return []
        analysis = payload["analysis"]
        candidates = [
            *(analysis.get("recommendations") if isinstance(analysis.get("recommendations"), list) else []),
            *(analysis.get("observations") if isinstance(analysis.get("observations"), list) else []),
        ]
        lines: list[str] = []
        for item in candidates:
            if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                continue
            evidence_keys = item.get("evidence_keys")
            if not isinstance(evidence_keys, list) or not recent_keys.intersection(evidence_keys):
                continue
            title = item.get("title") if isinstance(item.get("title"), str) else "Оценка"
            safe_title = _escape_limited(title, 180)
            safe_text = _escape_limited(item["text"], 620)
            if safe_title and safe_text:
                lines.append(f"• <b>{safe_title}</b>: {safe_text}")
        return self._pack_messages("<b>🩺 Оценка лабораторных результатов</b>", lines)

    def _lab_messages(self, now: datetime) -> list[str]:
        since = now - timedelta(hours=24)
        rows = list(
            self.db.scalars(
                select(LabResult)
                .where(LabResult.deleted.is_(False), LabResult.created_at >= since)
                .order_by(LabResult.observed_on, LabResult.created_at, LabResult.source_index)
            )
        )
        labels = {
            "within_reference": "в референсе",
            "below_reference": "ниже референса",
            "above_reference": "выше референса",
            "outside_reference": "вне референса",
            "indeterminate": "без оценки",
        }
        lines: list[str] = []
        for row in rows:
            value = str(row.value_numeric) if row.value_numeric is not None else (row.value_text or "—")
            if row.comparator and row.comparator != "=":
                value = f"{row.comparator}{value}"
            if row.unit:
                value = f"{value} {row.unit}"
            if row.reference_text:
                reference = row.reference_text
            elif row.reference_low is not None and row.reference_high is not None:
                reference = f"{row.reference_low}–{row.reference_high} {row.unit or ''}".strip()
            elif row.reference_low is not None:
                reference = f"от {row.reference_low} {row.unit or ''}".strip()
            elif row.reference_high is not None:
                reference = f"до {row.reference_high} {row.unit or ''}".strip()
            else:
                reference = "не указан"
            verification = "проверено" if row.verification_status == "verified" else "НЕ ПРОВЕРЕНО"
            lines.append(
                f"• <b>{escape(row.analyte_name)}</b>: {escape(value)} · "
                f"референс {escape(reference)} · {escape(labels.get(row.status, 'без оценки'))} · {verification}"
            )
        return self._pack_messages("<b>🧪 Новые лабораторные результаты</b>", lines)

    def deliver(self, event: Outbox, now: datetime | None = None) -> DeliveryResult:
        current = now or datetime.now(timezone.utc)
        if event.event_type == "measurement.weight":
            text, keys = self._weight_text(event, current)
            self.client.send_message(text)
            return DeliveryResult(keys)
        if event.event_type == "measurement.pressure":
            self.client.send_message(self._pressure_text(event, current))
            return DeliveryResult()
        if event.event_type == "weekly.digest":
            week_ending = self._week_ending(event, current)
            text = self._digest_text(current, week_ending=week_ending)
            self.client.send_photo(
                render_weekly_card(self.db, self.settings, current, week_ending),
                "<b>📊 Недельный график Amigo</b>",
            )
            self.client.send_message(text)
            for message in [
                *self._lab_messages(current),
                *self._lab_assessment_messages(current),
                *self._ai_recommendation_messages(),
            ]:
                self.client.send_message(message)
            return DeliveryResult()
        if event.event_type == "daily.digest":
            self.client.send_message(self._daily_digest_text(current))
            for message in [
                *self._lab_messages(current),
                *self._lab_assessment_messages(current),
                *self._ai_recommendation_messages(),
            ]:
                self.client.send_message(message)
            return DeliveryResult()
        raise TelegramError(f"unsupported outbox event {event.event_type}")

    def _week_ending(self, event: Outbox, now: datetime) -> date:
        raw = event.payload.get("week_ending")
        if isinstance(raw, str):
            try:
                parsed = date.fromisoformat(raw)
            except ValueError:
                parsed = None
            if parsed is not None:
                # Normalize legacy events, which stored the Monday report date,
                # to the preceding completed Sunday.
                return (
                    parsed
                    if parsed.weekday() == 6
                    else parsed - timedelta(days=parsed.weekday() + 1)
                )
        local_day = now.astimezone(self.settings.tz).date()
        return local_day - timedelta(days=local_day.weekday() + 1)

    def _group(self, event: Outbox) -> MeasurementGroup:
        provider = str(event.payload.get("provider", "withings"))
        provider_id = str(event.payload.get("provider_group_id", ""))
        group = self.db.scalar(
            select(MeasurementGroup).where(
                MeasurementGroup.provider == provider,
                MeasurementGroup.provider_group_id == provider_id,
            )
        )
        if group is None:
            raise TelegramError("measurement group for notification is missing")
        return group

    def _weight_text(self, event: Outbox, now: datetime) -> tuple[str, tuple[str, ...]]:
        group = self._group(event)
        values = {measurement.kind: float(measurement.value) for measurement in group.measurements}
        if "weight" not in values:
            raise TelegramError("weight notification has no weight")
        if not any(kind in values for kind in ("fat_percent", "fat_mass", "fat_free_mass")):
            nearby = self.db.execute(
                select(Measurement.kind, Measurement.value)
                .join(MeasurementGroup, Measurement.group_id == MeasurementGroup.id)
                .where(
                    Measurement.kind.in_(("fat_percent", "fat_mass", "fat_free_mass", "muscle_mass")),
                    MeasurementGroup.measured_at >= group.measured_at - timedelta(minutes=10),
                    MeasurementGroup.measured_at <= group.measured_at + timedelta(minutes=10),
                )
            )
            values.update({kind: float(value) for kind, value in nearby})
        day = group.measured_at.astimezone(self.settings.tz).date()
        plan = active_plan(self.db)
        daily = weight_daily(self.db, self.settings.tz, plan.start_date)
        current_index = next((i for i, item in enumerate(daily) if item.day == day), len(daily) - 1)
        point = daily[current_index] if daily else None
        previous = daily[current_index - 1] if current_index > 0 else None
        planned = plan_weight(day, plan)
        forecast = overview(self.db, self.settings.tz, now)["forecast"]
        lines = [f"<b>⚖️ Новый вес: {values['weight']:.2f} кг</b>"]
        if point and previous:
            lines.append(f"К предыдущему дню: {point.value - previous.value:+.2f} кг")
        if point and point.rolling_7d is not None:
            lines.append(f"Тренд за 7 дней: {point.rolling_7d:.2f} кг")
            lines.append(f"С 15.08.2026: {point.rolling_7d - plan.start_weight_kg:+.2f} кг")
        if planned is not None and point:
            lines.append(f"План: {planned:.2f} кг · отклонение {point.value - planned:+.2f} кг")
        tempo = trend_change(daily, 28)
        if tempo is not None:
            lines.append(f"Изменение за 28 дней: {tempo:+.2f} кг")
        if forecast["reliable"]:
            lines.append(f"Прогноз цели: {forecast['target_date']}")
        composition_labels = {
            "fat_percent": "Жир",
            "fat_mass": "Жировая масса",
            "fat_free_mass": "Безжировая масса",
            "muscle_mass": "Мышечная масса",
        }
        composition = [
            f"{label}: {values[kind]:.1f}{'%' if kind == 'fat_percent' else ' кг'}"
            for kind, label in composition_labels.items()
            if kind in values
        ]
        if composition:
            lines.extend(composition)
            lines.append("<i>Состав тела — приблизительная BIA-оценка.</i>")
        lines.extend(self._ai_lines(limit=2))
        lines.append(f'<a href="{escape(self.settings.public_url)}">Открыть Amigo</a>')
        return "\n".join(lines), ()

    def _pressure_text(self, event: Outbox, now: datetime) -> str:
        group = self._group(event)
        values = {measurement.kind: float(measurement.value) for measurement in group.measurements}
        if "systolic" not in values or "diastolic" not in values:
            raise TelegramError("pressure notification is incomplete")
        payload = pressure_series(self.db, self.settings.tz, "all", now)
        lines = [f"<b>🫀 Давление: {values['systolic']:.0f}/{values['diastolic']:.0f} мм рт. ст.</b>"]
        if "pulse" in values:
            lines.append(f"Пульс: {values['pulse']:.0f} уд/мин")
        for key, label in (("last_7_days", "7 дней"), ("last_30_days", "30 дней")):
            stat = payload["statistics"][key]
            if stat["systolic"] and stat["diastolic"]:
                lines.append(
                    f"Среднее за {label}: {stat['systolic']['mean']:.0f}/{stat['diastolic']['mean']:.0f}"
                )
        lines.append("<i>Только описательная статистика, без медицинской оценки.</i>")
        lines.append(f'<a href="{escape(self.settings.public_url)}">Открыть Amigo</a>')
        return "\n".join(lines)

    @staticmethod
    def _payload_date(value: Any) -> date | None:
        if not isinstance(value, str):
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None

    @staticmethod
    def _date_label(value: date | None) -> str:
        return value.strftime("%d.%m.%Y") if value is not None else "дата не указана"

    def _completed_week(self, payload: dict[str, Any], week_ending: date) -> dict[str, Any]:
        candidates = []
        for row in payload.get("weekly") or []:
            if not isinstance(row, dict):
                continue
            end = self._payload_date(row.get("end_date"))
            if end is not None and end <= week_ending:
                candidates.append((end, row))
        return max(candidates, key=lambda item: item[0])[1] if candidates else {}

    def _activity_recovery_lines(
        self,
        now: datetime,
        *,
        weekly: bool,
        week_ending: date | None = None,
    ) -> list[str]:
        activity = activity_series(self.db, self.settings.tz, "30d", now)
        recovery = recovery_series(self.db, self.settings.tz, "30d", now)
        lines: list[str] = []
        activity_summary = activity.get("summary") or {}
        recovery_summary = recovery.get("summary") or {}
        if weekly:
            completed_end = week_ending or (
                now.astimezone(self.settings.tz).date()
                - timedelta(days=now.astimezone(self.settings.tz).date().weekday() + 1)
            )
            completed_start = completed_end - timedelta(days=6)
            week_label = f"{completed_start:%d.%m}–{completed_end:%d.%m.%Y}"
            activity_week = self._completed_week(activity, completed_end)
            steps = activity_week.get("actual_steps")
            baseline = activity_week.get("baseline_steps")
            if steps is not None:
                line = f"Шаги за неделю {week_label}: {float(steps):,.0f}".replace(",", " ")
                if baseline is not None:
                    line += f" · личная база {float(baseline):,.0f}".replace(",", " ")
                lines.append(line)
            workouts = activity_week.get("workouts")
            if workouts is not None:
                lines.append(f"Тренировки за неделю: {int(workouts)}")
            recovery_week = self._completed_week(recovery, completed_end)
            sleep = recovery_week.get("average_sleep_minutes")
            if sleep is not None:
                sleep_value = float(sleep)
                lines.append(
                    f"Средний сон за неделю: {int(sleep_value // 60)} ч "
                    f"{int(round(sleep_value % 60))} мин"
                )
            heart_average = recovery_week.get("average_heart_rate_bpm")
            if heart_average is not None:
                heart_line = (
                    f"Пульс с часов за неделю: средний "
                    f"{float(heart_average):.0f} уд/мин"
                )
                heart_minimum = recovery_week.get("minimum_heart_rate_bpm")
                heart_maximum = recovery_week.get("maximum_heart_rate_bpm")
                if heart_minimum is not None and heart_maximum is not None:
                    heart_line += (
                        f" · диапазон {float(heart_minimum):.0f}–"
                        f"{float(heart_maximum):.0f}"
                    )
                lines.append(heart_line)
            resting = recovery_week.get("average_resting_heart_rate_bpm")
            if resting is not None:
                lines.append(f"Средний пульс покоя за неделю: {float(resting):.0f} уд/мин")
            hrv = recovery_week.get("average_hrv_rmssd_ms")
            if hrv is not None:
                lines.append(f"Средний HRV за неделю: {float(hrv):.0f} мс")
        else:
            local_today = now.astimezone(self.settings.tz).date()
            activity_day = self._payload_date(activity_summary.get("latest_date"))
            activity_label = self._date_label(activity_day)
            steps = activity_summary.get("steps")
            if steps is not None:
                lines.append(f"Шаги за {activity_label}: {float(steps):,.0f}".replace(",", " "))
            active_minutes = activity_summary.get("active_minutes")
            if active_minutes is not None:
                lines.append(f"Активные минуты за {activity_label}: {float(active_minutes):.0f}")
            if activity_day is not None and (local_today - activity_day).days > HEALTH_SUMMARY_STALE_DAYS:
                lines.append(f"⚠️ Последние данные активности: {activity_label}.")

            recovery_day = self._payload_date(recovery_summary.get("latest_date"))
            recovery_label = self._date_label(recovery_day)
            sleep = recovery_summary.get("sleep_minutes")
            if sleep is not None:
                sleep_value = float(sleep)
                lines.append(
                    f"Сон за {recovery_label}: {int(sleep_value // 60)} ч "
                    f"{int(round(sleep_value % 60))} мин"
                )
            heart_average = recovery_summary.get("average_heart_rate_bpm")
            if heart_average is not None:
                heart_line = (
                    f"Пульс с часов за {recovery_label}: средний "
                    f"{float(heart_average):.0f} уд/мин"
                )
                heart_minimum = recovery_summary.get("minimum_heart_rate_bpm")
                heart_maximum = recovery_summary.get("maximum_heart_rate_bpm")
                if heart_minimum is not None and heart_maximum is not None:
                    heart_line += (
                        f" · диапазон {float(heart_minimum):.0f}–"
                        f"{float(heart_maximum):.0f}"
                    )
                lines.append(heart_line)
            resting = recovery_summary.get("resting_heart_rate_bpm")
            if resting is not None:
                lines.append(f"Пульс покоя за {recovery_label}: {float(resting):.0f} уд/мин")
            hrv = recovery_summary.get("hrv_rmssd_ms")
            if hrv is not None:
                lines.append(f"HRV за {recovery_label}: {float(hrv):.0f} мс")
            if recovery_day is not None and (local_today - recovery_day).days > HEALTH_SUMMARY_STALE_DAYS:
                lines.append(f"⚠️ Последние данные восстановления: {recovery_label}.")
        return lines

    def _daily_digest_text(self, now: datetime) -> str:
        summary = overview(self.db, self.settings.tz, now)
        lines = ["<b>☀️ Утренняя сводка Amigo</b>"]
        weight_is_stale = bool(summary["weight"].get("is_stale"))
        if summary["latest_weight"]:
            measured_at = datetime.fromisoformat(
                str(summary["latest_weight"]["measured_at"]).replace("Z", "+00:00")
            ).astimezone(self.settings.tz)
            lines.append(
                f"Вес: {summary['latest_weight']['value']:.2f} кг · "
                f"замер {measured_at:%d.%m.%Y}"
            )
        if weight_is_stale:
            lines.append(
                "⚠️ Нет свежего веса больше двух недель. "
                "Текущие тренд и отклонение от плана не показываются."
            )
        if not weight_is_stale and summary["rolling_7d_kg"] is not None:
            lines.append(f"Тренд веса: {summary['rolling_7d_kg']:.2f} кг")
        if not weight_is_stale and summary["plan_delta_kg"] is not None:
            lines.append(f"Отклонение от плана: {summary['plan_delta_kg']:+.2f} кг")
        pressure = summary.get("pressure") or {}
        if pressure.get("latest_systolic") is not None and pressure.get("latest_diastolic") is not None:
            lines.append(
                f"Давление: {float(pressure['latest_systolic']):.0f}/"
                f"{float(pressure['latest_diastolic']):.0f} мм рт. ст."
            )
        lines.extend(self._activity_recovery_lines(now, weekly=False))
        ai_lines = self._ai_lines(limit=3)
        lines.extend(ai_lines)
        if not ai_lines:
            lines.append("<i>ИИ-анализ ещё готовится; отправлены только факты.</i>")
        lines.append(f'<a href="{escape(self.settings.public_url)}">Открыть дашборд</a>')
        return "\n".join(lines)

    def _digest_text(self, now: datetime, *, week_ending: date | None = None) -> str:
        summary = overview(self.db, self.settings.tz, now)
        pressure = pressure_series(self.db, self.settings.tz, "30d", now)
        lines = ["<b>📊 Итоги недели Amigo</b>"]
        weight_is_stale = bool(summary["weight"].get("is_stale"))
        if summary["latest_weight"]:
            measured_at = datetime.fromisoformat(
                str(summary["latest_weight"]["measured_at"]).replace("Z", "+00:00")
            ).astimezone(self.settings.tz)
            lines.append(
                f"Вес: {summary['latest_weight']['value']:.2f} кг · "
                f"замер {measured_at:%d.%m.%Y}"
            )
        if weight_is_stale:
            lines.append(
                "⚠️ Нет свежего веса больше двух недель. "
                "Текущие тренд, отклонение от плана и прогноз приостановлены."
            )
        if not weight_is_stale and summary["rolling_7d_kg"] is not None:
            lines.append(f"Тренд: {summary['rolling_7d_kg']:.2f} кг")
        if not weight_is_stale and summary["plan_delta_kg"] is not None:
            lines.append(f"Отклонение от плана: {summary['plan_delta_kg']:+.2f} кг")
        if not weight_is_stale and summary["change_28d_kg"] is not None:
            lines.append(f"Изменение за 28 дней: {summary['change_28d_kg']:+.2f} кг")
        composition = summary["composition"]
        if composition["fat_pct"] is not None:
            lines.append(f"Доля жира: {composition['fat_pct']:.1f}% (BIA-оценка)")
        if composition["fat_mass_kg"] is not None:
            lines.append(f"Жировая масса: {composition['fat_mass_kg']:.1f} кг (BIA-оценка)")
        if composition["lean_mass_kg"] is not None:
            lines.append(f"Безжировая масса: {composition['lean_mass_kg']:.1f} кг (BIA-оценка)")
        stat = pressure["statistics"]["last_7_days"]
        if stat["systolic"] and stat["diastolic"]:
            lines.append(
                f"Среднее давление за 7 дней: {stat['systolic']['mean']:.0f}/{stat['diastolic']['mean']:.0f}"
            )
        lines.extend(
            self._activity_recovery_lines(
                now,
                weekly=True,
                week_ending=week_ending,
            )
        )
        ai_lines = self._ai_lines(limit=4)
        lines.extend(ai_lines)
        if not ai_lines:
            lines.append("<i>ИИ-анализ ещё готовится; отправлены только факты.</i>")
        lines.append(f'<a href="{escape(self.settings.public_url)}">Открыть дашборд</a>')
        return "\n".join(lines)
