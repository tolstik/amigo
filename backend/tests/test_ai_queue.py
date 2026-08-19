from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.ai_contracts import (
    AI_MODEL,
    AI_PROMPT_VERSION,
    AiAnalysis,
    AiObservation,
    AnalysisSnapshot,
    GatewayAnalyzeResponse,
    SnapshotFact,
)
from app.ai_models import AiAnalysisJob, AiAnalysisResult
from app.ai_queue import (
    claim_analysis_job,
    complete_analysis_job,
    enqueue_analysis,
    fail_analysis_job,
    latest_analysis,
    public_analysis_payload,
    recover_expired_leases,
)


NOW = datetime(2026, 8, 19, 18, 0, tzinfo=timezone.utc)


def snapshot(value: float, source_through: datetime = NOW) -> AnalysisSnapshot:
    return AnalysisSnapshot(
        source_through=source_through,
        facts=[
            SnapshotFact(
                key="weight.change_7d_kg",
                scope="weight",
                period="7d",
                value=value,
                unit="kg",
            )
        ],
    )


def response_for(job: AiAnalysisJob, generated_at: datetime = NOW) -> GatewayAnalyzeResponse:
    return GatewayAnalyzeResponse(
        snapshot_hash=job.snapshot_hash,
        prompt_version=AI_PROMPT_VERSION,
        model=AI_MODEL,
        generated_at=generated_at,
        duration_ms=500,
        analysis=AiAnalysis(
            headline="Недельный тренд",
            summary="Изменение веса сохраняет выбранное направление.",
            confidence="medium",
            observations=[
                AiObservation(
                    title="Динамика",
                    text="Сглаженный тренд снижается.",
                    scope="weight",
                    tone="positive",
                    evidence_keys=["weight.change_7d_kg"],
                )
            ],
            recommendations=[],
            limitations=[],
        ),
    )


def test_enqueue_deduplicates_and_supersedes_older_pending_job(db):
    first = enqueue_analysis(db, snapshot(-0.4), trigger="measurement", now=NOW)
    duplicate = enqueue_analysis(db, snapshot(-0.4), trigger="measurement", now=NOW)
    assert duplicate.id == first.id

    second = enqueue_analysis(
        db,
        snapshot(-0.8, NOW + timedelta(minutes=5)),
        trigger="measurement",
        now=NOW + timedelta(minutes=5),
    )
    db.refresh(first)
    assert first.status == "superseded"
    assert second.status == "pending"
    assert db.query(AiAnalysisJob).count() == 2


def test_activity_requests_are_debounced_after_latest_processing_or_result(db):
    first = enqueue_analysis(
        db,
        snapshot(-0.4),
        trigger="measurement",
        now=NOW,
        debounce_seconds=0,
    )
    first.status = "succeeded"
    db.commit()
    second_now = NOW + timedelta(minutes=10)
    second = enqueue_analysis(
        db,
        snapshot(-0.5, second_now),
        trigger="activity",
        now=second_now,
        debounce_seconds=0,
        activity_min_interval_seconds=3600,
    )
    expected = NOW + timedelta(hours=1)
    actual = second.available_at.replace(tzinfo=timezone.utc) if second.available_at.tzinfo is None else second.available_at
    assert actual == expected


def test_new_activity_snapshot_keeps_existing_pending_deadline(db):
    first = enqueue_analysis(
        db,
        snapshot(-0.4),
        trigger="activity",
        now=NOW,
        debounce_seconds=300,
    )
    expected = first.available_at
    second = enqueue_analysis(
        db,
        snapshot(-0.5, NOW + timedelta(minutes=2)),
        trigger="activity",
        now=NOW + timedelta(minutes=2),
        debounce_seconds=300,
    )
    db.refresh(first)
    assert first.status == "superseded"
    assert second.available_at == expected


def test_cached_result_stays_ready_until_new_snapshot_then_expires(db):
    enqueue_analysis(
        db,
        snapshot(-0.8),
        trigger="measurement",
        now=NOW,
        debounce_seconds=0,
    )
    job = claim_analysis_job(db, now=NOW, lease_seconds=30)
    assert job is not None
    assert job.status == "processing"
    assert job.attempts == 1
    result = complete_analysis_job(db, job, response_for(job), stale_seconds=60)
    assert isinstance(result, AiAnalysisResult)
    assert latest_analysis(db, now=NOW + timedelta(days=7)).status == "ready"
    payload = public_analysis_payload(db, now=NOW)
    assert payload["status"] == "ready"
    assert payload["analysis"]["headline"] == "Недельный тренд"

    newer_at = NOW + timedelta(days=8)
    enqueue_analysis(
        db,
        snapshot(-0.9, newer_at),
        trigger="measurement",
        now=newer_at,
        debounce_seconds=0,
        stale_seconds=60,
    )
    assert latest_analysis(db, now=newer_at + timedelta(seconds=30)).status == "stale"
    expired = latest_analysis(db, now=newer_at + timedelta(seconds=61))
    assert expired.status == "unavailable"
    assert expired.analysis is None
    assert public_analysis_payload(db, now=newer_at + timedelta(seconds=61))["analysis"] is None


def test_cached_result_that_fails_the_active_contract_is_hidden(db):
    enqueue_analysis(
        db,
        snapshot(-0.8),
        trigger="measurement",
        now=NOW,
        debounce_seconds=0,
    )
    job = claim_analysis_job(db, now=NOW, lease_seconds=30)
    assert job is not None
    result = complete_analysis_job(db, job, response_for(job))
    result.analysis = {
        **result.analysis,
        "headline": "Диагноз: гипертония",
    }
    db.commit()

    state = latest_analysis(db, now=NOW)
    assert state.status == "unavailable"
    assert state.analysis is None
    assert public_analysis_payload(db, now=NOW)["analysis"] is None


def test_failure_backoff_and_expired_lease_never_persist_raw_errors(db):
    enqueue_analysis(
        db,
        snapshot(-0.8),
        trigger="measurement",
        now=NOW,
        debounce_seconds=0,
    )
    job = claim_analysis_job(db, now=NOW, lease_seconds=30)
    assert job is not None
    fail_analysis_job(db, job, "secret raw provider response", now=NOW, backoff_base_seconds=60)
    db.refresh(job)
    assert job.status == "pending"
    assert job.last_error_code == "internal"

    job.status = "processing"
    job.lease_until = NOW - timedelta(seconds=1)
    job.attempts = 4
    db.commit()
    assert recover_expired_leases(db, now=NOW, max_attempts=4) == 1
    db.refresh(job)
    assert job.status == "failed"
    assert job.last_error_code == "lease_expired"


def test_result_completed_after_newer_enqueue_is_immediately_stale(db):
    enqueue_analysis(
        db,
        snapshot(-0.4),
        trigger="measurement",
        now=NOW,
        debounce_seconds=0,
    )
    old_job = claim_analysis_job(db, now=NOW, lease_seconds=300)
    assert old_job is not None
    newer_at = NOW + timedelta(minutes=1)
    enqueue_analysis(
        db,
        snapshot(-0.7, newer_at),
        trigger="measurement",
        now=newer_at,
        debounce_seconds=0,
        stale_seconds=120,
    )
    complete_analysis_job(db, old_job, response_for(old_job, newer_at), stale_seconds=120)
    assert latest_analysis(db, now=newer_at).status == "stale"
    assert latest_analysis(db, now=newer_at + timedelta(seconds=121)).status == "unavailable"
