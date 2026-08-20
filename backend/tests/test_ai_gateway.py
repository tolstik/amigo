from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.ai_contracts import (
    AI_MODEL,
    AI_PROMPT_VERSION,
    MAX_ANALYSIS_REQUEST_ATTEMPT,
    AiAnalysis,
    AnalysisSnapshot,
    GatewayAnalyzeRequest,
    GatewayAnalyzeResponse,
    SnapshotFact,
    SnapshotPoint,
    SnapshotSeries,
    snapshot_hash,
)
from app.ai_gateway import (
    DISABLED_CODEX_FEATURES,
    AiGatewaySettings,
    CodexRunner,
    GatewayExecutionError,
    build_analysis_output_schema,
    build_analysis_prompt,
    build_chat_output_schema,
    build_chat_prompt,
    build_lab_output_schema,
    create_app,
)
from app.lab_contracts import GatewayChatRequest


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
            schema = Path(command[command.index("--output-schema") + 1])
            captured["schema"] = json.loads(schema.read_text(encoding="utf-8"))
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
    assert 'web_search="disabled"' in command
    for feature in DISABLED_CODEX_FEATURES:
        positions = [
            index
            for index, value in enumerate(command[:-1])
            if value == "--disable" and command[index + 1] == feature
        ]
        assert len(positions) == 1
    assert command[-1] == "-"
    assert b"Snapshot JSON" in captured["stdin"]
    assert "weight" not in " ".join(command)
    assert "DATABASE_URL" not in captured["kwargs"]["env"]
    assert "TELEGRAM_BOT_TOKEN" not in captured["kwargs"]["env"]
    assert captured["kwargs"]["start_new_session"] is True
    for definition in ("AiObservation", "AiRecommendation"):
        assert captured["schema"]["$defs"][definition]["properties"]["evidence_keys"][
            "items"
        ]["enum"] == ["activity.steps_7d"]
    assert result.model == AI_MODEL


def test_app_server_command_disables_all_tool_producing_features(tmp_path):
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    binary_hash = sha256(Path("/bin/true").read_bytes()).hexdigest()
    runner = CodexRunner(
        AiGatewaySettings(
            codex_binary="/bin/true",
            codex_expected_sha256=binary_hash,
            codex_home=codex_home,
        )
    )

    command = runner._app_server_command("/bin/true")

    assert 'web_search="disabled"' in command
    assert 'shell_environment_policy.inherit="none"' in command
    for feature in DISABLED_CODEX_FEATURES:
        assert any(
            value == "--disable" and command[index + 1] == feature
            for index, value in enumerate(command[:-1])
        )


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
    assert "Final contract checklist" in prompt
    assert 'Allowed evidence keys: ["activity.steps_7d"]' in prompt
    assert "including negated caveats" in prompt
    assert "validator-blocked stems" in prompt
    assert "`диагноз`" in prompt
    assert "Queue retry correction" not in prompt


def test_retry_prompt_adds_fixed_correction_without_prior_output():
    request = request_payload().model_copy(update={"attempt": 2})

    prompt = build_analysis_prompt(request)

    assert f"Queue retry correction (2/{MAX_ANALYSIS_REQUEST_ATTEMPT})" in prompt
    assert "Generate a complete new object" in prompt
    assert "do not assume or reconstruct earlier output" in prompt
    assert "prior candidate text" not in prompt


def test_request_specific_schema_enumerates_evidence_and_requires_medical_recommendation():
    medical_evidence = "pressure." + "systolic7d"
    snapshot = AnalysisSnapshot(
        source_through=NOW,
        facts=[
            SnapshotFact(
                key="weight.change28d",
                scope="weight",
                period="28d",
                value=-1.2,
                unit="kg",
            ),
            SnapshotFact(
                key=medical_evidence,
                scope="pressure",
                period="7d",
                value=128,
                unit="mmhg",
            ),
        ],
        series=[
            SnapshotSeries(
                key="heart.resting90d",
                scope="heart",
                unit="bpm",
                points=[SnapshotPoint(day="2026-08-19", value=62)],
            )
        ],
    )

    schema = build_analysis_output_schema(snapshot)
    expected = [
        "heart.resting90d",
        medical_evidence,
        "weight.change28d",
    ]
    for definition in ("AiObservation", "AiRecommendation"):
        assert schema["$defs"][definition]["properties"]["evidence_keys"]["items"][
            "enum"
        ] == expected
    assert schema["properties"]["recommendations"]["minItems"] == 1


def test_request_specific_schema_allows_empty_recommendations_without_medical_evidence():
    schema = build_analysis_output_schema(request_payload().snapshot)

    assert "minItems" not in schema["properties"]["recommendations"]


def _schema_nodes(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _schema_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _schema_nodes(child)


def test_lab_output_schema_meets_strict_structured_outputs_contract():
    schema = build_lab_output_schema()

    for node in _schema_nodes(schema):
        assert "default" not in node
        if node.get("type") == "object":
            assert set(node.get("required", [])) == set(node.get("properties", {}))
            assert node.get("additionalProperties") is False
    result_properties = schema["$defs"]["ExtractedLabResult"]["properties"]
    for field in ("value_numeric", "reference_low", "reference_high"):
        assert result_properties[field] == {
            "anyOf": [{"type": "number"}, {"type": "null"}]
        }


def test_chat_output_schema_enumerates_exact_allowed_evidence():
    request = GatewayChatRequest(
        message_id="00000000-0000-0000-0000-000000000001",
        prompt="Synthetic context",
        allowed_evidence_keys=["weight.latest", "activity.steps", "weight.latest"],
    )

    schema = build_chat_output_schema(request)

    assert schema["$defs"]["ChatSegment"]["properties"]["evidence_keys"][
        "items"
    ]["enum"] == ["activity.steps", "weight.latest"]


def test_chat_prompt_matches_validator_and_adds_retry_correction():
    request = GatewayChatRequest(
        message_id="00000000-0000-0000-0000-000000000001",
        attempt=2,
        prompt="Synthetic context",
        allowed_evidence_keys=["quality.runtime_smoke"],
    )

    prompt = build_chat_prompt(request)

    assert "Retry correction (2/2)" in prompt
    assert "evidence-backed hypotheses" in prompt
    assert "definitive diagnosis" in prompt
    assert "Medical vocabulary is allowed" in prompt
    assert "HTML, Markdown, links" in prompt
    assert 'Allowed evidence keys: ["quality.runtime_smoke"]' in prompt
    assert "Attempt: 2/2" in prompt


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


def test_codex_runner_leaves_invalid_output_for_queue_level_retry(monkeypatch, tmp_path):
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    binary_hash = sha256(Path("/bin/true").read_bytes()).hexdigest()
    settings = AiGatewaySettings(
        codex_binary="/bin/true",
        codex_expected_sha256=binary_hash,
        codex_home=codex_home,
        codex_work_dir=tmp_path / "work",
    )
    calls = 0

    class InvalidProcess:
        pid = 999_999
        returncode = 0

        def __init__(self, command, **_kwargs):
            self.command = command

        def communicate(self, input=None, timeout=None):
            nonlocal calls
            calls += 1
            output = Path(self.command[self.command.index("--output-last-message") + 1])
            invalid = analysis_dict()
            invalid["headline"] = "Это медицинский диагноз"
            output.write_text(json.dumps(invalid), encoding="utf-8")
            return (None, None)

    monkeypatch.setattr("app.ai_gateway.subprocess.Popen", InvalidProcess)

    with pytest.raises(GatewayExecutionError, match="invalid_response"):
        CodexRunner(settings).run(request_payload())

    assert calls == 1


class UnsafeErrorRunner:
    def resolved_binary(self):
        return "/bin/true"

    def run(self, _request):
        raise GatewayExecutionError("private generated output")


def test_gateway_normalizes_unknown_execution_code_before_http_and_logs(
    caplog, tmp_path
):
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    client = TestClient(
        create_app(AiGatewaySettings(codex_home=codex_home), UnsafeErrorRunner())
    )

    with caplog.at_level("WARNING", logger="amigo.ai.gateway"):
        response = client.post(
            "/analyze",
            json=request_payload().model_dump(mode="json"),
        )

    assert response.status_code == 500
    assert response.json() == {"detail": "internal"}
    assert "private generated output" not in caplog.text
    assert "code=internal" in caplog.text
