from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.ai_contracts import (
    AI_MODEL,
    AI_PROMPT_VERSION,
    AiAnalysis,
    AnalysisSnapshot,
    GatewayAnalyzeRequest,
    GatewayAnalyzeResponse,
    SnapshotFact,
    snapshot_hash,
)
from app.ai_gateway import AiGatewaySettings, CodexRunner, build_analysis_prompt, create_app


NOW = datetime(2026, 8, 19, 18, 0, tzinfo=timezone.utc)


def request_payload() -> GatewayAnalyzeRequest:
    snapshot = AnalysisSnapshot(
        source_through=NOW,
        facts=[
            SnapshotFact(
                key="activity.steps_7d",
                scope="activity",
                period="7d",
                value=7200,
                unit="steps",
            )
        ],
    )
    return GatewayAnalyzeRequest(snapshot_hash=snapshot_hash(snapshot), snapshot=snapshot)


def analysis_dict() -> dict:
    return {
        "headline": "Активность стабильна",
        "summary": "Данных достаточно для осторожного наблюдения за текущим ритмом.",
        "observations": [],
        "recommendations": [],
        "confidence": "medium",
        "limitations": [],
    }


def test_codex_runner_uses_fixed_safe_arguments_stdin_and_clean_environment(
    monkeypatch, tmp_path
):
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    binary_hash = sha256(Path("/bin/true").read_bytes()).hexdigest()
    settings = AiGatewaySettings(
        codex_binary="/bin/true",
        codex_expected_sha256=binary_hash,
        codex_home=codex_home,
        codex_work_dir=tmp_path / "work",
    )
    captured = {}

    class FakeProcess:
        pid = 999_999
        returncode = 0

        def __init__(self, command, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs

        def communicate(self, input=None, timeout=None):
            captured["stdin"] = input
            captured["timeout"] = timeout
            command = captured["command"]
            output = Path(command[command.index("--output-last-message") + 1])
            output.write_text(json.dumps(analysis_dict()), encoding="utf-8")
            return (None, None)

    monkeypatch.setattr("app.ai_gateway.subprocess.Popen", FakeProcess)
    monkeypatch.setenv("DATABASE_URL", "postgresql://contains-secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "contains-secret")
    result = CodexRunner(settings).run(request_payload())

    command = captured["command"]
    assert command[0] == "/bin/true"
    assert ["--model", AI_MODEL] == command[command.index("--model") : command.index("--model") + 2]
    assert "--sandbox" in command and command[command.index("--sandbox") + 1] == "read-only"
    assert "--ask-for-approval" in command and command[command.index("--ask-for-approval") + 1] == "never"
    assert "--ephemeral" in command
    assert "--ignore-user-config" in command
    assert "--output-schema" in command
    assert "--search" not in command
    assert not any("dangerously" in value for value in command)
    assert 'shell_environment_policy.inherit="none"' in command
    assert command[-1] == "-"
    assert b"Snapshot JSON" in captured["stdin"]
    assert "weight" not in " ".join(command)
    assert "DATABASE_URL" not in captured["kwargs"]["env"]
    assert "TELEGRAM_BOT_TOKEN" not in captured["kwargs"]["env"]
    assert captured["kwargs"]["start_new_session"] is True
    assert result.model == AI_MODEL


def test_codex_runner_rejects_binary_with_unexpected_hash(tmp_path):
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    settings = AiGatewaySettings(
        codex_binary="/bin/true",
        codex_expected_sha256="0" * 64,
        codex_home=codex_home,
    )
    assert CodexRunner(settings).resolved_binary() is None


def test_analysis_prompt_demands_specific_actions_bounded_medical_guidance_and_dating():
    prompt = build_analysis_prompt(request_payload())

    assert "concrete action" in prompt
    assert "realistic cadence or review period" in prompt
    assert "scope \"medical\" or" in prompt
    assert "include at least one bounded" in prompt
    assert "standardized repeat-measurement plan" in prompt
    assert "start/stop/change medication" in prompt
    assert "fixed calorie target" in prompt
    assert "not an emergency-triage tool" in prompt
    assert "persistent logged pattern" in prompt
    assert "Never recalculate or classify BMI" in prompt
    assert "`observed_on` is the actual measurement date" in prompt
    assert "never imply that it is fresher than that date" in prompt


class FakeRunner:
    def resolved_binary(self):
        return "/bin/true"

    def run(self, request):
        return GatewayAnalyzeResponse(
            snapshot_hash=request.snapshot_hash,
            prompt_version=AI_PROMPT_VERSION,
            model=AI_MODEL,
            generated_at=NOW,
            duration_ms=10,
            analysis=AiAnalysis.model_validate(analysis_dict()),
        )


def test_gateway_sanitizes_validation_errors_and_returns_valid_contract(tmp_path):
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    settings = AiGatewaySettings(codex_home=codex_home)
    client = TestClient(create_app(settings, FakeRunner()))

    bad = client.post("/analyze", json={"raw_health_secret": "must-not-echo"})
    assert bad.status_code == 422
    assert bad.json() == {"detail": "invalid_request"}
    assert "must-not-echo" not in bad.text

    request = request_payload()
    response = client.post("/analyze", json=request.model_dump(mode="json"))
    assert response.status_code == 200
    assert response.json()["snapshot_hash"] == request.snapshot_hash
    assert response.headers["cache-control"] == "no-store"
    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json()["model"] == AI_MODEL
