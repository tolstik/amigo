"""Exercise the actual release API probe without production data or inference."""

import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "verify-production.sh"
API_PROBE = SCRIPT.read_text().split(
    "python3 - \"${API_BODY}\" \"${contract}\" <<'PY'\n", 1
)[1].split("\nPY\n", 1)[0]


def probe(tmp_path, contract, payload):
    body = tmp_path / "synthetic-api.json"
    body.write_text(json.dumps(payload))
    return subprocess.run(
        [sys.executable, "-", str(body), contract],
        input=API_PROBE, text=True, capture_output=True, check=False,
    )


def analysis(status):
    payload = {
        "status": status, "ai_generated": True,
        "analysis_id": None, "headline": None, "summary": None,
        "confidence": None, "generated_at": None, "data_as_of": None,
        "model": None, "prompt_version": None,
        "insights": [], "recommendations": [], "limitations": [], "evidence": {},
    }
    if status in {"fresh", "stale"}:
        payload.update(
            analysis_id=1, model="gpt-5.6-sol", prompt_version="amigo-health-v4",
            recommendations=[{"id": "recommendation-1", "evidence_ids": ["fact.test"]}],
            evidence={"fact.test": {"key": "fact.test", "kind": "fact", "target": {}}},
        )
    return payload


@pytest.mark.parametrize("status", ["fresh", "stale", "pending", "unavailable"])
def test_release_accepts_normal_analysis_availability(tmp_path, status):
    result = probe(tmp_path, "ai", analysis(status))
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("status", ["pending", "unavailable"])
@pytest.mark.parametrize("field,value", [
    ("headline", "Unvalidated text"),
    ("recommendations", [{"evidence_ids": ["fact.test"]}]),
    ("evidence", {"fact.test": {}}),
    ("analysis_id", 1),
])
def test_release_rejects_content_without_validated_analysis(tmp_path, status, field, value):
    payload = analysis(status)
    payload[field] = value
    assert probe(tmp_path, "ai", payload).returncode != 0


@pytest.mark.parametrize("status", ["fresh", "stale"])
@pytest.mark.parametrize("field,value", [
    ("model", "unexpected-model"),
    ("prompt_version", "old-contract"),
    ("recommendations", []),
    ("evidence", {"different": {"key": "different", "kind": "fact", "target": {}}}),
    ("evidence", {"fact.test": {"key": "wrong", "kind": "fact", "target": {}}}),
    ("evidence", {"fact.test": {"key": "fact.test", "kind": "fact"}}),
])
def test_release_still_checks_published_analysis(tmp_path, status, field, value):
    payload = analysis(status)
    payload[field] = value
    assert probe(tmp_path, "ai", payload).returncode != 0


def test_release_rejects_unknown_analysis_state(tmp_path):
    assert probe(tmp_path, "ai", analysis("broken")).returncode != 0


@pytest.mark.parametrize("has_recommendations", [False, True])
def test_assistant_accepts_optional_analysis_recommendations(tmp_path, has_recommendations):
    payload = analysis("fresh" if has_recommendations else "pending")
    payload["items"] = []
    result = probe(tmp_path, "assistant", payload)
    assert result.returncode == 0, result.stderr


def test_assistant_checks_historical_evidence_without_current_analysis(tmp_path):
    payload = analysis("pending")
    payload["items"] = [{"evidence_keys": ["fact.test"], "evidence": {}}]
    assert probe(tmp_path, "assistant", payload).returncode != 0


def test_assistant_rejects_unresolved_recommendation_evidence(tmp_path):
    payload = analysis("fresh")
    payload.update(items=[], evidence={})
    assert probe(tmp_path, "assistant", payload).returncode != 0


def test_assistant_rejects_analysis_metadata_without_recommendations(tmp_path):
    payload = analysis("pending")
    payload.update(items=[], analysis_id=1)
    assert probe(tmp_path, "assistant", payload).returncode != 0


def weight_payload():
    from datetime import date
    from app.analytics import DailyPoint, monthly_weight_points, weekly_weight_points
    points = [DailyPoint(date(2026, 8, 31), 126.0, 1), DailyPoint(date(2026, 9, 10), 124.0, 1)]
    return {"points": [], "weekly": weekly_weight_points(points, as_of=date(2026, 9, 11)),
            "monthly": monthly_weight_points(points, as_of=date(2026, 9, 11))}


def test_weight_probe_checks_full_elapsed_and_observed_plans_without_exposing_measurements(tmp_path):
    result = probe(tmp_path, "weight", weight_payload())
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert probe(tmp_path, "weight", {"points": []}).returncode != 0


@pytest.mark.parametrize("period", ["weekly", "monthly"])
@pytest.mark.parametrize("field,value", [
    ("planned_full_change_kg", None), ("planned_change_kg", -99),
    ("actual_change_kg", -99), ("period_end_date", "2026-08-01"),
    ("actual_start_date", "2026-08-15"), ("planned_end_kg", None),
])
def test_weight_probe_rejects_averages_and_invalid_period_endpoints(tmp_path, period, field, value):
    payload = weight_payload()
    payload[period][-1][field] = value
    assert probe(tmp_path, "weight", payload).returncode != 0
