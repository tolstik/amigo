from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import sessionmaker

import app.worker as worker_module
from app.config import Settings
from app.models import JobRun
from app.withings import SyncResult


def sync_result(*, created: int = 0, updated: int = 0) -> SyncResult:
    return SyncResult(
        pages=1,
        groups_seen=created + updated,
        groups_created=created,
        groups_updated=updated,
        notifications_enqueued=0,
        cursor=1,
    )


@pytest.mark.parametrize(
    ("created", "updated", "analysis_expected"),
    ((0, 0, False), (1, 0, True), (0, 1, True)),
)
def test_incremental_sync_uses_group_counters_and_records_success(
    db, monkeypatch, created, updated, analysis_expected
):
    now = datetime(2026, 8, 18, 1, 0, tzinfo=timezone.utc)
    result = sync_result(created=created, updated=updated)
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    analysis_calls = []

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz is not None else now.replace(tzinfo=None)

    class FakeWithingsClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def sync(self, **kwargs):
            assert kwargs == {}
            return result

    monkeypatch.setattr(worker_module, "datetime", FixedDatetime)
    monkeypatch.setattr(worker_module, "SessionLocal", factory)
    monkeypatch.setattr(worker_module, "WithingsClient", FakeWithingsClient)
    monkeypatch.setattr(
        worker_module,
        "enqueue_current_analysis",
        lambda *_args, **kwargs: analysis_calls.append(kwargs),
    )

    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        timezone="UTC",
        ai_enabled=True,
    )
    worker_module.Worker(settings).run_once()

    db.expire_all()
    run = db.query(JobRun).filter_by(job_name="withings-incremental").one()
    assert run.status == "success"
    assert run.details["groups_created"] == created
    assert run.details["groups_updated"] == updated
    assert bool(analysis_calls) is analysis_expected
