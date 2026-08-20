from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.ai_contracts import (
    AI_MODEL,
    AI_PROMPT_VERSION,
    MAX_ANALYSIS_REQUEST_ATTEMPT,
    AiAnalysis,
    AiObservation,
    AiRecommendation,
    AnalysisSnapshot,
    GatewayAnalyzeRequest,
    SnapshotFact,
    SnapshotPoint,
    SnapshotSeries,
    analysis_request_key,
    canonical_snapshot_json,
    snapshot_evidence_keys,
    snapshot_hash,
    snapshot_medical_evidence_keys,
    validate_analysis_evidence,
)
from app.config import Settings


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


def test_gateway_contract_is_pinned_to_sol():
    snapshot = AnalysisSnapshot(source_through=NOW, facts=[fact("weight.latest_kg", 120)])

    assert AI_MODEL == "gpt-5.6-sol"
    with pytest.raises(ValidationError):
        GatewayAnalyzeRequest(
            snapshot_hash=snapshot_hash(snapshot),
            prompt_version=AI_PROMPT_VERSION,
            model="gpt-5.6-terra",
            snapshot=snapshot,
        )


def test_gateway_request_attempt_is_bounded_and_does_not_change_snapshot_hash():
    snapshot = AnalysisSnapshot(source_through=NOW, facts=[fact("weight.latest_kg", 120)])
    digest = snapshot_hash(snapshot)

    first = GatewayAnalyzeRequest(snapshot_hash=digest, snapshot=snapshot)
    retry = GatewayAnalyzeRequest(
        snapshot_hash=digest,
        snapshot=snapshot,
        attempt=MAX_ANALYSIS_REQUEST_ATTEMPT,
    )

    assert first.attempt == 1
    assert retry.attempt == MAX_ANALYSIS_REQUEST_ATTEMPT
    assert first.snapshot_hash == retry.snapshot_hash
    for invalid_attempt in (0, MAX_ANALYSIS_REQUEST_ATTEMPT + 1):
        with pytest.raises(ValidationError):
            GatewayAnalyzeRequest(
                snapshot_hash=digest,
                snapshot=snapshot,
                attempt=invalid_attempt,
            )


def test_worker_default_attempt_cap_matches_gateway_request_contract():
    assert Settings.model_fields["ai_max_attempts"].default == MAX_ANALYSIS_REQUEST_ATTEMPT


def test_model_migration_changes_the_deduplication_key():
    digest = "a" * 64

    assert analysis_request_key(digest) != analysis_request_key(
        digest,
        model="gpt-5.6-terra",
    )


def test_gateway_request_rejects_noncanonical_hash():
    snapshot = AnalysisSnapshot(source_through=NOW, facts=[fact("weight.latest_kg", 120)])
    with pytest.raises(ValidationError):
        GatewayAnalyzeRequest(
            snapshot_hash="0" * 64,
            prompt_version=AI_PROMPT_VERSION,
            model=AI_MODEL,
            snapshot=snapshot,
        )


def test_snapshot_evidence_helpers_preserve_validator_medical_scope_rules():
    snapshot = AnalysisSnapshot(
        source_through=NOW,
        facts=[
            fact("weight.change_7d_kg", -0.7),
            fact("pressure.systolic_7d", 128, "pressure", "mmhg"),
            fact("recovery.spo2_percent", 97, "quality", "percent"),
        ],
        series=[
            SnapshotSeries(
                key="heart.resting_daily",
                scope="heart",
                unit="bpm",
                points=[SnapshotPoint(day="2026-08-19", value=62)],
            )
        ],
    )

    assert snapshot_evidence_keys(snapshot) == {
        "heart.resting_daily",
        "pressure.systolic_7d",
        "recovery.spo2_percent",
        "weight.change_7d_kg",
    }
    assert snapshot_medical_evidence_keys(snapshot) == {
        "heart.resting_daily",
        "pressure.systolic_7d",
        "recovery.spo2_percent",
    }


def test_generated_output_allows_bounded_medical_guidance_but_rejects_unsafe_instructions():
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
        wrong_scope = AiAnalysis(
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
        with pytest.raises(ValueError, match="must be medical or measurement scoped"):
            validate_analysis_evidence(wrong_scope, snapshot)

        medical = AiAnalysis(
            headline="Наблюдение",
            summary="Данных достаточно для описания динамики.",
            confidence="medium",
            recommendations=[
                AiRecommendation(
                    title="Проверка динамики",
                    text="Повторяйте измерение утром семь дней и покажите журнал врачу, если уровень сохраняется.",
                    scope="medical",
                    evidence_keys=[evidence],
                )
            ],
            observations=[],
            limitations=[],
        )
        validate_analysis_evidence(medical, snapshot)

    wrong_medical_evidence = AiAnalysis(
        headline="Наблюдение",
        summary="Данных достаточно для описания динамики.",
        confidence="medium",
        recommendations=[
            AiRecommendation(
                title="Обсуждение динамики",
                text="Покажите журнал врачу, если измеренный паттерн сохраняется.",
                scope="medical",
                evidence_keys=["weight.change_7d_kg"],
            )
        ],
        observations=[],
        limitations=[],
    )
    with pytest.raises(ValueError, match="must cite a medical metric"):
        validate_analysis_evidence(wrong_medical_evidence, snapshot)

    unbounded_medical_action = AiAnalysis(
        headline="Наблюдение",
        summary="Данных достаточно для описания динамики.",
        confidence="medium",
        recommendations=[
            AiRecommendation(
                title="Рацион",
                text="Добавляйте овощи к каждому основному приему пищи.",
                scope="measurement",
                evidence_keys=["pressure.systolic_7d"],
            )
        ],
        observations=[],
        limitations=[],
    )
    with pytest.raises(ValueError, match="only measurement, logging, or clinician"):
        validate_analysis_evidence(unbounded_medical_action, snapshot)

    clinician_without_pattern = AiAnalysis(
        headline="Наблюдение",
        summary="Данных достаточно для описания динамики.",
        confidence="medium",
        recommendations=[
            AiRecommendation(
                title="Обсуждение",
                text="Покажите результат врачу.",
                scope="medical",
                evidence_keys=["pressure.systolic_7d"],
            )
        ],
        observations=[],
        limitations=[],
    )
    with pytest.raises(ValueError, match="persistent measured pattern"):
        validate_analysis_evidence(clinician_without_pattern, snapshot)

    with pytest.raises(ValidationError, match="unsafe medical recommendation"):
        AiRecommendation(
            title="Лечение",
            text="Измените дозировку препарата.",
            scope="general",
            evidence_keys=["weight.change_7d_kg"],
        )

    with pytest.raises(ValidationError, match="diagnosis, treatment, and medication"):
        AiObservation(
            title="Давление",
            text="Давление находится в опасной категории.",
            scope="pressure",
            tone="attention",
            evidence_keys=["pressure.systolic_7d"],
        )

    with pytest.raises(ValidationError, match="diagnosis, treatment, and medication"):
        AiObservation(
            title="Диагноз",
            text="Эти измерения означают гипертонию.",
            scope="pressure",
            tone="attention",
            evidence_keys=["pressure.systolic_7d"],
        )

    with pytest.raises(ValidationError, match="unsafe medical recommendation"):
        AiRecommendation(
            title="Препарат",
            text="Примите аспирин и повторите измерение.",
            scope="medical",
            evidence_keys=["pressure.systolic_7d"],
        )

    with pytest.raises(ValidationError, match="unsafe medical recommendation"):
        AiRecommendation(
            title="Срочная реакция",
            text="Немедленно вызовите скорую.",
            scope="medical",
            evidence_keys=["pressure.systolic_7d"],
        )

    with pytest.raises(ValidationError, match="unsafe medical recommendation"):
        AiRecommendation(
            title="Рацион",
            text="Сократите рацион до 1500 ккал.",
            scope="weight",
            evidence_keys=["weight.change_7d_kg"],
        )
    with pytest.raises(ValidationError, match="unsafe medical recommendation"):
        AiRecommendation(
            title="Рацион",
            text="Установите цель 1500 калорий в день.",
            scope="nutrition",
            evidence_keys=["weight.change_7d_kg"],
        )

    sustainable = AiRecommendation(
        title="Рацион на две недели",
        text="Добавляйте овощи к двум основным приемам пищи и отмечайте голод перед едой.",
        scope="nutrition",
        evidence_keys=["weight.change_7d_kg"],
    )
    assert sustainable.scope == "nutrition"


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
                ),
                AiRecommendation(
                    title="Серия измерений",
                    text="Повторяйте измерение утром семь дней и записывайте результат в журнал.",
                    scope="measurement",
                    evidence_keys=[pressure_key],
                ),
            ],
            observations=[],
            limitations=[],
        )

    validate_analysis_evidence(analysis(safe_key), snapshot)
    for evidence_key in (pressure_key, heart_key, oxygen_key, vo2_key):
        with pytest.raises(ValueError, match="must be medical or measurement scoped"):
            validate_analysis_evidence(analysis(evidence_key), snapshot)


def test_medical_metrics_require_one_bounded_recommendation():
    snapshot = AnalysisSnapshot(
        source_through=NOW,
        facts=[fact("pressure.systolic_7d", 128, "pressure", "mmhg")],
    )
    analysis = AiAnalysis(
        headline="Наблюдение",
        summary="Доступна серия измерений.",
        confidence="medium",
        recommendations=[],
        observations=[],
        limitations=[],
    )

    with pytest.raises(ValueError, match="requires a bounded"):
        validate_analysis_evidence(analysis, snapshot)
