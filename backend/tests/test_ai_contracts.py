from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.ai_contracts import (
    AI_MODEL,
    AI_PROMPT_VERSION,
    AiAnalysis,
    AiObservation,
    AiRecommendation,
    AnalysisSnapshot,
    GatewayAnalyzeRequest,
    SnapshotFact,
    SnapshotPoint,
    SnapshotSeries,
    canonical_snapshot_json,
    snapshot_hash,
    validate_analysis_evidence,
)


NOW = datetime(2026, 8, 19, 18, 0, tzinfo=timezone.utc)


def fact(key: str, value: float, scope: str = "weight", unit: str = "kg") -> SnapshotFact:
    return SnapshotFact(
        key=key,
        scope=scope,
        period="7d",
        value=value,
        unit=unit,
    )


def test_snapshot_hash_is_canonical_for_metric_and_point_order():
    first = AnalysisSnapshot(
        source_through=NOW,
        facts=[fact("weight.latest_kg", 120), fact("weight.change_7d_kg", -0.7)],
        series=[
            SnapshotSeries(
                key="activity.steps_daily",
                scope="activity",
                unit="steps",
                points=[
                    SnapshotPoint(day="2026-08-19", value=7000),
                    SnapshotPoint(day="2026-08-18", value=6000),
                ],
            )
        ],
    )
    second = AnalysisSnapshot(
        source_through=NOW,
        facts=list(reversed(first.facts)),
        series=[
            SnapshotSeries(
                key="activity.steps_daily",
                scope="activity",
                unit="steps",
                points=list(reversed(first.series[0].points)),
            )
        ],
    )
    assert canonical_snapshot_json(first) == canonical_snapshot_json(second)
    assert snapshot_hash(first) == snapshot_hash(second)


def test_snapshot_accepts_restricted_heart_oxygen_and_vo2_metrics():
    snapshot = AnalysisSnapshot(
        source_through=NOW,
        facts=[
            fact("sleep.duration_hours", 7.2, "sleep", "hours"),
            fact("recovery.hrv_ms", 48, "heart", "milliseconds"),
            fact("heart.resting_bpm", 62, "heart", "bpm"),
            fact("recovery.spo2_percent", 97, "oxygen", "percent"),
            fact("recovery.vo2_max", 41.5, "vo2", "ml_kg_min"),
        ],
    )
    assert len(snapshot.facts) == 5


def test_codex_output_schema_requires_every_declared_property():
    schema = AiAnalysis.model_json_schema()
    objects = [schema, *schema.get("$defs", {}).values()]
    for value in objects:
        if value.get("type") == "object":
            assert set(value.get("required", [])) == set(value.get("properties", {}))


def test_gateway_request_rejects_noncanonical_hash():
    snapshot = AnalysisSnapshot(source_through=NOW, facts=[fact("weight.latest_kg", 120)])
    with pytest.raises(ValidationError):
        GatewayAnalyzeRequest(
            snapshot_hash="0" * 64,
            prompt_version=AI_PROMPT_VERSION,
            model=AI_MODEL,
            snapshot=snapshot,
        )


def test_generated_output_rejects_markup_unknown_evidence_and_medical_recommendations():
    snapshot = AnalysisSnapshot(
        source_through=NOW,
        facts=[
            fact("weight.change_7d_kg", -0.7),
            fact("pressure.systolic_7d", 128, "pressure", "mmhg"),
            fact("heart.resting_bpm", 62, "heart", "bpm"),
            fact("recovery.hrv_ms", 48, "heart", "milliseconds"),
            fact("recovery.vo2_max", 41.5, "vo2", "ml_kg_min"),
        ],
    )
    with pytest.raises(ValidationError):
        AiAnalysis(
            headline="<b>Прогресс</b>",
            summary="Данные обновлены",
            observations=[],
            recommendations=[],
            confidence="high",
            limitations=[],
        )
    unknown = AiAnalysis(
        headline="Прогресс продолжается",
        summary="Недельный тренд направлен вниз.",
        confidence="medium",
        observations=[
            AiObservation(
                title="Динамика",
                text="Сглаженный тренд снижается.",
                scope="weight",
                tone="positive",
                evidence_keys=["weight.unknown"],
            )
        ],
        recommendations=[],
        limitations=[],
    )
    with pytest.raises(ValueError, match="unknown metric"):
        validate_analysis_evidence(unknown, snapshot)

    for evidence in (
        "pressure.systolic_7d",
        "heart.resting_bpm",
        "recovery.hrv_ms",
        "recovery.vo2_max",
    ):
        medical = AiAnalysis(
            headline="Наблюдение",
            summary="Данных достаточно для описания динамики.",
            confidence="medium",
            recommendations=[
                AiRecommendation(
                    title="Режим",
                    text="Сохраните устойчивый распорядок.",
                    scope="general",
                    evidence_keys=[evidence],
                )
            ],
            observations=[],
            limitations=[],
        )
        with pytest.raises(ValueError, match="cannot support"):
            validate_analysis_evidence(medical, snapshot)

    with pytest.raises(ValidationError, match="clinical recommendations"):
        AiRecommendation(
            title="Лечение",
            text="Измените дозировку препарата.",
            scope="general",
            evidence_keys=["weight.change_7d_kg"],
        )

    with pytest.raises(ValidationError, match="clinical language"):
        AiObservation(
            title="Давление",
            text="Давление находится в опасной категории.",
            scope="pressure",
            tone="attention",
            evidence_keys=["pressure.systolic_7d"],
        )

    with pytest.raises(ValidationError, match="clinical recommendations"):
        AiRecommendation(
            title="Рацион",
            text="Сократите рацион до 1500 ккал.",
            scope="weight",
            evidence_keys=["weight.change_7d_kg"],
        )


def test_recommendations_reject_restricted_correlations_but_allow_safe_one():
    safe_key = "correlation.activity_steps_to_weight_kg"
    pressure_key = "correlation.activity_steps_to_systolic_mm_hg"
    heart_key = "correlation.recovery_resting_heart_rate_bpm_to_weight_kg"
    oxygen_key = "correlation.recovery_spo2_pct_to_weight_kg"
    vo2_key = "correlation.recovery_vo2_max_to_weight_kg"
    snapshot = AnalysisSnapshot(
        source_through=NOW,
        facts=[
            fact(safe_key, -0.5, "correlation", "coefficient"),
            fact(pressure_key, 0.25, "pressure", "coefficient"),
            fact(heart_key, 0.4, "heart", "coefficient"),
            fact(oxygen_key, -0.1, "oxygen", "coefficient"),
            fact(vo2_key, -0.2, "vo2", "coefficient"),
        ],
    )

    def analysis(evidence_key: str) -> AiAnalysis:
        return AiAnalysis(
            headline="Наблюдение",
            summary="Данных достаточно для описания динамики.",
            confidence="medium",
            recommendations=[
                AiRecommendation(
                    title="Режим",
                    text="Сохраняйте устойчивый распорядок активности.",
                    scope="activity",
                    evidence_keys=[evidence_key],
                )
            ],
            observations=[],
            limitations=[],
        )

    validate_analysis_evidence(analysis(safe_key), snapshot)
    for evidence_key in (pressure_key, heart_key, oxygen_key, vo2_key):
        with pytest.raises(ValueError, match="cannot support"):
            validate_analysis_evidence(analysis(evidence_key), snapshot)
