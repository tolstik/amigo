from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.ai_contracts import (
    AI_MODEL,
    AI_PROMPT_VERSION,
    AiAnalysis,
    AiRecommendation,
    AnalysisSnapshot,
    GatewayAnalyzeResponse,
    SnapshotFact,
)
from app.ai_queue import claim_analysis_job, complete_analysis_job, enqueue_analysis
from app.feature_models import HealthTask, HealthTaskReminderDelivery
from app.models import Outbox
from app.tasks_api import (
    TaskCreate,
    _next_occurrence,
    _source_snapshot,
    complete_task,
    create_task,
)
from app.telegram import TelegramNotifier
from app.worker import schedule_task_reminders
from app.config import Settings


def test_creates_and_completes_one_time_task(db):
    due = datetime.now(timezone.utc) + timedelta(hours=2)
    created = create_task(
        TaskCreate(title="Измерить давление", next_due_at=due, telegram_enabled=False),
        None,  # type: ignore[arg-type]
        db,
    )

    assert created["status"] == "active"
    assert created["overdue"] is False
    completed = complete_task(created["id"], None, db)  # type: ignore[arg-type]
    assert completed["status"] == "completed"
    assert completed["next_due_at"] is None


def test_monthly_recurrence_clamps_to_last_day():
    january = datetime(2027, 1, 31, 6, tzinfo=timezone.utc)
    assert _next_occurrence(january, "monthly") == datetime(
        2027, 2, 28, 6, tzinfo=timezone.utc
    )
    leap_january = datetime(2028, 1, 31, 6, tzinfo=timezone.utc)
    assert _next_occurrence(leap_january, "monthly") == datetime(
        2028, 2, 29, 6, tzinfo=timezone.utc
    )


def test_due_task_schedules_one_deduplicated_telegram_delivery(db):
    now = datetime(2026, 8, 25, 8, tzinfo=timezone.utc)
    task = HealthTask(
        id="11111111-1111-4111-8111-111111111111",
        title="Повторить анализ",
        next_due_at=now - timedelta(minutes=1),
        recurrence="once",
        telegram_enabled=True,
        status="active",
        created_at=now - timedelta(days=1),
        updated_at=now - timedelta(days=1),
    )
    db.add(task)
    db.commit()

    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    assert schedule_task_reminders(db, settings, now) == 1
    assert schedule_task_reminders(db, settings, now) == 0
    deliveries = db.query(HealthTaskReminderDelivery).all()
    assert len(deliveries) == 1
    assert deliveries[0].status == "pending"


def test_existing_deliveries_do_not_starve_later_due_tasks(db):
    now = datetime(2026, 8, 25, 8, tzinfo=timezone.utc)
    for index in range(50):
        occurrence = now - timedelta(minutes=100 - index)
        task = HealthTask(
            id=f"00000000-0000-4000-8000-{index:012d}",
            title=f"Уже отправлено {index}",
            next_due_at=occurrence,
            recurrence="once",
            telegram_enabled=True,
            status="active",
            created_at=now - timedelta(days=1),
            updated_at=now - timedelta(days=1),
        )
        db.add(task)
        db.flush()
        db.add(
            HealthTaskReminderDelivery(
                task_id=task.id,
                occurrence_at=occurrence,
                channel="telegram",
                status="sent",
                created_at=occurrence,
                sent_at=occurrence,
            )
        )
    pending = HealthTask(
        id="ffffffff-ffff-4fff-8fff-ffffffffffff",
        title="Новое напоминание",
        next_due_at=now - timedelta(minutes=1),
        recurrence="once",
        telegram_enabled=True,
        status="active",
        created_at=now - timedelta(days=1),
        updated_at=now - timedelta(days=1),
    )
    db.add(pending)
    db.commit()

    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    assert schedule_task_reminders(db, settings, now) == 1
    delivery = db.query(HealthTaskReminderDelivery).filter_by(task_id=pending.id).one()
    assert delivery.status == "pending"


def test_queued_reminder_is_suppressed_after_telegram_is_disabled(db):
    now = datetime(2026, 8, 25, 8, tzinfo=timezone.utc)
    task = HealthTask(
        id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        title="Не отправлять",
        next_due_at=now,
        recurrence="once",
        telegram_enabled=False,
        status="active",
        created_at=now - timedelta(days=1),
        updated_at=now,
    )
    event = Outbox(
        event_key=f"task-reminder:{task.id}:{now.isoformat()}",
        event_type="task.reminder",
        payload={"task_id": task.id, "occurrence_at": now.isoformat()},
        available_at=now,
    )
    db.add_all([task, event])
    db.commit()

    notifier = TelegramNotifier(
        db,
        Settings(database_url="sqlite+pysqlite:///:memory:"),
        client=object(),  # type: ignore[arg-type]
    )
    assert notifier._task_reminder_text(event, now) is None


def test_reminder_contains_only_title_due_time_and_dashboard_link(db):
    now = datetime(2026, 8, 25, 8, tzinfo=timezone.utc)
    task = HealthTask(
        id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        title="Повторить анализ",
        note="SECRET_PRIVATE_NOTE",
        next_due_at=now,
        recurrence="once",
        telegram_enabled=True,
        status="active",
        source_snapshot={"text": "SECRET_AI_SOURCE"},
        created_at=now - timedelta(days=1),
        updated_at=now,
    )
    event = Outbox(
        event_key=f"task-reminder:{task.id}:{now.isoformat()}",
        event_type="task.reminder",
        payload={"task_id": task.id, "occurrence_at": now.isoformat()},
        available_at=now,
    )
    db.add_all([task, event])
    db.commit()
    notifier = TelegramNotifier(
        db,
        Settings(database_url="sqlite+pysqlite:///:memory:"),
        client=object(),  # type: ignore[arg-type]
    )

    text = notifier._task_reminder_text(event, now)
    assert text is not None
    assert "Повторить анализ" in text
    assert "25.08.2026 11:00" in text
    assert "/amigo/tasks" in text
    assert "SECRET_PRIVATE_NOTE" not in text
    assert "SECRET_AI_SOURCE" not in text
    assert task.id not in text


def _completed_recommendation(db, value: int, now: datetime):
    snapshot = AnalysisSnapshot(
        source_through=now,
        facts=[
            SnapshotFact(
                key="activity.steps_latest",
                scope="activity",
                period="day",
                value=value,
                unit="steps",
                observed_on=now.date(),
            )
        ],
    )
    enqueue_analysis(
        db,
        snapshot,
        trigger="activity",
        now=now,
        debounce_seconds=0,
        activity_min_interval_seconds=0,
    )
    job = claim_analysis_job(db, now=now)
    assert job is not None
    return complete_analysis_job(
        db,
        job,
        GatewayAnalyzeResponse(
            snapshot_hash=job.snapshot_hash,
            prompt_version=AI_PROMPT_VERSION,
            model=AI_MODEL,
            generated_at=now,
            duration_ms=1,
            analysis=AiAnalysis(
                headline="Активность",
                summary="Доступна сохранённая динамика активности.",
                observations=[],
                recommendations=[
                    AiRecommendation(
                        title="Проверить ритм",
                        text="Сверяйте число шагов раз в неделю.",
                        scope="activity",
                        evidence_keys=["activity.steps_latest"],
                    )
                ],
                confidence="medium",
                limitations=[],
            ),
        ),
    )


def test_task_source_accepts_only_latest_validated_analysis(db):
    now = datetime.now(timezone.utc)
    first = _completed_recommendation(db, 5_000, now - timedelta(minutes=2))
    source = _source_snapshot(db, first.id, "recommendation-1")
    assert source["text"] == "Сверяйте число шагов раз в неделю."
    assert source["evidence"]["activity.steps_latest"]["value"] == 5_000

    second = _completed_recommendation(db, 6_000, now)
    assert _source_snapshot(db, second.id, "recommendation-1")["evidence_ids"] == [
        "activity.steps_latest"
    ]
    with pytest.raises(HTTPException) as stale:
        _source_snapshot(db, first.id, "recommendation-1")
    assert stale.value.status_code == 409
    with pytest.raises(HTTPException) as malformed:
        _source_snapshot(db, second.id, "1")
    assert malformed.value.status_code == 404
