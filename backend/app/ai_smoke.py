from __future__ import annotations

from datetime import datetime, timezone
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


def run_smoke() -> GatewayAnalyzeResponse:
    settings = get_settings()
    request = synthetic_request()
    try:
        response = httpx.post(
            f"{settings.ai_gateway_url}/analyze",
            json=request.model_dump(mode="json"),
            timeout=httpx.Timeout(settings.ai_gateway_timeout_seconds + 15, connect=10),
        )
    except httpx.HTTPError as exc:
        raise RuntimeError("AI gateway could not be reached") from exc
    if response.status_code != 200:
        raise RuntimeError(f"AI gateway smoke returned HTTP {response.status_code}")
    if len(response.content) > 65_536:
        raise RuntimeError("AI gateway smoke response is too large")
    try:
        parsed = GatewayAnalyzeResponse.model_validate(response.json())
    except (ValueError, TypeError) as exc:
        raise RuntimeError("AI gateway smoke response failed schema validation") from exc
    if parsed.snapshot_hash != request.snapshot_hash:
        raise RuntimeError("AI gateway smoke response hash does not match the request")
    return parsed


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    result = run_smoke()
    logger.info(
        "AI gateway smoke passed model=%s prompt_version=%s",
        result.model,
        result.prompt_version,
    )


if __name__ == "__main__":
    main()
