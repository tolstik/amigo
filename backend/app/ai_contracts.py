from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256
import json
import math
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


AI_MODEL = "gpt-5.6-terra"
AI_PROMPT_VERSION = "amigo-health-v2"
SNAPSHOT_SCHEMA_VERSION = "1"

MetricKey = Annotated[
    str,
    StringConstraints(
        min_length=3,
        max_length=80,
        pattern=r"^[a-z][a-z0-9]*(?:[._][a-z0-9]+)*$",
    ),
]
MetricScope = Literal[
    "profile",
    "weight",
    "composition",
    "activity",
    "sleep",
    "recovery",
    "heart",
    "oxygen",
    "vo2",
    "pressure",
    "quality",
    "correlation",
]
MetricPeriod = Literal[
    "current",
    "day",
    "week",
    "7d",
    "14d",
    "28d",
    "30d",
    "90d",
    "program",
    "all",
]
MetricUnit = Literal[
    "centimeters",
    "kg_m2",
    "kg",
    "percent",
    "steps",
    "minutes",
    "hours",
    "days",
    "count",
    "km",
    "kcal",
    "mmhg",
    "bpm",
    "milliseconds",
    "breaths_per_minute",
    "score",
    "ml_kg_min",
    "coefficient",
    "boolean",
    "date",
]
AnalysisScope = Literal[
    "weight",
    "composition",
    "activity",
    "sleep",
    "recovery",
    "heart",
    "pressure",
    "general",
    "measurement",
]
RecommendationScope = Literal[
    "weight",
    "composition",
    "activity",
    "nutrition",
    "sleep",
    "recovery",
    "medical",
    "general",
    "measurement",
]
AnalysisTone = Literal["positive", "neutral", "attention", "achievement"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SnapshotFact(StrictModel):
    key: MetricKey
    scope: MetricScope
    period: MetricPeriod
    value: float | int | bool | None
    unit: MetricUnit
    observed_on: date | None = None

    @field_validator("value")
    @classmethod
    def finite_value(cls, value: float | int | bool | None) -> float | int | bool | None:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("metric value must be finite")
        return value


class SnapshotPoint(StrictModel):
    day: date
    value: float

    @field_validator("value")
    @classmethod
    def finite_value(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("series value must be finite")
        return value


class SnapshotSeries(StrictModel):
    key: MetricKey
    scope: MetricScope
    unit: MetricUnit
    points: Annotated[list[SnapshotPoint], Field(min_length=1, max_length=120)]

    @model_validator(mode="after")
    def unique_days(self) -> SnapshotSeries:
        days = [point.day for point in self.points]
        if len(days) != len(set(days)):
            raise ValueError("series days must be unique")
        return self


class AnalysisSnapshot(StrictModel):
    schema_version: Literal["1"] = SNAPSHOT_SCHEMA_VERSION
    source_through: datetime
    timezone: Literal["Europe/Moscow"] = "Europe/Moscow"
    facts: Annotated[list[SnapshotFact], Field(max_length=96)] = Field(default_factory=list)
    series: Annotated[list[SnapshotSeries], Field(max_length=12)] = Field(default_factory=list)

    @field_validator("source_through")
    @classmethod
    def aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("source_through must be timezone-aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def useful_unique_payload(self) -> AnalysisSnapshot:
        if not self.facts and not self.series:
            raise ValueError("snapshot must contain at least one metric")
        keys = [item.key for item in self.facts] + [item.key for item in self.series]
        if len(keys) != len(set(keys)):
            raise ValueError("snapshot metric keys must be unique")
        return self


SafeTitle = Annotated[str, StringConstraints(min_length=1, max_length=100)]
SafeBody = Annotated[str, StringConstraints(min_length=1, max_length=700)]
EvidenceList = Annotated[list[MetricKey], Field(min_length=1, max_length=6)]


def _safe_generated_text(value: str) -> str:
    if any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise ValueError("control characters are not allowed")
    lowered = value.casefold()
    if "<" in value or ">" in value or re.search(r"(?:https?://|www\.|tg://)", lowered):
        raise ValueError("markup and links are not allowed")
    return value


_PROHIBITED_CLINICAL_LANGUAGE = re.compile(
    r"(?:диагноз|диагностир|гипертони|гипотони|ожирен|"
    r"избыточн.{0,16}вес|назнач|отмен|дозиров|лекарств|медикамент|препарат|"
    r"таблет|лечени|терапи|аспирин|метформин|инсулин|семаглутид|оземпик|"
    r"инсульт|инфаркт|аритми|тахикард|брадикард|диабет|"
    r"срочн|немедлен|неотложн|скорую|"
    r"(?:(?:давлени|пульс|чсс|сатураци|spo2|hrv|vo2).{0,24}"
    r"(?:высок|низк|норм|опасн|критич))|"
    r"(?:(?:высок|низк|норм|опасн|критич).{0,24}"
    r"(?:давлени|пульс|чсс|сатураци|spo2|hrv|vo2))|"
    r"diagnos|hypertens|hypotens|obes|overweight|prescri|medicat|dosage|"
    r"treatment|therapy|aspirin|metformin|insulin|semaglutide|ozempic|"
    r"stroke|heart attack|arrhythm|tachycard|bradycard|diabet|"
    r"urgent|immediate|emergency|ambulance|"
    r"(?:(?:blood pressure|heart rate|pulse|spo2|hrv|vo2).{0,24}"
    r"(?:high|low|normal|danger|critical))|"
    r"(?:(?:high|low|normal|danger|critical).{0,24}"
    r"(?:blood pressure|heart rate|pulse|spo2|hrv|vo2)))",
    re.IGNORECASE,
)
_UNSAFE_RECOMMENDATION = re.compile(
    r"(?:\b\d{2,5}(?:[.,]\d+)?\s*(?:ккал|kcal|калори(?:й|и|я)|calories?)\b|"
    r"голодан|сух(?:ая|ое)\s+голодов|"
    r"(?:не\s+ешьте|не\s+есть).{0,24}(?:сут|дн)|\bfasting\b)",
    re.IGNORECASE,
)
_BOUNDED_MEDICAL_ACTION = re.compile(
    r"(?:повтор|измер|замер|журнал|дневник|запис|обсуд|покаж|"
    r"repeat|measure|measurement|log|record|discuss|show)",
    re.IGNORECASE,
)
_CLINICIAN_REFERENCE = re.compile(r"(?:врач|доктор|doctor|clinician)", re.IGNORECASE)
_PERSISTENT_PATTERN = re.compile(
    r"(?:сохраня|устойчив|повтор|нескольк|сер(?:ия|ии)|журнал|дневник|"
    r"\b\d+\s*(?:дн|недел)|persist|repeat|several|series|log)",
    re.IGNORECASE,
)


def _safe_medical_text(value: str) -> str:
    sanitized = _safe_generated_text(value)
    if _PROHIBITED_CLINICAL_LANGUAGE.search(sanitized):
        raise ValueError("diagnosis, treatment, and medication instructions are not allowed")
    return sanitized


class AiObservation(StrictModel):
    title: SafeTitle
    text: SafeBody
    scope: AnalysisScope
    tone: AnalysisTone
    evidence_keys: EvidenceList

    @field_validator("title", "text")
    @classmethod
    def safe_text(cls, value: str) -> str:
        return _safe_medical_text(value)


class AiRecommendation(StrictModel):
    title: SafeTitle
    text: SafeBody
    scope: RecommendationScope
    evidence_keys: EvidenceList

    @field_validator("title", "text")
    @classmethod
    def safe_text(cls, value: str) -> str:
        sanitized = _safe_generated_text(value)
        if _PROHIBITED_CLINICAL_LANGUAGE.search(sanitized) or _UNSAFE_RECOMMENDATION.search(
            sanitized
        ):
            raise ValueError("unsafe medical recommendation is not allowed")
        return sanitized


class AiAnalysis(StrictModel):
    headline: Annotated[str, StringConstraints(min_length=1, max_length=160)]
    summary: SafeBody
    observations: Annotated[list[AiObservation], Field(max_length=5)]
    recommendations: Annotated[list[AiRecommendation], Field(max_length=5)]
    confidence: Literal["low", "medium", "high"]
    limitations: Annotated[list[SafeBody], Field(max_length=4)]

    @field_validator("headline", "summary")
    @classmethod
    def safe_text(cls, value: str) -> str:
        return _safe_medical_text(value)

    @field_validator("limitations")
    @classmethod
    def safe_limitations(cls, values: list[str]) -> list[str]:
        return [_safe_medical_text(value) for value in values]


class GatewayAnalyzeRequest(StrictModel):
    snapshot_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    prompt_version: Literal["amigo-health-v2"] = AI_PROMPT_VERSION
    model: Literal["gpt-5.6-terra"] = AI_MODEL
    snapshot: AnalysisSnapshot

    @model_validator(mode="after")
    def matching_hash(self) -> GatewayAnalyzeRequest:
        if self.snapshot_hash != snapshot_hash(self.snapshot):
            raise ValueError("snapshot hash mismatch")
        return self


class GatewayAnalyzeResponse(StrictModel):
    snapshot_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    prompt_version: Literal["amigo-health-v2"] = AI_PROMPT_VERSION
    model: Literal["gpt-5.6-terra"] = AI_MODEL
    generated_at: datetime
    duration_ms: Annotated[int, Field(ge=0, le=600_000)]
    analysis: AiAnalysis

    @field_validator("generated_at")
    @classmethod
    def aware_generated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must be timezone-aware")
        return value.astimezone(timezone.utc)


def canonical_snapshot_payload(snapshot: AnalysisSnapshot) -> dict[str, Any]:
    payload = snapshot.model_dump(mode="json")
    payload["facts"] = sorted(payload["facts"], key=lambda item: item["key"])
    payload["series"] = sorted(payload["series"], key=lambda item: item["key"])
    for series in payload["series"]:
        series["points"] = sorted(series["points"], key=lambda item: item["day"])
    return payload


def canonical_snapshot_json(snapshot: AnalysisSnapshot) -> str:
    return json.dumps(
        canonical_snapshot_payload(snapshot),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def snapshot_hash(snapshot: AnalysisSnapshot) -> str:
    return sha256(canonical_snapshot_json(snapshot).encode("utf-8")).hexdigest()


def analysis_request_key(
    digest: str, prompt_version: str = AI_PROMPT_VERSION, model: str = AI_MODEL
) -> str:
    return sha256(f"{digest}\n{prompt_version}\n{model}".encode("utf-8")).hexdigest()


def validate_analysis_evidence(analysis: AiAnalysis, snapshot: AnalysisSnapshot) -> None:
    facts = {item.key: item for item in snapshot.facts}
    series = {item.key: item for item in snapshot.series}
    known = facts.keys() | series.keys()

    def is_medical_evidence(key: str) -> bool:
        return bool(
            (facts.get(key) and facts[key].scope in {"pressure", "heart", "oxygen", "vo2"})
            or (
                series.get(key)
                and series[key].scope in {"pressure", "heart", "oxygen", "vo2"}
            )
            or ".spo2" in key
            or "oxygen_saturation" in key
        )

    for item in [*analysis.observations, *analysis.recommendations]:
        if not set(item.evidence_keys).issubset(known):
            raise ValueError("analysis cites an unknown metric")
    has_medical_evidence = any(is_medical_evidence(key) for key in known)
    has_bounded_medical_recommendation = False
    for recommendation in analysis.recommendations:
        uses_medical_metric = any(is_medical_evidence(key) for key in recommendation.evidence_keys)
        if uses_medical_metric and recommendation.scope not in {"medical", "measurement"}:
            raise ValueError(
                "pressure, heart, oxygen, and VO2 recommendations must be medical or measurement scoped"
            )
        if recommendation.scope == "medical" and not uses_medical_metric:
            raise ValueError("medical recommendations must cite a medical metric")
        if uses_medical_metric:
            combined = f"{recommendation.title} {recommendation.text}"
            if not _BOUNDED_MEDICAL_ACTION.search(combined):
                raise ValueError(
                    "medical metrics may support only measurement, logging, or clinician discussion"
                )
            if _CLINICIAN_REFERENCE.search(combined) and not _PERSISTENT_PATTERN.search(
                combined
            ):
                raise ValueError("clinician discussion requires a persistent measured pattern")
            has_bounded_medical_recommendation = True
    if has_medical_evidence and not has_bounded_medical_recommendation:
        raise ValueError(
            "analysis with medical metrics requires a bounded medical or measurement recommendation"
        )
