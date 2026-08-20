from __future__ import annotations

import logging
import time

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url


logger = logging.getLogger("amigo.background_wait")


_ALLOWED_CHANNELS = frozenset({"amigo_ai_work", "amigo_queue_events"})


def wait_for_notification(
    database_url: str,
    channel: str,
    timeout_seconds: float = 60,
) -> None:
    """Sleep on a bounded PostgreSQL notification instead of polling tables."""

    if channel not in _ALLOWED_CHANNELS:
        raise ValueError("unsupported notification channel")
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        time.sleep(timeout_seconds)
        return
    kwargs = {
        "dbname": url.database,
        "user": url.username,
        "password": url.password,
        "host": url.host,
        "port": url.port,
        "connect_timeout": 5,
    }
    try:
        with psycopg.connect(**{key: value for key, value in kwargs.items() if value is not None}, autocommit=True) as connection:
            connection.execute(sql.SQL("LISTEN {}").format(sql.Identifier(channel)))
            for _notification in connection.notifies(timeout=timeout_seconds, stop_after=1):
                break
    except Exception as exc:
        logger.warning("background notification wait failed type=%s", type(exc).__name__)
        time.sleep(min(timeout_seconds, 5))


def wait_for_ai_work(database_url: str, timeout_seconds: float = 60) -> None:
    wait_for_notification(database_url, "amigo_ai_work", timeout_seconds)


def wait_for_queue_event(database_url: str, timeout_seconds: float = 30) -> None:
    wait_for_notification(database_url, "amigo_queue_events", timeout_seconds)
