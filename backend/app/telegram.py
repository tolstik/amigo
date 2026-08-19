from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select
from sqlalchemy.orm import Session

from .analytics import plan_weight, pressure_sessions, trend_change
from .config import Settings
from .models import JobRun, Measurement, MeasurementGroup, Outbox, utcnow
from .service import (
    active_plan,
    insights,
    overview,
    pressure_readings,
    pressure_series,
    weight_daily,
)


class TelegramError(RuntimeError):
    pass


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


def render_weekly_card(db: Session, settings: Settings, now: datetime | None = None) -> bytes:
    current = now or datetime.now(timezone.utc)
    plan = active_plan(db)
    local_today = current.astimezone(settings.tz).date()
    chart_start = max(plan.start_date, local_today - timedelta(days=89))
    recent = weight_daily(db, settings.tz, chart_start)
    summary = overview(db, settings.tz, current)
    canvas = Image.new("RGB", (1200, 700), "#101827")
    draw = ImageDraw.Draw(canvas)
    draw.text((64, 42), "AMIGO · НЕДЕЛЬНЫЙ ПРОГРЕСС", fill="#F8FAFC", font=_font(42, True))
    draw.text(
        (66, 100),
        current.astimezone(settings.tz).strftime("%d.%m.%Y"),
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
    composition = summary["composition"]
    composition_bits = []
    if composition["fat_pct"] is not None:
        composition_bits.append(f"жир {composition['fat_pct']:.1f}%")
    if composition["fat_mass_kg"] is not None:
        composition_bits.append(f"жировая масса {composition['fat_mass_kg']:.1f} кг")
    if composition["lean_mass_kg"] is not None:
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

    def _eligible_advice(self, limit: int, now: datetime) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
        items = insights(self.db, self.settings.tz, now)["items"]
        iso = now.astimezone(self.settings.tz).date().isocalendar()
        chosen: list[dict[str, Any]] = []
        keys: list[str] = []
        for item in items:
            if str(item.get("rule", "")).startswith("milestone_"):
                key = f"advice:{item['id']}:once"
            else:
                key = f"advice:{item['id']}:{iso.year}-W{iso.week:02d}"
            if self.db.scalar(select(JobRun.id).where(JobRun.run_key == key)) is not None:
                continue
            chosen.append(item)
            keys.append(key)
            if len(chosen) == limit:
                break
        return chosen, tuple(keys)

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
            text, keys = self._digest_text(current)
            self.client.send_photo(render_weekly_card(self.db, self.settings, current), text)
            return DeliveryResult(keys)
        raise TelegramError(f"unsupported outbox event {event.event_type}")

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
        advice, keys = self._eligible_advice(1, now)
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
        if advice:
            lines.append(f"💡 {escape(str(advice[0]['message']))}")
        lines.append(f'<a href="{escape(self.settings.public_url)}">Открыть Amigo</a>')
        return "\n".join(lines), keys

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

    def _digest_text(self, now: datetime) -> tuple[str, tuple[str, ...]]:
        summary = overview(self.db, self.settings.tz, now)
        pressure = pressure_series(self.db, self.settings.tz, "30d", now)
        advice, keys = self._eligible_advice(2, now)
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
        for item in advice:
            lines.append(f"💡 {escape(str(item['message']))}")
        lines.append(f'<a href="{escape(self.settings.public_url)}">Открыть дашборд</a>')
        return "\n".join(lines), keys
