from datetime import datetime, timezone
from unittest.mock import Mock

from app.config import Settings
from app.models import Outbox
from app.telegram import TelegramNotifier
from app.main import app
from fastapi.testclient import TestClient


def test_retired_task_routes_return_404_for_every_method():
    with TestClient(app) as client:
        for path in ("/api/v1/tasks", "/api/v1/tasks/00000000-0000-4000-8000-000000000000", "/api/v1/tasks/00000000-0000-4000-8000-000000000000/complete", "/api/v1/tasks/00000000-0000-4000-8000-000000000000/cancel"):
            for method in ("GET", "POST", "PATCH", "DELETE"):
                assert client.request(method, path).status_code == 404


def test_retired_queued_reminder_is_consumed_without_telegram(db):
    client = Mock()
    notifier = TelegramNotifier(db, Settings(database_url="sqlite+pysqlite:///:memory:"), client=client)
    # Even a malformed legacy payload cannot send, retry, or expose task notes.
    event = Outbox(event_key="retired-reminder", event_type="task.reminder", payload={})
    result = notifier.deliver(event, datetime.now(timezone.utc))
    assert not result.advice_run_keys
    client.send_message.assert_not_called()
    client.send_photo.assert_not_called()
