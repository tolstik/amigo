from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .ai_contracts import (
    AI_MODEL,
    AI_PROMPT_VERSION,
    AiAnalysis,
    AnalysisSnapshot,
    GatewayAnalyzeResponse,
    analysis_request_key,
    snapshot_hash,
    validate_analysis_evidence,
)
from .ai_models import AiAnalysisJob, AiAnalysisResult


AnalysisTrigger = Literal["measurement", "activity", "scheduled", "manual"]
JobErrorCode = Literal[
    "timeout",
    "gateway_unavailable",
    "gateway_busy",
    "gateway_rejected",
    "invalid_response",
    "hash_mismatch",
    "codex_failed",
    "lease_expired",
    "internal",
]
ALLOWED_ERROR_CODES = {
    "timeout",
    "gateway_unavailable",
    "gateway_busy",
    "gateway_rejected",
    "invalid_response",
    "hash_mismatch",
    "codex_failed",
    "lease_expired",
    "internal",
}


def _aware(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    return current.replace(tzinfo=timezone.utc) if current.tzinfo is None else current


def _backoff_seconds(attempts: int, base_seconds: int) -> int:
    return min(3600, base_seconds * (2 ** max(0, attempts - 1)))


def enqueue_analysis(
    db: Session,
    snapshot: AnalysisSnapshot,
    *,
    trigger: AnalysisTrigger,
    now: datetime | None = None,
    debounce_seconds: int = 300,
    activity_min_interval_seconds: int = 3600,
    stale_seconds: int = 86400,
) -> AiAnalysisJob:
    """Persist one canonical request and supersede older queued snapshots."""

    current = _aware(now)
    digest = snapshot_hash(snapshot)
    request_key = analysis_request_key(digest)
    existing = db.scalar(select(AiAnalysisJob).where(AiAnalysisJob.request_key == request_key))
    if existing is not None:
        return existing

    latest_result = db.scalar(
        select(AiAnalysisResult)
        .order_by(AiAnalysisResult.source_through.desc(), AiAnalysisResult.generated_at.desc())
        .limit(1)
    )
    if (
        latest_result is not None
        and latest_result.snapshot_hash != digest
        and latest_result.fresh_until is None
    ):
        latest_result.fresh_until = current + timedelta(seconds=max(1, stale_seconds))

    available_at = current + timedelta(seconds=max(0, debounce_seconds))
    if trigger == "activity" and activity_min_interval_seconds > 0:
        latest = db.scalar(
            select(AiAnalysisJob)
            .where(AiAnalysisJob.status.in_(("pending", "processing", "succeeded")))
            .order_by(AiAnalysisJob.created_at.desc(), AiAnalysisJob.id.desc())
            .limit(1)
        )
        if latest is not None:
            if latest.status == "pending":
                # A stream of five-minute activity updates must not postpone
                # analysis forever. Replace the payload but retain the first
                # pending deadline.
                available_at = min(
                    available_at,
                    max(current, _aware(latest.available_at)),
                )
            else:
                not_before = _aware(latest.created_at) + timedelta(
                    seconds=activity_min_interval_seconds
                )
                available_at = max(available_at, not_before)

    db.execute(
        update(AiAnalysisJob)
        .where(AiAnalysisJob.status == "pending")
        .values(status="superseded", finished_at=current, lease_until=None)
    )
    job = AiAnalysisJob(
        request_key=request_key,
        snapshot_hash=digest,
        snapshot=snapshot.model_dump(mode="json"),
        source_through=snapshot.source_through,
        prompt_version=AI_PROMPT_VERSION,
        model=AI_MODEL,
        trigger=trigger,
        status="pending",
        available_at=available_at,
        created_at=current,
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        concurrent = db.scalar(
            select(AiAnalysisJob).where(AiAnalysisJob.request_key == request_key)
        )
        if concurrent is None:
            raise
        return concurrent
    db.refresh(job)
    return job


def recover_expired_leases(
    db: Session,
    *,
    now: datetime | None = None,
    max_attempts: int = 4,
    backoff_base_seconds: int = 60,
) -> int:
    current = _aware(now)
    jobs = list(
        db.scalars(
            select(AiAnalysisJob).where(
                AiAnalysisJob.status == "processing",
                AiAnalysisJob.lease_until <= current,
            )
        )
    )
    for job in jobs:
        job.lease_until = None
        job.last_error_code = "lease_expired"
        if job.attempts >= max_attempts:
            job.status = "failed"
            job.finished_at = current
        else:
            job.status = "pending"
            job.available_at = current + timedelta(
                seconds=_backoff_seconds(job.attempts, backoff_base_seconds)
            )
    if jobs:
        db.commit()
    return len(jobs)


def claim_analysis_job(
    db: Session,
    *,
    now: datetime | None = None,
    lease_seconds: int = 180,
    max_attempts: int = 4,
) -> AiAnalysisJob | None:
    current = _aware(now)
    job = db.scalar(
        select(AiAnalysisJob)
        .where(
            AiAnalysisJob.status == "pending",
            AiAnalysisJob.available_at <= current,
            AiAnalysisJob.attempts < max_attempts,
        )
        .order_by(AiAnalysisJob.available_at, AiAnalysisJob.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        return None
    job.status = "processing"
    job.attempts += 1
    job.started_at = job.started_at or current
    job.lease_until = current + timedelta(seconds=lease_seconds)
    job.last_error_code = None
    db.commit()
    db.refresh(job)
    return job


def complete_analysis_job(
    db: Session,
    job: AiAnalysisJob,
    response: GatewayAnalyzeResponse,
    *,
    stale_seconds: int = 86400,
) -> AiAnalysisResult:
    snapshot = AnalysisSnapshot.model_validate(job.snapshot)
    if response.snapshot_hash != job.snapshot_hash:
        raise ValueError("snapshot hash mismatch")
    if response.prompt_version != job.prompt_version or response.model != job.model:
        raise ValueError("gateway contract mismatch")
    validate_analysis_evidence(response.analysis, snapshot)

    existing = db.scalar(
        select(AiAnalysisResult).where(AiAnalysisResult.job_id == job.id)
    )
    if existing is not None:
        return existing
    generated_at = _aware(response.generated_at)
    newer_job = db.scalar(
        select(AiAnalysisJob)
        .where(
            AiAnalysisJob.id != job.id,
            AiAnalysisJob.snapshot_hash != job.snapshot_hash,
            AiAnalysisJob.created_at >= job.created_at,
        )
        .order_by(AiAnalysisJob.created_at, AiAnalysisJob.id)
        .limit(1)
    )
    result = AiAnalysisResult(
        job_id=job.id,
        snapshot_hash=job.snapshot_hash,
        prompt_version=job.prompt_version,
        model=job.model,
        analysis=response.analysis.model_dump(mode="json"),
        source_through=job.source_through,
        generated_at=generated_at,
        fresh_until=(
            _aware(newer_job.created_at) + timedelta(seconds=max(1, stale_seconds))
            if newer_job is not None
            else None
        ),
    )
    db.add(result)
    job.status = "succeeded"
    job.finished_at = generated_at
    job.lease_until = None
    job.last_error_code = None
    db.commit()
    db.refresh(result)
    return result


def fail_analysis_job(
    db: Session,
    job: AiAnalysisJob,
    error_code: str,
    *,
    now: datetime | None = None,
    max_attempts: int = 4,
    backoff_base_seconds: int = 60,
) -> None:
    current = _aware(now)
    code = error_code if error_code in ALLOWED_ERROR_CODES else "internal"
    persisted = db.get(AiAnalysisJob, job.id)
    if persisted is None or persisted.status == "succeeded":
        return
    persisted.last_error_code = code
    persisted.lease_until = None
    if persisted.attempts >= max_attempts:
        persisted.status = "failed"
        persisted.finished_at = current
    else:
        persisted.status = "pending"
        persisted.available_at = current + timedelta(
            seconds=_backoff_seconds(persisted.attempts, backoff_base_seconds)
        )
    db.commit()


@dataclass(frozen=True)
class LatestAnalysis:
    status: Literal["ready", "stale", "pending", "unavailable"]
    analysis: AiAnalysis | None
    snapshot_hash: str | None
    generated_at: datetime | None
    source_through: datetime | None
    model: str | None
    prompt_version: str | None


def latest_analysis(db: Session, *, now: datetime | None = None) -> LatestAnalysis:
    current = _aware(now)
    result = db.scalar(
        select(AiAnalysisResult)
        .order_by(AiAnalysisResult.source_through.desc(), AiAnalysisResult.generated_at.desc())
        .limit(1)
    )
    pending = db.scalar(
        select(AiAnalysisJob.id)
        .where(AiAnalysisJob.status.in_(("pending", "processing")))
        .limit(1)
    )
    if result is None:
        return LatestAnalysis(
            status="pending" if pending is not None else "unavailable",
            analysis=None,
            snapshot_hash=None,
            generated_at=None,
            source_through=None,
            model=None,
            prompt_version=None,
        )
    if result.fresh_until is None:
        status: Literal["ready", "stale"] = "ready"
    elif _aware(result.fresh_until) >= current:
        status = "stale"
    else:
        return LatestAnalysis(
            status="unavailable",
            analysis=None,
            snapshot_hash=None,
            generated_at=None,
            source_through=None,
            model=None,
            prompt_version=None,
        )
    try:
        analysis = AiAnalysis.model_validate(result.analysis)
    except ValueError:
        # A stricter output contract may make a cached result from an older
        # prompt version invalid. Fail closed instead of breaking public GETs
        # or exposing text that no longer passes the active safety boundary.
        return LatestAnalysis(
            status="pending" if pending is not None else "unavailable",
            analysis=None,
            snapshot_hash=None,
            generated_at=None,
            source_through=None,
            model=None,
            prompt_version=None,
        )
    return LatestAnalysis(
        status=status,
        analysis=analysis,
        snapshot_hash=result.snapshot_hash,
        generated_at=_aware(result.generated_at),
        source_through=_aware(result.source_through),
        model=result.model,
        prompt_version=result.prompt_version,
    )


def public_analysis_payload(db: Session, *, now: datetime | None = None) -> dict[str, object]:
    """Serialize the cached result for the public read-only API.

    This function never invokes the gateway and deliberately has no generated
    fallback. Callers may expose ready/stale content or the status alone.
    """

    state = latest_analysis(db, now=now)
    return {
        "status": state.status,
        "ai_generated": True,
        "generated_at": state.generated_at.isoformat().replace("+00:00", "Z")
        if state.generated_at
        else None,
        "source_through": state.source_through.isoformat().replace("+00:00", "Z")
        if state.source_through
        else None,
        "model": state.model,
        "prompt_version": state.prompt_version,
        "snapshot_hash": state.snapshot_hash,
        "analysis": state.analysis.model_dump(mode="json") if state.analysis else None,
    }
