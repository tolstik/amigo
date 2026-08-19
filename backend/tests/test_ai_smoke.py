from datetime import datetime, timezone

import httpx
import pytest

from app.ai_contracts import GatewayAnalyzeResponse
from app.ai_smoke import run_smoke, synthetic_request


def _response_payload(snapshot_hash: str) -> dict:
    return GatewayAnalyzeResponse(
        snapshot_hash=snapshot_hash,
        generated_at=datetime(2026, 8, 19, 8, 0, 1, tzinfo=timezone.utc),
        duration_ms=250,
        analysis={
            "headline": "Проверка контура выполнена",
            "summary": "Синтетический сигнал обработан.",
            "observations": [],
            "recommendations": [],
            "confidence": "low",
            "limitations": ["Это техническая проверка без данных о здоровье."],
        },
    ).model_dump(mode="json")


def test_synthetic_request_contains_no_health_observations() -> None:
    request = synthetic_request(datetime(2026, 8, 19, 8, tzinfo=timezone.utc))

    assert [fact.key for fact in request.snapshot.facts] == ["quality.runtime_smoke"]
    assert request.snapshot.series == []


def test_run_smoke_validates_gateway_response(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(*_args, **kwargs) -> httpx.Response:
        payload = kwargs["json"]
        return httpx.Response(200, json=_response_payload(payload["snapshot_hash"]))

    monkeypatch.setattr("app.ai_smoke.httpx.post", fake_post)

    result = run_smoke()

    assert result.analysis.headline == "Проверка контура выполнена"


def test_run_smoke_fails_closed_on_invalid_output(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(*_args, **_kwargs) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    monkeypatch.setattr("app.ai_smoke.httpx.post", fake_post)

    with pytest.raises(RuntimeError, match="schema validation"):
        run_smoke()
