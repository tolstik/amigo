from __future__ import annotations

from datetime import datetime, timezone

from app.ai_contracts import (
    AI_MODEL,
    AI_PROMPT_VERSION,
    AiAnalysis,
    AnalysisSnapshot,
    GatewayAnalyzeResponse,
    SnapshotFact,
)
from app.ai_models import AiAnalysisJob, AiAnalysisResult
from app.ai_queue import enqueue_analysis
from app.ai_worker import AiAnalysisWorker, GatewayClientError
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


def settings() -> Settings:
    return Settings(
        ai_enabled=True,
        ai_lease_seconds=30,
        ai_max_attempts=2,
        ai_backoff_base_seconds=60,
        ai_stale_seconds=3600,
    )


def test_ai_worker_persists_validated_result(db):
    enqueue_analysis(db, snapshot(), trigger="activity", now=NOW, debounce_seconds=0)
    worker = AiAnalysisWorker(settings(), gateway=SuccessfulGateway())
    assert worker.process_one(db, NOW) is True
    job = db.query(AiAnalysisJob).one()
    result = db.query(AiAnalysisResult).one()
    assert job.status == "succeeded"
    assert result.analysis["headline"] == "Ритм активности"


def test_ai_worker_retries_with_sanitized_error_code(db):
    enqueue_analysis(db, snapshot(), trigger="activity", now=NOW, debounce_seconds=0)
    worker = AiAnalysisWorker(settings(), gateway=FailingGateway())
    assert worker.process_one(db, NOW) is True
    job = db.query(AiAnalysisJob).one()
    assert job.status == "pending"
    assert job.last_error_code == "timeout"
    assert job.attempts == 1
