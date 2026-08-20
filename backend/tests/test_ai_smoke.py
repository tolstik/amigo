from datetime import datetime, timezone
import json

import httpx
import pytest

from app.ai_contracts import GatewayAnalyzeResponse
from app.ai_smoke import (
    run_smoke,
    synthetic_chat_request,
    synthetic_lab_request,
    synthetic_request,
)
from app.lab_contracts import GatewayChatResponse, GatewayLabResponse


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


def test_synthetic_lab_and_chat_requests_contain_only_contract_fixtures() -> None:
    lab = synthetic_lab_request()
    chat = synthetic_chat_request()

    assert lab.document_id == "00000000-0000-0000-0000-000000000001"
    assert "Synthetic contract fixture" in lab.text
    assert chat.allowed_evidence_keys == ["quality.runtime_smoke"]
    assert "name" not in chat.prompt.casefold()
    assert "patient" not in lab.text.casefold()


def test_run_smoke_validates_gateway_response(monkeypatch: pytest.MonkeyPatch) -> None:
    urls = []

    def fake_post(url, **kwargs) -> httpx.Response:
        urls.append(url)
        payload = kwargs["json"]
        if url.endswith("/analyze"):
            return httpx.Response(200, json=_response_payload(payload["snapshot_hash"]))
        if url.endswith("/extract-labs"):
            response = GatewayLabResponse(
                extraction={
                    "report": {
                        "observed_on": None,
                        "laboratory": None,
                        "specimen": None,
                    },
                    "results": [],
                }
            )
            return httpx.Response(200, json=response.model_dump(mode="json"))
        response = GatewayChatResponse(
            answer={
                "segments": [
                    {
                        "text": "Синтетический сигнал доступен.",
                        "evidence_keys": ["quality.runtime_smoke"],
                    }
                ]
            }
        )
        body = json.dumps(
            {"type": "complete", "response": response.model_dump(mode="json")}
        )
        return httpx.Response(200, text=body + "\n")

    monkeypatch.setattr("app.ai_smoke.httpx.post", fake_post)

    result = run_smoke()

    assert result.analysis.headline == "Проверка контура выполнена"
    assert [url.rsplit("/", 1)[-1] for url in urls] == [
        "analyze",
        "extract-labs",
        "chat",
    ]


def test_run_smoke_fails_closed_on_invalid_output(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(*_args, **_kwargs) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    monkeypatch.setattr("app.ai_smoke.httpx.post", fake_post)

    with pytest.raises(RuntimeError, match="schema validation"):
        run_smoke()
