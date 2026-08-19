from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO

from PIL import Image

from app.config import Settings
from app.models import Outbox
from app.service import ensure_default_plan
from app.telegram import TelegramNotifier, render_weekly_card
from app.worker import schedule_weekly_digest


def test_weekly_card_is_local_png_and_digest_is_idempotent(db, add_group):
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
    now = datetime(2026, 8, 17, 5, 30, tzinfo=timezone.utc)  # Monday 08:30 Moscow
    image = render_weekly_card(db, settings, now)
    assert image.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(BytesIO(image)) as card:
        assert card.format == "PNG"
        assert card.size == (1200, 700)
    assert schedule_weekly_digest(db, settings, now)
    assert not schedule_weekly_digest(db, settings, now)
    event = db.query(Outbox).one()
    assert event.event_type == "weekly.digest"


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
    text, _ = notifier._digest_text(start + timedelta(days=33, hours=1))
    assert "Нет свежего веса больше двух недель" in text
    assert "Отклонение от плана:" not in text
    assert "\nТренд:" not in text
    assert "Изменение за 28 дней:" not in text
