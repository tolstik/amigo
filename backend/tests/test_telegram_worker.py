from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from io import BytesIO

from PIL import Image

from app.config import Settings
from app.models import Outbox
from app.service import ensure_default_plan
from app.telegram import TelegramNotifier, render_weekly_card
from app.worker import schedule_daily_digest, schedule_weekly_digest


class RecordingTelegramClient:
    def __init__(self):
        self.calls: list[tuple[str, object, object | None]] = []

    def send_message(self, text: str) -> None:
        self.calls.append(("message", text, None))

    def send_photo(self, image: bytes, caption: str) -> None:
        self.calls.append(("photo", image, caption))


def test_weekly_card_is_local_png_and_digest_is_idempotent_after_0900(db, add_group):
    ensure_default_plan(db)
    add_group(
        "w1",
        datetime(2026, 8, 17, 6, tzinfo=timezone.utc),
        {
            "weight": (126.4, "kg"),
            "fat_percent": (34.1, "%"),
            "fat_mass": (43.1, "kg"),
            "fat_free_mass": (83.3, "kg"),
        },
    )
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    now = datetime(2026, 8, 17, 6, 30, tzinfo=timezone.utc)  # Monday 09:30 Moscow
    image = render_weekly_card(db, settings, now)
    assert image.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(BytesIO(image)) as card:
        assert card.format == "PNG"
        assert card.size == (1200, 700)
    assert schedule_weekly_digest(db, settings, now)
    assert not schedule_weekly_digest(db, settings, now)
    event = db.query(Outbox).one()
    assert event.event_type == "weekly.digest"
    assert event.payload == {"week_ending": "2026-08-16"}


def test_weekly_card_excludes_the_new_partial_week(db, add_group):
    ensure_default_plan(db)
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    now = datetime(2026, 8, 17, 6, 30, tzinfo=timezone.utc)  # Monday 09:30 Moscow
    week_ending = date(2026, 8, 16)
    add_group(
        "sunday",
        datetime(2026, 8, 16, 6, tzinfo=timezone.utc),
        {
            "weight": (126.8, "kg"),
            "fat_percent": (34.2, "%"),
            "fat_mass": (43.4, "kg"),
        },
    )
    completed_week_card = render_weekly_card(db, settings, now, week_ending)

    add_group(
        "monday",
        datetime(2026, 8, 17, 6, tzinfo=timezone.utc),
        {
            "weight": (125.1, "kg"),
            "fat_percent": (31.0, "%"),
            "fat_mass": (38.8, "kg"),
        },
    )

    assert render_weekly_card(db, settings, now, week_ending) == completed_week_card


def test_daily_digest_is_idempotent_after_0900(db):
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    before = datetime(2026, 8, 18, 5, 59, tzinfo=timezone.utc)  # Tuesday 08:59 Moscow
    now = datetime(2026, 8, 18, 6, 30, tzinfo=timezone.utc)  # Tuesday 09:30 Moscow

    assert not schedule_daily_digest(db, settings, before)
    assert schedule_daily_digest(db, settings, now)
    assert not schedule_daily_digest(db, settings, now)

    events = db.query(Outbox).all()
    assert [(event.event_type, event.event_key) for event in events] == [
        ("daily.digest", "daily-digest:2026-08-18")
    ]


def test_monday_weekly_digest_replaces_daily_digest(db):
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    now = datetime(2026, 8, 17, 6, 30, tzinfo=timezone.utc)  # Monday 09:30 Moscow

    assert schedule_weekly_digest(db, settings, now)
    assert not schedule_daily_digest(db, settings, now)
    assert [event.event_type for event in db.query(Outbox).all()] == ["weekly.digest"]


def test_weekly_delivery_sends_photo_then_full_message(db, monkeypatch):
    ensure_default_plan(db)
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    now = datetime(2026, 8, 17, 6, 30, tzinfo=timezone.utc)
    client = RecordingTelegramClient()
    notifier = TelegramNotifier(db, settings, client=client)  # type: ignore[arg-type]
    monkeypatch.setattr("app.telegram.render_weekly_card", lambda *_args: b"weekly-png")
    event = Outbox(
        event_key="weekly-digest:2026-08-17",
        event_type="weekly.digest",
        payload={"week_ending": "2026-08-16"},
        available_at=now,
    )

    notifier.deliver(event, now)

    assert [call[0] for call in client.calls] == ["photo", "message"]
    assert client.calls[0] == (
        "photo",
        b"weekly-png",
        "<b>📊 Недельный график Amigo</b>",
    )
    assert str(client.calls[1][1]).startswith("<b>📊 Итоги недели Amigo</b>")
    assert client.calls[1][1] != client.calls[0][2]


def test_monday_weekly_digest_uses_previous_completed_activity_and_recovery_week(
    db, monkeypatch
):
    ensure_default_plan(db)
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    notifier = TelegramNotifier(db, settings, client=RecordingTelegramClient())  # type: ignore[arg-type]
    now = datetime(2026, 8, 17, 6, 30, tzinfo=timezone.utc)  # Monday 09:30 Moscow
    monkeypatch.setattr(
        "app.telegram.activity_series",
        lambda *_args: {
            "summary": {"latest_date": "2026-08-17", "steps": 100},
            "weekly": [
                {
                    "start_date": "2026-08-10",
                    "end_date": "2026-08-16",
                    "actual_steps": 70_000,
                    "baseline_steps": 63_000,
                    "workouts": 4,
                },
                {
                    "start_date": "2026-08-17",
                    "end_date": "2026-08-17",
                    "actual_steps": 100,
                    "baseline_steps": 90,
                    "workouts": 0,
                },
            ],
        },
    )
    monkeypatch.setattr(
        "app.telegram.recovery_series",
        lambda *_args: {
            "summary": {"latest_date": "2026-08-17", "sleep_minutes": 120},
            "weekly": [
                {
                    "start_date": "2026-08-10",
                    "end_date": "2026-08-16",
                    "average_sleep_minutes": 450,
                    "average_resting_heart_rate_bpm": 61,
                    "average_hrv_rmssd_ms": 44,
                },
                {
                    "start_date": "2026-08-17",
                    "end_date": "2026-08-17",
                    "average_sleep_minutes": 120,
                    "average_resting_heart_rate_bpm": 99,
                    "average_hrv_rmssd_ms": 10,
                },
            ],
        },
    )

    text = notifier._digest_text(now, week_ending=date(2026, 8, 16))

    assert "Шаги за неделю 10.08–16.08.2026: 70 000 · личная база 63 000" in text
    assert "Тренировки за неделю: 4" in text
    assert "Средний сон за неделю: 7 ч 30 мин" in text
    assert "Средний пульс покоя за неделю: 61 уд/мин" in text
    assert "Средний HRV за неделю: 44 мс" in text
    assert "99 уд/мин" not in text


def test_ai_text_is_html_escaped_in_telegram(db, monkeypatch):
    ensure_default_plan(db)
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    notifier = TelegramNotifier(db, settings, client=RecordingTelegramClient())  # type: ignore[arg-type]
    monkeypatch.setattr(
        "app.telegram.public_analysis_payload",
        lambda *_args: {
            "status": "ready",
            "analysis": {
                "headline": "<Фокус> & ритм",
                "summary": "Сон < 7 ч & нагрузка > базы",
                "observations": [
                    {
                        "title": "Шаги <базы>",
                        "text": "A&B > C",
                    }
                ],
                "recommendations": [],
            },
        },
    )

    text = notifier._daily_digest_text(datetime(2026, 8, 18, 6, 30, tzinfo=timezone.utc))

    assert "<b>✨ &lt;Фокус&gt; &amp; ритм</b>" in text
    assert "Сон &lt; 7 ч &amp; нагрузка &gt; базы" in text
    assert "<b>Шаги &lt;базы&gt;</b>: A&amp;B &gt; C" in text
    assert "<Фокус>" not in text
    assert "A&B" not in text


def test_ai_text_is_bounded_without_cutting_html_entities(db, monkeypatch):
    ensure_default_plan(db)
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    notifier = TelegramNotifier(db, settings, client=RecordingTelegramClient())  # type: ignore[arg-type]
    monkeypatch.setattr(
        "app.telegram.public_analysis_payload",
        lambda *_args: {
            "status": "ready",
            "analysis": {
                "headline": "&" * 500,
                "summary": "&" * 1_000,
                "observations": [
                    {"title": "&" * 200, "text": "&" * 1_000}
                    for _ in range(4)
                ],
                "recommendations": [],
            },
        },
    )

    text = notifier._daily_digest_text(datetime(2026, 8, 18, 6, 30, tzinfo=timezone.utc))

    assert len(text) < 3_900
    assert "&…" not in text
    assert "&amp;" in text


def test_digests_are_explicitly_facts_only_while_ai_is_unavailable(db, monkeypatch):
    ensure_default_plan(db)
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    notifier = TelegramNotifier(db, settings, client=RecordingTelegramClient())  # type: ignore[arg-type]
    monkeypatch.setattr(
        "app.telegram.public_analysis_payload",
        lambda *_args: {"status": "pending", "analysis": None},
    )
    now = datetime(2026, 8, 18, 6, 30, tzinfo=timezone.utc)

    daily = notifier._daily_digest_text(now)
    weekly = notifier._digest_text(now)

    notice = "<i>ИИ-анализ ещё готовится; отправлены только факты.</i>"
    assert notice in daily
    assert notice in weekly
    assert "<b>✨" not in daily
    assert "<b>✨" not in weekly


def test_stale_weekly_digest_does_not_present_old_trend_as_current(db, add_group):
    ensure_default_plan(db)
    start = datetime(2026, 8, 15, 6, tzinfo=timezone.utc)
    for index in range(18):
        add_group(
            f"stale-digest-{index}",
            start + timedelta(days=index),
            {"weight": (127.03 - index * 0.15, "kg")},
        )
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    notifier = TelegramNotifier(db, settings, client=object())  # type: ignore[arg-type]
    text = notifier._digest_text(start + timedelta(days=33, hours=1))
    assert "Нет свежего веса больше двух недель" in text
    assert "Отклонение от плана:" not in text
    assert "\nТренд:" not in text
    assert "Изменение за 28 дней:" not in text


def test_stale_daily_digest_dates_sources_and_suppresses_old_weight_trend(
    db, add_group, monkeypatch
):
    ensure_default_plan(db)
    start = datetime(2026, 8, 15, 6, tzinfo=timezone.utc)
    for index in range(18):
        add_group(
            f"stale-daily-{index}",
            start + timedelta(days=index),
            {"weight": (127.03 - index * 0.15, "kg")},
        )
    monkeypatch.setattr(
        "app.telegram.activity_series",
        lambda *_args: {
            "summary": {
                "latest_date": "2026-08-20",
                "steps": 8_432,
                "active_minutes": 47,
            },
            "weekly": [],
        },
    )
    monkeypatch.setattr(
        "app.telegram.recovery_series",
        lambda *_args: {
            "summary": {
                "latest_date": "2026-08-21",
                "sleep_minutes": 438,
                "resting_heart_rate_bpm": 62,
                "hrv_rmssd_ms": 43,
            },
            "weekly": [],
        },
    )
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    notifier = TelegramNotifier(db, settings, client=RecordingTelegramClient())  # type: ignore[arg-type]

    text = notifier._daily_digest_text(start + timedelta(days=33, hours=1))

    assert "Вес:" in text and "замер 01.09.2026" in text
    assert "Нет свежего веса больше двух недель" in text
    assert "Тренд веса:" not in text
    assert "Отклонение от плана:" not in text
    assert "Шаги за 20.08.2026: 8 432" in text
    assert "Активные минуты за 20.08.2026: 47" in text
    assert "Последние данные активности: 20.08.2026" in text
    assert "Сон за 21.08.2026: 7 ч 18 мин" in text
    assert "Пульс покоя за 21.08.2026: 62 уд/мин" in text
    assert "HRV за 21.08.2026: 43 мс" in text
    assert "Последние данные восстановления: 21.08.2026" in text
