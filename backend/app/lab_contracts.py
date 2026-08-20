from __future__ import annotations

from datetime import date
from decimal import Decimal
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .ai_contracts import AI_MODEL


LAB_EXTRACTION_PROMPT_VERSION = "amigo-lab-extraction-v1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


ShortText = Annotated[str, StringConstraints(min_length=1, max_length=240)]


class ExtractedLabResult(StrictModel):
    analyte_name: ShortText
    canonical_hint: Annotated[str | None, StringConstraints(max_length=120)] = None
    value_numeric: Decimal | None = None
    value_text: Annotated[str | None, StringConstraints(max_length=240)] = None
    comparator: Literal["<", "<=", "=", ">=", ">"] | None = None
    unit: Annotated[str | None, StringConstraints(max_length=80)] = None
    observed_on: date | None = None
    specimen: Annotated[str | None, StringConstraints(max_length=120)] = None
    method: Annotated[str | None, StringConstraints(max_length=240)] = None
    reference_low: Decimal | None = None
    reference_high: Decimal | None = None
    reference_text: Annotated[str | None, StringConstraints(max_length=240)] = None
    laboratory_flag: Annotated[str | None, StringConstraints(max_length=80)] = None
    source_page: int | None = Field(default=None, ge=1, le=50)

    @model_validator(mode="after")
    def has_value(self) -> "ExtractedLabResult":
        if self.value_numeric is None and not self.value_text:
            raise ValueError("a numeric or textual value is required")
        return self


class ExtractedLabReport(StrictModel):
    observed_on: date | None = None
    laboratory: Annotated[str | None, StringConstraints(max_length=240)] = None
    specimen: Annotated[str | None, StringConstraints(max_length=120)] = None


class LabExtraction(StrictModel):
    report: ExtractedLabReport
    results: list[ExtractedLabResult] = Field(default_factory=list, max_length=300)


class GatewayLabRequest(StrictModel):
    contract_version: Literal[LAB_EXTRACTION_PROMPT_VERSION]
    model: Literal[AI_MODEL]
    document_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f-]{36}$")]
    chunk_index: int = Field(ge=0, le=99)
    page_from: int = Field(ge=1, le=50)
    page_to: int = Field(ge=1, le=50)
    text: Annotated[str, StringConstraints(min_length=1, max_length=100_000)]


class GatewayLabResponse(StrictModel):
    contract_version: Literal[LAB_EXTRACTION_PROMPT_VERSION]
    model: Literal[AI_MODEL]
    extraction: LabExtraction


class ChatSegment(StrictModel):
    text: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    evidence_keys: list[Annotated[str, StringConstraints(min_length=1, max_length=180)]] = Field(
        min_length=1, max_length=12
    )


class ChatAnswer(StrictModel):
    segments: list[ChatSegment] = Field(min_length=1, max_length=8)


class GatewayChatRequest(StrictModel):
    model: Literal[AI_MODEL]
    contract_version: Literal["amigo-health-chat-v1"]
    message_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f-]{36}$")]
    prompt: Annotated[str, StringConstraints(min_length=1, max_length=600_000)]
    allowed_evidence_keys: list[Annotated[str, StringConstraints(min_length=1, max_length=180)]] = Field(
        max_length=3000
    )


class GatewayChatResponse(StrictModel):
    model: Literal[AI_MODEL]
    contract_version: Literal["amigo-health-chat-v1"]
    answer: ChatAnswer


_UNSAFE_CHAT = re.compile(
    r"(?:диагноз|назнач|отмен|дозиров|лекарств|медикамент|препарат|таблет|"
    r"лечени|терапи|аспирин|метформин|инсулин|семаглутид|оземпик|"
    r"срочн|немедлен|неотложн|скорую|diagnos|prescri|medicat|dosage|"
    r"treatment|therapy|urgent|emergency|ambulance|"
    r"\b\d{2,5}(?:[.,]\d+)?\s*(?:ккал|kcal|калори(?:й|и|я)|calories?)\b)",
    re.IGNORECASE,
)


def validate_chat_answer(answer: ChatAnswer, allowed_evidence_keys: set[str]) -> None:
    for segment in answer.segments:
        if any(key not in allowed_evidence_keys for key in segment.evidence_keys):
            raise ValueError("unknown evidence key")
        if any(ord(char) < 32 and char not in "\n\t" for char in segment.text):
            raise ValueError("control character")
        if "<" in segment.text or ">" in segment.text or re.search(r"(?:https?://|www\.)", segment.text, re.I):
            raise ValueError("markup or link")
        if _UNSAFE_CHAT.search(segment.text):
            raise ValueError("unsafe clinical language")
