from __future__ import annotations

from argparse import Namespace
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone

from app import cli
from app.ai_contracts import (
    AI_MODEL,
    AI_PROMPT_VERSION,
    AiAnalysis,
    AnalysisSnapshot,
    GatewayAnalyzeResponse,
    SnapshotFact,
)
from app.ai_queue import claim_analysis_job, complete_analysis_job, enqueue_analysis
from app.config import Settings


NOW = datetime(2026, 8, 19, 18, 0, tzinfo=timezone.utc)


def analysis_snapshot(value: int = 7200) -> AnalysisSnapshot:
    return AnalysisSnapshot(
        source_through=NOW,
        facts=[
            SnapshotFact(
                key="activity.steps_7d",
                scope="activity",
                period="7d",
                value=value,
                unit="steps",
            )
        ],
    )


def test_current_ai_analysis_ready_requires_exact_current_sol_v2_result(db, monkeypatch):
    current = analysis_snapshot()
    monkeypatch.setattr(cli, "build_analysis_snapshot", lambda *_args, **_kwargs: current)
    settings = Settings(ai_enabled=True)

    assert cli.current_ai_analysis_ready(db, settings, NOW) is False
    enqueue_analysis(db, current, trigger="manual", now=NOW, debounce_seconds=0)
    job = claim_analysis_job(db, now=NOW)
    assert job is not None
    complete_analysis_job(
        db,
        job,
        GatewayAnalyzeResponse(
            snapshot_hash=job.snapshot_hash,
            prompt_version=AI_PROMPT_VERSION,
            model=AI_MODEL,
            generated_at=NOW,
            duration_ms=100,
            analysis=AiAnalysis(
                headline="Недельная активность",
                summary="Доступен недельный агрегат шагов.",
                observations=[],
                recommendations=[],
                confidence="medium",
                limitations=[],
            ),
        ),
    )

    assert cli.current_ai_analysis_ready(db, settings, NOW) is True

    monkeypatch.setattr(
        cli,
        "build_analysis_snapshot",
        lambda *_args, **_kwargs: analysis_snapshot(7300),
    )
    assert cli.current_ai_analysis_ready(db, settings, NOW) is False


def test_current_ai_analysis_ready_fails_closed_when_empty_snapshot_time_changes(
    db, monkeypatch
):
    def empty_snapshot(_db, _tz, now, *, user_height_cm):
        return AnalysisSnapshot(
            source_through=now,
            facts=[
                SnapshotFact(
                    key="quality.no_health_data",
                    scope="quality",
                    period="current",
                    value=True,
                    unit="boolean",
                    observed_on=now.date(),
                )
            ],
        )

    monkeypatch.setattr(cli, "build_analysis_snapshot", empty_snapshot)
    settings = Settings(ai_enabled=True)
    initial = empty_snapshot(db, settings.tz, NOW, user_height_cm=176)
    enqueue_analysis(db, initial, trigger="manual", now=NOW, debounce_seconds=0)
    job = claim_analysis_job(db, now=NOW)
    assert job is not None
    complete_analysis_job(
        db,
        job,
        GatewayAnalyzeResponse(
            snapshot_hash=job.snapshot_hash,
            generated_at=NOW,
            duration_ms=100,
            analysis=AiAnalysis(
                headline="Данных пока нет",
                summary="Доступен только технический статус.",
                observations=[],
                recommendations=[],
                confidence="low",
                limitations=["Нет измерений для анализа."],
            ),
        ),
    )

    assert cli.current_ai_analysis_ready(db, settings, NOW) is True
    assert cli.current_ai_analysis_ready(db, settings, NOW + timedelta(seconds=1)) is False


def test_prepare_current_ai_retry_reclaims_only_exact_processing_job(db, monkeypatch):
    current = analysis_snapshot()
    monkeypatch.setattr(cli, "build_analysis_snapshot", lambda *_args, **_kwargs: current)
    settings = Settings(ai_enabled=True, ai_max_attempts=4)
    enqueue_analysis(db, current, trigger="manual", now=NOW, debounce_seconds=0)
    job = claim_analysis_job(db, now=NOW, lease_seconds=180)
    assert job is not None
    assert job.status == "processing"

    assert cli.prepare_current_ai_retry(db, settings, NOW + timedelta(seconds=1)) is True

    db.refresh(job)
    assert job.status == "pending"
    assert job.attempts == 1
    assert job.lease_until is None
    assert job.last_error_code == "lease_expired"
    available_at = (
        job.available_at.replace(tzinfo=timezone.utc)
        if job.available_at.tzinfo is None
        else job.available_at
    )
    assert available_at == NOW + timedelta(seconds=1)


def test_prepare_current_ai_retry_resets_exhausted_exact_processing_job(db, monkeypatch):
    current = analysis_snapshot()
    monkeypatch.setattr(cli, "build_analysis_snapshot", lambda *_args, **_kwargs: current)
    settings = Settings(ai_enabled=True, ai_max_attempts=4)
    enqueue_analysis(db, current, trigger="manual", now=NOW, debounce_seconds=0)
    job = claim_analysis_job(db, now=NOW, lease_seconds=180)
    assert job is not None
    job.attempts = 4
    db.commit()

    assert cli.prepare_current_ai_retry(db, settings, NOW + timedelta(seconds=1)) is True

    db.refresh(job)
    assert job.status == "pending"
    assert job.attempts == 0
    assert job.lease_until is None
    assert job.last_error_code is None


def test_ai_ready_cli_prints_only_fixed_success_and_silent_failure(
    monkeypatch, capsys
):
    settings = Settings(ai_enabled=True)
    session = object()
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "SessionLocal", lambda: nullcontext(session))

    monkeypatch.setattr(cli, "current_ai_analysis_ready", lambda *_args: False)
    assert cli.execute(Namespace(command="ai-ready")) == 75
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "AI analysis not ready\n"

    monkeypatch.setattr(cli, "current_ai_analysis_ready", lambda *_args: True)
    assert cli.execute(Namespace(command="ai-ready")) == 0
    captured = capsys.readouterr()
    assert captured.out == "AI analysis ready\n"
    assert captured.err == ""


def test_ai_retry_cli_requires_explicit_worker_stop_confirmation(monkeypatch, capsys):
    settings = Settings(ai_enabled=True)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    args = cli.build_parser().parse_args(["ai-retry-current"])

    assert args.worker_stopped is False
    assert cli.execute(args) == 64
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "AI worker stop confirmation required\n"


def test_ai_retry_cli_prints_only_fixed_result(monkeypatch, capsys):
    settings = Settings(ai_enabled=True)
    session = object()
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "SessionLocal", lambda: nullcontext(session))
    monkeypatch.setattr(cli, "prepare_current_ai_retry", lambda *_args: True)

    assert (
        cli.execute(Namespace(command="ai-retry-current", worker_stopped=True))
        == 0
    )
    captured = capsys.readouterr()
    assert captured.out == "AI analysis retry prepared\n"
    assert captured.err == ""
