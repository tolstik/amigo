from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import signal
import threading
import time

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .ai_snapshot import enqueue_current_analysis
from .db import SessionLocal
from .feature_models import (
    DoctorReportSnapshot,
    HealthTask,
    HealthTaskReminderDelivery,
)
from .models import JobRun, Outbox, utcnow
from .telegram import TelegramNotifier
from .withings import SyncResult, WithingsClient


logger = logging.getLogger("amigo.worker")
WEEKDAYS = {name: index for index, name in enumerate(("mon", "tue", "wed", "thu", "fri", "sat", "sun"))}


def _withings_sync_has_changes(result: SyncResult) -> bool:
    return result.groups_created > 0 or result.groups_updated > 0


def schedule_weekly_digest(db: Session, settings: Settings, now: datetime | None = None) -> bool:
    current = now or datetime.now(timezone.utc)
    local = current.astimezone(settings.tz)
    digest_time = settings.weekly_digest_time
    if local.weekday() != WEEKDAYS[settings.weekly_digest_day] or (
        local.hour,
        local.minute,
    ) < (digest_time.hour, digest_time.minute):
        return False
    event_key = f"weekly-digest:{local.date().isoformat()}"
    if db.scalar(select(Outbox.id).where(Outbox.event_key == event_key)) is not None:
        return False
    db.add(
        Outbox(
            event_key=event_key,
            event_type="weekly.digest",
            # The Monday report summarizes the ISO week that ended on Sunday,
            # never the just-started current week.
            payload={"week_ending": (local.date() - timedelta(days=1)).isoformat()},
            available_at=current,
        )
    )
    db.commit()
    return True


def schedule_daily_digest(db: Session, settings: Settings, now: datetime | None = None) -> bool:
    current = now or datetime.now(timezone.utc)
    local = current.astimezone(settings.tz)
    digest_time = settings.daily_digest_time
    # Monday's expanded weekly digest replaces the daily message.
    if local.weekday() == WEEKDAYS[settings.weekly_digest_day] or (
        local.hour,
        local.minute,
    ) < (digest_time.hour, digest_time.minute):
        return False
    event_key = f"daily-digest:{local.date().isoformat()}"
    if db.scalar(select(Outbox.id).where(Outbox.event_key == event_key)) is not None:
        return False
    db.add(
        Outbox(
            event_key=event_key,
            event_type="daily.digest",
            payload={"day": local.date().isoformat()},
            available_at=current,
        )
    )
    db.commit()
    return True


def schedule_task_reminders(
    db: Session,
    settings: Settings,
    now: datetime | None = None,
) -> int:
    """Create at most one Telegram outbox event for each due task occurrence."""

    current = now or datetime.now(timezone.utc)
    lower_bound = current - timedelta(hours=24)
    already_scheduled = (
        select(HealthTaskReminderDelivery.id)
        .where(
            HealthTaskReminderDelivery.task_id == HealthTask.id,
            HealthTaskReminderDelivery.occurrence_at == HealthTask.next_due_at,
            HealthTaskReminderDelivery.channel == "telegram",
        )
        .exists()
    )
    rows = list(
        db.scalars(
            select(HealthTask)
            .where(
                HealthTask.status == "active",
                HealthTask.telegram_enabled.is_(True),
                HealthTask.next_due_at.is_not(None),
                HealthTask.next_due_at <= current,
                HealthTask.next_due_at >= lower_bound,
                ~already_scheduled,
            )
            .order_by(HealthTask.next_due_at, HealthTask.id)
            .limit(50)
        )
    )
    created = 0
    for task in rows:
        occurrence = task.next_due_at
        if occurrence is None:
            continue
        aware_occurrence = (
            occurrence.replace(tzinfo=timezone.utc)
            if occurrence.tzinfo is None
            else occurrence.astimezone(timezone.utc)
        )
        existing = db.scalar(
            select(HealthTaskReminderDelivery.id).where(
                HealthTaskReminderDelivery.task_id == task.id,
                HealthTaskReminderDelivery.occurrence_at == aware_occurrence,
                HealthTaskReminderDelivery.channel == "telegram",
            )
        )
        if existing is not None:
            continue
        event = Outbox(
            event_key=f"task-reminder:{task.id}:{aware_occurrence.isoformat()}",
            event_type="task.reminder",
            payload={"task_id": task.id, "occurrence_at": aware_occurrence.isoformat()},
            available_at=current,
        )
        db.add(event)
        db.flush()
        db.add(
            HealthTaskReminderDelivery(
                task_id=task.id,
                occurrence_at=aware_occurrence,
                channel="telegram",
                status="pending",
                outbox_id=event.id,
                created_at=current,
            )
        )
        created += 1
    if created:
        db.commit()
    return created


def cleanup_doctor_reports(db: Session, now: datetime | None = None) -> int:
    current = now or datetime.now(timezone.utc)
    result = db.execute(
        delete(DoctorReportSnapshot).where(DoctorReportSnapshot.expires_at <= current)
    )
    if result.rowcount:
        db.commit()
    return result.rowcount or 0


class OutboxProcessor:
    def __init__(self, db: Session, settings: Settings):
        self.db = db
        self.settings = settings

    def recover_stale(self, now: datetime | None = None) -> int:
        current = now or utcnow()
        result = self.db.execute(
            update(Outbox)
            .where(Outbox.status == "processing", Outbox.available_at <= current)
            .values(status="pending")
        )
        self.db.commit()
        return result.rowcount

    def process_one(self, now: datetime | None = None) -> bool:
        current = now or utcnow()
        event = self.db.scalar(
            select(Outbox)
            .where(
                Outbox.status == "pending",
                Outbox.available_at <= current,
                Outbox.attempts < 6,
            )
            .order_by(Outbox.available_at, Outbox.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if event is None:
            return False
        event.status = "processing"
        event.available_at = current + timedelta(minutes=10)
        self.db.commit()
        try:
            notifier = TelegramNotifier(self.db, self.settings)
            try:
                result = notifier.deliver(event, current)
            finally:
                notifier.close()
            event.status = "sent"
            event.sent_at = utcnow()
            event.last_error = None
            if event.event_type == "task.reminder":
                delivery = self.db.scalar(
                    select(HealthTaskReminderDelivery).where(
                        HealthTaskReminderDelivery.outbox_id == event.id
                    )
                )
                if delivery is not None:
                    delivery.status = "sent"
                    delivery.sent_at = event.sent_at
            for key in result.advice_run_keys:
                if self.db.scalar(select(JobRun.id).where(JobRun.run_key == key)) is None:
                    self.db.add(
                        JobRun(
                            job_name="advice-delivered",
                            run_key=key,
                            status="success",
                            finished_at=utcnow(),
                        )
                    )
            self.db.commit()
        except Exception as exc:
            self.db.rollback()
            event = self.db.get(Outbox, event.id)
            event.attempts += 1
            event.last_error = str(exc)[:2000]
            if event.attempts >= 6:
                event.status = "failed"
            else:
                event.status = "pending"
                event.available_at = utcnow() + timedelta(minutes=min(60, 2**event.attempts))
            if event.event_type == "task.reminder":
                delivery = self.db.scalar(
                    select(HealthTaskReminderDelivery).where(
                        HealthTaskReminderDelivery.outbox_id == event.id
                    )
                )
                if delivery is not None:
                    delivery.status = "failed" if event.status == "failed" else "pending"
            self.db.commit()
            logger.warning("outbox delivery %s failed: %s", event.id, type(exc).__name__)
        return True

    def drain(self, limit: int = 20) -> int:
        processed = 0
        while processed < limit and self.process_one():
            processed += 1
        return processed


class Worker:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.running = True
        self.next_sync_at = 0.0
        self.stop_event = threading.Event()

    def stop(self, *_: object) -> None:
        self.running = False
        self.stop_event.set()

    def _recorded_job(self, db: Session, job_name: str, run_key: str, action) -> None:
        if db.scalar(select(JobRun.id).where(JobRun.run_key == run_key)) is not None:
            return
        run = JobRun(job_name=job_name, run_key=run_key, status="running")
        db.add(run)
        db.commit()
        try:
            details = action()
            run.status = "success"
            run.details = details or {}
        except Exception as exc:
            db.rollback()
            run = db.scalar(select(JobRun).where(JobRun.run_key == run_key))
            run.status = "failed"
            run.details = {"error_type": type(exc).__name__}
            logger.exception("job %s failed", job_name)
        run.finished_at = utcnow()
        db.commit()

    def run_once(self) -> None:
        now = datetime.now(timezone.utc)
        local = now.astimezone(self.settings.tz)
        with SessionLocal() as db:
            processor = OutboxProcessor(db, self.settings)
            processor.recover_stale(now)
            if time.monotonic() >= self.next_sync_at:
                run_key = f"sync:{now.strftime('%Y%m%dT%H%M')}"

                def incremental():
                    with WithingsClient(db, self.settings) as client:
                        result = client.sync()
                    if _withings_sync_has_changes(result):
                        enqueue_current_analysis(
                            db,
                            self.settings,
                            trigger="measurement",
                            now=now,
                        )
                    return result.__dict__

                self._recorded_job(db, "withings-incremental", run_key, incremental)
                self.next_sync_at = time.monotonic() + self.settings.sync_interval_seconds
            if local.hour >= 3:
                run_key = f"reconcile-90d:{local.date().isoformat()}"

                def reconcile():
                    with WithingsClient(db, self.settings) as client:
                        result = client.sync(reconcile_days=90, suppress_notifications=True)
                    if _withings_sync_has_changes(result):
                        enqueue_current_analysis(
                            db,
                            self.settings,
                            trigger="measurement",
                            now=now,
                        )
                    return result.__dict__

                self._recorded_job(db, "withings-reconcile", run_key, reconcile)
            digest_at = datetime.combine(local.date(), self.settings.daily_digest_time, self.settings.tz)
            prepare_at = digest_at - timedelta(minutes=15)
            if local >= prepare_at:
                run_key = f"ai-digest-prepare:{local.date().isoformat()}"

                def prepare_ai_digest():
                    job = enqueue_current_analysis(
                        db,
                        self.settings,
                        trigger="scheduled",
                        now=now,
                    )
                    return {"job_id": job.id if job is not None else None}

                self._recorded_job(db, "ai-digest-prepare", run_key, prepare_ai_digest)
            schedule_weekly_digest(db, self.settings, now)
            schedule_daily_digest(db, self.settings, now)
            schedule_task_reminders(db, self.settings, now)
            cleanup_doctor_reports(db, now)
            processor.drain()
    def run(self) -> None:
        while self.running:
            self.run_once()
            if self.settings.worker_once:
                break
            self.stop_event.wait(self.settings.outbox_poll_seconds)


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    worker = Worker(settings)
    signal.signal(signal.SIGTERM, worker.stop)
    signal.signal(signal.SIGINT, worker.stop)
    worker.run()


if __name__ == "__main__":
    main()
