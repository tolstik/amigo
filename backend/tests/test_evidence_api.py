from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.ai_contracts import (
    AI_MODEL,
    AI_PROMPT_VERSION,
    AiAnalysis,
    AiObservation,
    AiRecommendation,
    AnalysisSnapshot,
    GatewayAnalyzeResponse,
    SnapshotFact,
)
from app.ai_queue import claim_analysis_job, complete_analysis_job, enqueue_analysis
from app.ai_models import AiAnalysisResult
from app.auth import require_session
from app.db import get_db
from app.main import app


NOW = datetime(2026, 8, 24, 12, tzinfo=timezone.utc)


def _complete_cached_analysis(db):
    snapshot = AnalysisSnapshot(
        source_through=NOW,
        facts=[
            SnapshotFact(
                key="weight.change28d",
                scope="weight",
                period="28d",
                value=-1.25,
                unit="kg",
            )
        ],
    )
    enqueue_analysis(
        db,
        snapshot,
        trigger="measurement",
        now=NOW,
        debounce_seconds=0,
    )
    job = claim_analysis_job(db, now=NOW)
    assert job is not None
    response = GatewayAnalyzeResponse(
        snapshot_hash=job.snapshot_hash,
        prompt_version=AI_PROMPT_VERSION,
        model=AI_MODEL,
        generated_at=NOW,
        duration_ms=1,
        analysis=AiAnalysis(
            headline="Тренд веса",
            summary="Динамика рассчитана по сохранённым измерениям.",
            observations=[
                AiObservation(
                    title="Изменение за 28 дней",
                    text="Сглаженная динамика веса направлена вниз.",
                    scope="weight",
                    tone="positive",
                    evidence_keys=["weight.change28d"],
                )
            ],
            recommendations=[
                AiRecommendation(
                    title="Сверить динамику",
                    text="Сверяйте изменение веса раз в неделю.",
                    scope="weight",
                    evidence_keys=["weight.change28d"],
                )
            ],
            confidence="high",
            limitations=[],
        ),
    )
    complete_analysis_job(db, job, response)
    return job


def test_ai_endpoints_expose_snapshot_backed_stable_evidence(db, add_group):
    job = _complete_cached_analysis(db)
    # A later source measurement must not rewrite evidence for the completed
    # result. The descriptor is reconstructed from this job's exact snapshot.
    add_group("later-weight", NOW, {"weight": (99.9, "kg")})

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_session] = lambda: object()
    try:
        with TestClient(app) as client:
            analysis = client.get("/api/v1/ai-analysis")
            insights = client.get("/api/v1/insights")
            assistant = client.get("/api/v1/assistant/messages")
    finally:
        app.dependency_overrides.clear()

    assert analysis.status_code == 200
    assert analysis.json()["status"] == "fresh"
    persisted = db.query(AiAnalysisResult).filter_by(job_id=job.id).one()
    assert analysis.json()["analysis_id"] == persisted.id
    descriptor = analysis.json()["evidence"]["weight.change28d"]
    assert descriptor == {
        "key": "weight.change28d",
        "kind": "fact",
        "metric": "weight",
        "label": "Вес",
        "value": -1.25,
        "unit": "kg",
        "date": None,
        "period": "28d",
        "target": {"path": "/progress", "available": True},
    }
    assert insights.status_code == 200
    assert insights.json()["items"][0]["evidence_ids"] == ["weight.change28d"]
    assert insights.json()["evidence"] == {"weight.change28d": descriptor}
    assert assistant.status_code == 200
    assert assistant.json()["analysis_id"] == persisted.id
    assert assistant.json()["recommendations"] == [
        {
            "id": "recommendation-1",
            "title": "Сверить динамику",
            "text": "Сверяйте изменение веса раз в неделю.",
            "evidence_ids": ["weight.change28d"],
        }
    ]
    assert assistant.json()["evidence"] == {"weight.change28d": descriptor}
    assert db.get(type(job), job.id).snapshot["facts"][0]["value"] == -1.25


def test_corrupt_persisted_snapshot_fails_closed(db):
    job = _complete_cached_analysis(db)
    job.snapshot = {
        **job.snapshot,
        "facts": [{**job.snapshot["facts"][0], "value": 777}],
    }
    db.commit()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_session] = lambda: object()
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/ai-analysis")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert response.json()["analysis_id"] is None
    assert response.json()["evidence"] == {}


def test_cached_analysis_with_unknown_evidence_fails_closed(db):
    job = _complete_cached_analysis(db)
    result = db.query(AiAnalysisResult).filter_by(job_id=job.id).one()
    result.analysis = {
        **result.analysis,
        "observations": [
            {
                **result.analysis["observations"][0],
                "evidence_keys": ["weight.not_in_the_snapshot"],
            }
        ],
    }
    db.commit()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_session] = lambda: object()
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/ai-analysis")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert response.json()["analysis_id"] is None
    assert response.json()["evidence"] == {}
