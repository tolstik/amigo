from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from app.ai_contracts import (
    AI_MODEL,
    AI_PROMPT_VERSION,
    AiAnalysis,
    AnalysisSnapshot,
    GatewayAnalyzeResponse,
    SnapshotFact,
    snapshot_hash,
)
from app.ai_models import AiAnalysisJob, AiAnalysisResult
from app.ai_queue import enqueue_analysis
from app.ai_worker import AiAnalysisWorker, AiGatewayClient, GatewayClientError
from app.config import Settings


NOW = datetime(2026, 8, 19, 18, 0, tzinfo=timezone.utc)


def snapshot() -> AnalysisSnapshot:
    return AnalysisSnapshot(
        source_through=NOW,
        facts=[
            SnapshotFact(
                key="activity.active_minutes_7d",
                scope="activity",
                period="7d",
                value=210,
                unit="minutes",
            )
        ],
    )


class SuccessfulGateway:
    def analyze(self, job):
        return GatewayAnalyzeResponse(
            snapshot_hash=job.snapshot_hash,
            prompt_version=AI_PROMPT_VERSION,
            model=AI_MODEL,
            generated_at=NOW,
            duration_ms=100,
            analysis=AiAnalysis(
                headline="Ритм активности",
                summary="Активность формирует достаточно устойчивый недельный ритм.",
                observations=[],
                recommendations=[],
                confidence="medium",
                limitations=[],
            ),
        )


class FailingGateway:
    def analyze(self, _job):
        raise GatewayClientError("timeout")


class UnknownCodeGateway:
    def analyze(self, _job):
        raise GatewayClientError("raw private gateway detail")


class InvalidThenSuccessfulGateway:
    def __init__(self):
        self.attempts = []

    def analyze(self, job):
        self.attempts.append(job.attempts)
        if job.attempts == 1:
            raise GatewayClientError("invalid_response")
        return GatewayAnalyzeResponse(
            snapshot_hash=job.snapshot_hash,
            prompt_version=AI_PROMPT_VERSION,
            model=AI_MODEL,
            generated_at=NOW,
            duration_ms=100,
            analysis=AiAnalysis(
                headline="Ритм активности",
                summary="Доступен недельный агрегат активности.",
                observations=[],
                recommendations=[],
                confidence="medium",
                limitations=[],
            ),
        )


class BoundedInvalidGateway:
    def __init__(self, success_attempt=None):
        self.success_attempt = success_attempt
        self.attempts = []

    def analyze(self, job):
        self.attempts.append(job.attempts)
        if job.attempts != self.success_attempt:
            raise GatewayClientError("invalid_response")
        return GatewayAnalyzeResponse(
            snapshot_hash=job.snapshot_hash,
            prompt_version=AI_PROMPT_VERSION,
            model=AI_MODEL,
            generated_at=NOW,
            duration_ms=100,
            analysis=AiAnalysis(
                headline="Ритм активности",
                summary="Доступен недельный агрегат активности.",
                observations=[],
                recommendations=[],
                confidence="medium",
                limitations=[],
            ),
        )


def settings() -> Settings:
    return Settings(
        ai_enabled=True,
        ai_lease_seconds=30,
        ai_max_attempts=2,
        ai_backoff_base_seconds=60,
        ai_stale_seconds=3600,
    )


def four_attempt_settings() -> Settings:
    return Settings(
        ai_enabled=True,
        ai_lease_seconds=30,
        ai_max_attempts=4,
        ai_backoff_base_seconds=60,
        ai_stale_seconds=3600,
    )


def test_worker_never_exceeds_gateway_attempt_contract():
    configured = four_attempt_settings().model_copy(update={"ai_max_attempts": 99})

    worker = AiAnalysisWorker(configured, gateway=SuccessfulGateway())

    assert worker.max_attempts == 4


def test_ai_worker_persists_validated_result(db):
    enqueue_analysis(db, snapshot(), trigger="activity", now=NOW, debounce_seconds=0)
    worker = AiAnalysisWorker(settings(), gateway=SuccessfulGateway())
    assert worker.process_one(db, NOW) is True
    job = db.query(AiAnalysisJob).one()
    result = db.query(AiAnalysisResult).one()
    assert job.status == "succeeded"
    assert result.analysis["headline"] == "Ритм активности"


def test_worker_offers_background_analysis_after_three_assistant_jobs(db, monkeypatch):
    worker = AiAnalysisWorker(
        settings(),
        gateway=SuccessfulGateway(),
        lab_gateway=object(),
    )
    calls: list[str] = []
    analysis_available = False

    def no_lab(*_args):
        calls.append("lab")
        return False

    def assistant(*_args):
        calls.append("assistant")
        return True

    def analysis(*_args):
        calls.append("analysis")
        return analysis_available

    monkeypatch.setattr("app.ai_worker.process_lab_job", no_lab)
    monkeypatch.setattr("app.ai_worker.process_assistant_job", assistant)
    monkeypatch.setattr(worker, "process_analysis", analysis)

    for _ in range(3):
        assert worker.process_one(db, NOW) is True
    assert calls == ["lab", "assistant"] * 3

    analysis_available = True
    assert worker.process_one(db, NOW) is True
    assert calls[-2:] == ["lab", "analysis"]

    assert worker.process_one(db, NOW) is True
    assert calls[-2:] == ["lab", "assistant"]


def test_worker_interleaves_guide_backfill_with_assistant_turns(db, monkeypatch):
    worker = AiAnalysisWorker(
        settings(),
        gateway=SuccessfulGateway(),
        lab_gateway=object(),
    )
    calls: list[str] = []
    remaining_guides = 2

    def no_document(*_args):
        return False

    def guide(*_args):
        nonlocal remaining_guides
        calls.append("guide")
        if remaining_guides == 0:
            return False
        remaining_guides -= 1
        return True

    def assistant(*_args):
        calls.append("assistant")
        return True

    monkeypatch.setattr("app.ai_worker.process_lab_job", no_document)
    monkeypatch.setattr("app.ai_worker.process_study_job", no_document)
    monkeypatch.setattr("app.ai_worker.process_analyte_guide_job", guide)
    monkeypatch.setattr("app.ai_worker.process_assistant_job", assistant)
    monkeypatch.setattr(worker, "process_analysis", lambda *_args: False)

    assert worker.process_one(db, NOW) is True
    assert worker.process_one(db, NOW) is True
    assert worker.process_one(db, NOW) is True

    assert calls == ["guide", "assistant", "guide"]


def test_ai_worker_retries_with_sanitized_error_code(db):
    enqueue_analysis(db, snapshot(), trigger="activity", now=NOW, debounce_seconds=0)
    worker = AiAnalysisWorker(settings(), gateway=FailingGateway())
    assert worker.process_one(db, NOW) is True
    job = db.query(AiAnalysisJob).one()
    assert job.status == "pending"
    assert job.last_error_code == "timeout"
    assert job.attempts == 1


def test_ai_worker_uses_next_claim_for_bounded_validation_retry(db):
    enqueue_analysis(db, snapshot(), trigger="activity", now=NOW, debounce_seconds=0)
    gateway = InvalidThenSuccessfulGateway()
    worker = AiAnalysisWorker(settings(), gateway=gateway)

    assert worker.process_one(db, NOW) is True
    job = db.query(AiAnalysisJob).one()
    assert job.status == "pending"
    assert job.last_error_code == "invalid_response"

    retry_at = NOW + timedelta(seconds=60)
    assert worker.process_one(db, retry_at) is True
    db.refresh(job)
    assert job.status == "succeeded"
    assert job.attempts == 2
    assert gateway.attempts == [1, 2]


def test_explicit_enqueue_accelerates_three_failures_then_fourth_attempt_succeeds(db):
    current_snapshot = snapshot()
    enqueue_analysis(db, current_snapshot, trigger="manual", now=NOW, debounce_seconds=0)
    gateway = BoundedInvalidGateway(success_attempt=4)
    worker = AiAnalysisWorker(four_attempt_settings(), gateway=gateway)

    for offset in range(4):
        current = NOW + timedelta(seconds=offset)
        assert worker.process_one(db, current) is True
        job = db.query(AiAnalysisJob).one()
        if offset < 3:
            assert job.status == "pending"
            enqueue_analysis(
                db,
                current_snapshot,
                trigger="manual",
                now=current,
                debounce_seconds=0,
                retry_terminal=True,
            )

    db.refresh(job)
    assert job.status == "succeeded"
    assert job.attempts == 4
    assert gateway.attempts == [1, 2, 3, 4]
    assert db.query(AiAnalysisResult).count() == 1


def test_four_invalid_attempts_fail_without_a_fifth_gateway_call(db):
    current_snapshot = snapshot()
    enqueue_analysis(db, current_snapshot, trigger="manual", now=NOW, debounce_seconds=0)
    gateway = BoundedInvalidGateway()
    worker = AiAnalysisWorker(four_attempt_settings(), gateway=gateway)

    for offset in range(4):
        current = NOW + timedelta(seconds=offset)
        assert worker.process_one(db, current) is True
        job = db.query(AiAnalysisJob).one()
        if offset < 3:
            enqueue_analysis(
                db,
                current_snapshot,
                trigger="manual",
                now=current,
                debounce_seconds=0,
                retry_terminal=True,
            )

    db.refresh(job)
    assert job.status == "failed"
    assert job.attempts == 4
    assert worker.process_one(db, NOW + timedelta(minutes=10)) is False
    assert gateway.attempts == [1, 2, 3, 4]
    assert db.query(AiAnalysisResult).count() == 0


def test_ai_worker_normalizes_unknown_error_before_log_and_database(db, caplog):
    enqueue_analysis(db, snapshot(), trigger="activity", now=NOW, debounce_seconds=0)
    worker = AiAnalysisWorker(settings(), gateway=UnknownCodeGateway())

    with caplog.at_level("WARNING", logger="amigo.ai.worker"):
        assert worker.process_one(db, NOW) is True

    job = db.query(AiAnalysisJob).one()
    assert job.last_error_code == "internal"
    assert "raw private gateway detail" not in caplog.text
    assert "code=internal" in caplog.text


class RecordingHttp:
    def __init__(self):
        self.payload = None

    def post(self, _url, *, json):
        self.payload = json
        return httpx.Response(
            200,
            json=GatewayAnalyzeResponse(
                snapshot_hash=json["snapshot_hash"],
                prompt_version=AI_PROMPT_VERSION,
                model=AI_MODEL,
                generated_at=NOW,
                duration_ms=100,
                analysis=AiAnalysis(
                    headline="Ритм активности",
                    summary="Доступен недельный агрегат активности.",
                    observations=[],
                    recommendations=[],
                    confidence="medium",
                    limitations=[],
                ),
            ).model_dump(mode="json"),
        )


def test_gateway_client_sends_bounded_claim_attempt(db):
    queued = enqueue_analysis(
        db,
        snapshot(),
        trigger="activity",
        now=NOW,
        debounce_seconds=0,
    )
    queued.attempts = 2
    db.commit()
    http = RecordingHttp()

    AiGatewayClient(settings(), http=http).analyze(queued)

    assert http.payload["attempt"] == 2

    queued.attempts = 999
    db.commit()
    AiGatewayClient(settings(), http=http).analyze(queued)
    assert http.payload["attempt"] == 4


def test_gateway_client_does_not_propagate_unknown_gateway_detail():
    class UnknownDetailHttp:
        def post(self, _url, *, json):
            return httpx.Response(502, json={"detail": "private response body"})

    job = AiAnalysisJob(
        snapshot_hash=snapshot_hash(snapshot()),
        snapshot=snapshot().model_dump(mode="json"),
        prompt_version=AI_PROMPT_VERSION,
        model=AI_MODEL,
        attempts=1,
    )

    try:
        AiGatewayClient(settings(), http=UnknownDetailHttp()).analyze(job)
    except GatewayClientError as exc:
        assert exc.code == "gateway_unavailable"
        assert "private response body" not in str(exc)
    else:
        raise AssertionError("unknown gateway detail must be rejected")
