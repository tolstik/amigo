from __future__ import annotations

import argparse
from datetime import datetime, timezone
import logging
import signal
import time

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from .ai_contracts import (
    MAX_ANALYSIS_REQUEST_ATTEMPT,
    AnalysisSnapshot,
    GatewayAnalyzeRequest,
    GatewayAnalyzeResponse,
)
from .ai_models import AiAnalysisJob
from .ai_queue import (
    ALLOWED_ERROR_CODES,
    claim_analysis_job,
    complete_analysis_job,
    fail_analysis_job,
    recover_expired_leases,
)
from .config import Settings, get_settings
from .db import SessionLocal


logger = logging.getLogger("amigo.ai.worker")


class GatewayClientError(RuntimeError):
    def __init__(self, code: str):
        normalized = code if code in ALLOWED_ERROR_CODES else "internal"
        super().__init__(normalized)
        self.code = normalized


class AiGatewayClient:
    def __init__(self, settings: Settings, http: httpx.Client | None = None):
        self.url = f"{settings.ai_gateway_url}/analyze"
        self.http = http or httpx.Client(
            timeout=httpx.Timeout(settings.ai_gateway_timeout_seconds, connect=10)
        )
        self._owns_http = http is None

    def close(self) -> None:
        if self._owns_http:
            self.http.close()

    def analyze(self, job: AiAnalysisJob) -> GatewayAnalyzeResponse:
        try:
            snapshot = AnalysisSnapshot.model_validate(job.snapshot)
            request = GatewayAnalyzeRequest(
                snapshot_hash=job.snapshot_hash,
                prompt_version=job.prompt_version,
                model=job.model,
                attempt=min(
                    MAX_ANALYSIS_REQUEST_ATTEMPT,
                    max(1, job.attempts),
                ),
                snapshot=snapshot,
            )
        except ValueError as exc:
            raise GatewayClientError("hash_mismatch") from exc
        try:
            response = self.http.post(self.url, json=request.model_dump(mode="json"))
        except httpx.TimeoutException as exc:
            raise GatewayClientError("timeout") from exc
        except httpx.HTTPError as exc:
            raise GatewayClientError("gateway_unavailable") from exc
        if response.status_code == 429:
            raise GatewayClientError("gateway_busy")
        if response.status_code == 504:
            raise GatewayClientError("timeout")
        if response.status_code == 503:
            raise GatewayClientError("gateway_unavailable")
        if response.status_code == 502:
            code = "gateway_unavailable"
            try:
                detail = response.json().get("detail")
                if detail in {"codex_failed", "invalid_response"}:
                    code = detail
            except (ValueError, AttributeError):
                pass
            raise GatewayClientError(code)
        if response.status_code < 200 or response.status_code >= 300:
            raise GatewayClientError("gateway_rejected")
        if len(response.content) > 65_536:
            raise GatewayClientError("invalid_response")
        try:
            parsed = GatewayAnalyzeResponse.model_validate(response.json())
        except (ValueError, TypeError) as exc:
            raise GatewayClientError("invalid_response") from exc
        if parsed.snapshot_hash != job.snapshot_hash:
            raise GatewayClientError("hash_mismatch")
        return parsed


class AiAnalysisWorker:
    def __init__(
        self,
        settings: Settings | None = None,
        gateway: AiGatewayClient | None = None,
    ):
        self.settings = settings or get_settings()
        self.gateway = gateway or AiGatewayClient(self.settings)
        self._owns_gateway = gateway is None
        self.max_attempts = min(
            self.settings.ai_max_attempts,
            MAX_ANALYSIS_REQUEST_ATTEMPT,
        )
        self.running = True

    def close(self) -> None:
        if self._owns_gateway:
            self.gateway.close()

    def stop(self, *_: object) -> None:
        self.running = False

    def process_one(self, db: Session, now: datetime | None = None) -> bool:
        current = now or datetime.now(timezone.utc)
        recover_expired_leases(
            db,
            now=current,
            max_attempts=self.max_attempts,
            backoff_base_seconds=self.settings.ai_backoff_base_seconds,
        )
        job = claim_analysis_job(
            db,
            now=current,
            lease_seconds=self.settings.ai_lease_seconds,
            max_attempts=self.max_attempts,
        )
        if job is None:
            return False
        try:
            response = self.gateway.analyze(job)
            complete_analysis_job(
                db,
                job,
                response,
                stale_seconds=self.settings.ai_stale_seconds,
            )
            logger.info("AI analysis job id=%s succeeded", job.id)
        except GatewayClientError as exc:
            fail_analysis_job(
                db,
                job,
                exc.code,
                now=current,
                max_attempts=self.max_attempts,
                backoff_base_seconds=self.settings.ai_backoff_base_seconds,
            )
            logger.warning("AI analysis job id=%s failed code=%s", job.id, exc.code)
        except Exception:
            db.rollback()
            fail_analysis_job(
                db,
                job,
                "internal",
                now=current,
                max_attempts=self.max_attempts,
                backoff_base_seconds=self.settings.ai_backoff_base_seconds,
            )
            logger.warning("AI analysis job id=%s failed code=internal", job.id)
        return True

    def run_once(self) -> bool:
        with SessionLocal() as db:
            return self.process_one(db)

    def run(self) -> None:
        try:
            while self.running:
                try:
                    processed = self.run_once()
                except Exception as exc:
                    logger.warning("AI worker loop failed type=%s", type(exc).__name__)
                    processed = False
                if self.settings.worker_once:
                    break
                if not processed:
                    time.sleep(self.settings.ai_poll_seconds)
        finally:
            self.close()


def healthcheck(settings: Settings) -> bool:
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        response = httpx.get(
            f"{settings.ai_gateway_url}/healthz",
            timeout=httpx.Timeout(5, connect=3),
        )
        return response.status_code == 200
    except Exception:
        return False


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Amigo asynchronous AI analysis worker")
    parser.add_argument("--health", action="store_true", help="check database and AI gateway")
    args = parser.parse_args(argv)
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if args.health:
        raise SystemExit(0 if healthcheck(settings) else 1)
    if not settings.ai_enabled:
        logger.info("AI analysis worker is disabled")
        return
    worker = AiAnalysisWorker(settings)
    signal.signal(signal.SIGTERM, worker.stop)
    signal.signal(signal.SIGINT, worker.stop)
    worker.run()


if __name__ == "__main__":
    main()
