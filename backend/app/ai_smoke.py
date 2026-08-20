from __future__ import annotations

from datetime import datetime, timezone
import json
import logging

import httpx

from .ai_contracts import (
    AI_MODEL,
    AI_PROMPT_VERSION,
    AnalysisSnapshot,
    GatewayAnalyzeRequest,
    GatewayAnalyzeResponse,
    SnapshotFact,
    snapshot_hash,
)
from .config import get_settings
from .lab_contracts import (
    LAB_EXTRACTION_PROMPT_VERSION,
    GatewayChatRequest,
    GatewayChatResponse,
    GatewayLabRequest,
    GatewayLabResponse,
    validate_chat_answer,
)


logger = logging.getLogger("amigo.ai.smoke")


def synthetic_request(now: datetime | None = None) -> GatewayAnalyzeRequest:
    """Build a contract-only probe that never contains personal health data."""

    current = now or datetime.now(timezone.utc)
    snapshot = AnalysisSnapshot(
        source_through=current,
        facts=[
            SnapshotFact(
                key="quality.runtime_smoke",
                scope="quality",
                period="current",
                value=True,
                unit="boolean",
                observed_on=current.date(),
            )
        ],
    )
    return GatewayAnalyzeRequest(
        snapshot_hash=snapshot_hash(snapshot),
        prompt_version=AI_PROMPT_VERSION,
        model=AI_MODEL,
        snapshot=snapshot,
    )


def synthetic_lab_request() -> GatewayLabRequest:
    """Build a parser-free extraction probe with no person or health history."""

    return GatewayLabRequest(
        contract_version=LAB_EXTRACTION_PROMPT_VERSION,
        model=AI_MODEL,
        document_id="00000000-0000-0000-0000-000000000001",
        chunk_index=0,
        page_from=1,
        page_to=1,
        text=(
            "Synthetic contract fixture. Quality marker: 1 unit. "
            "Reference interval: 0-2 unit. Source page: 1."
        ),
    )


def synthetic_chat_request() -> GatewayChatRequest:
    """Build an evidence-bound assistant probe with no personal context."""

    evidence_key = "quality.runtime_smoke"
    prompt = json.dumps(
        {
            "facts": [{"key": evidence_key, "value": True, "unit": "boolean"}],
            "conversation": [
                {
                    "role": "user",
                    "content": "Коротко подтверди доступность синтетического сигнала.",
                }
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return GatewayChatRequest(
        model=AI_MODEL,
        contract_version="amigo-health-chat-v1",
        message_id="00000000-0000-0000-0000-000000000002",
        prompt=prompt,
        allowed_evidence_keys=[evidence_key],
    )


def _post_contract(url: str, payload: dict, timeout_seconds: int, label: str) -> httpx.Response:
    try:
        response = httpx.post(
            url,
            json=payload,
            timeout=httpx.Timeout(timeout_seconds + 15, connect=10),
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"AI gateway {label} could not be reached") from exc
    if response.status_code != 200:
        raise RuntimeError(f"AI gateway {label} returned HTTP {response.status_code}")
    if len(response.content) > 300_000:
        raise RuntimeError(f"AI gateway {label} response is too large")
    return response


def run_smoke() -> GatewayAnalyzeResponse:
    settings = get_settings()
    request = synthetic_request()
    response = _post_contract(
        f"{settings.ai_gateway_url}/analyze",
        request.model_dump(mode="json"),
        settings.ai_gateway_timeout_seconds,
        "analysis smoke",
    )
    try:
        parsed = GatewayAnalyzeResponse.model_validate(response.json())
    except (ValueError, TypeError) as exc:
        raise RuntimeError("AI gateway smoke response failed schema validation") from exc
    if parsed.snapshot_hash != request.snapshot_hash:
        raise RuntimeError("AI gateway smoke response hash does not match the request")

    lab_request = synthetic_lab_request()
    lab_response = _post_contract(
        f"{settings.ai_gateway_url}/extract-labs",
        lab_request.model_dump(mode="json"),
        settings.ai_gateway_timeout_seconds,
        "laboratory smoke",
    )
    try:
        GatewayLabResponse.model_validate(lab_response.json())
    except (ValueError, TypeError) as exc:
        raise RuntimeError("AI gateway laboratory smoke failed schema validation") from exc

    chat_request = synthetic_chat_request()
    chat_response = _post_contract(
        f"{settings.ai_gateway_url}/chat",
        chat_request.model_dump(mode="json"),
        settings.ai_gateway_timeout_seconds,
        "assistant smoke",
    )
    completed: GatewayChatResponse | None = None
    try:
        for line in chat_response.text.splitlines():
            if not line:
                continue
            event = json.loads(line)
            if event.get("type") == "complete":
                completed = GatewayChatResponse.model_validate(event.get("response"))
            elif event.get("type") == "error":
                raise ValueError("assistant smoke returned an error event")
    except (ValueError, TypeError) as exc:
        raise RuntimeError("AI gateway assistant smoke failed schema validation") from exc
    if completed is None:
        raise RuntimeError("AI gateway assistant smoke did not complete")
    validate_chat_answer(completed.answer, set(chat_request.allowed_evidence_keys))
    return parsed


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    result = run_smoke()
    logger.info(
        "AI gateway smokes passed analysis/laboratory/assistant model=%s prompt_version=%s",
        result.model,
        result.prompt_version,
    )


if __name__ == "__main__":
    main()
