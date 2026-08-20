from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .ai_contracts import canonical_snapshot_json, snapshot_evidence_keys
from .ai_queue import latest_analysis
from .ai_snapshot import build_analysis_snapshot
from .auth import AI_DATA_CONSENT_VERSION, AuthContext, require_csrf
from .auth_models import UserProfile
from .config import Settings, get_settings
from .db import SessionLocal, get_db
from .health_analytics import activity_series, recovery_series
from .lab_models import AssistantJob, AssistantMessage, AssistantSummary, LabResult, StudyDocument
from .service import pressure_series, weight_series


router = APIRouter(prefix="/api/v1/assistant", tags=["assistant"])
MAX_CHAT_CONTEXT_BYTES = 560_000


class ChatContextTooLarge(ValueError):
    pass


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MessageCreate(StrictModel):
    content: Annotated[str, StringConstraints(min_length=1, max_length=4000)]
    client_request_id: Annotated[str, StringConstraints(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")]


def _message(row: AssistantMessage) -> dict:
    return {
        "id": row.id,
        "role": row.role,
        "status": row.status,
        "content": row.content,
        "draft_segments": row.draft_segments or [],
        "evidence_keys": row.evidence_keys or [],
        "error_code": row.error_code,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _require_consent(db: Session) -> None:
    profile = db.get(UserProfile, 1)
    if profile is None or profile.ai_data_consent_version != AI_DATA_CONSENT_VERSION:
        raise HTTPException(status_code=409, detail="ai_data_consent_required")


def build_chat_context(db: Session, settings: Settings, question: str) -> tuple[str, list[str]]:
    snapshot = build_analysis_snapshot(db, settings.tz, user_height_cm=settings.user_height_cm)
    known = sorted(snapshot_evidence_keys(snapshot))
    previous = list(
        db.scalars(
            select(AssistantMessage)
            .where(AssistantMessage.status == "complete")
            .order_by(AssistantMessage.created_at.desc(), AssistantMessage.id.desc())
            .limit(12)
        )
    )
    previous.reverse()
    summary = db.get(AssistantSummary, 1)
    lab_rows = list(
        db.scalars(
            select(LabResult)
            .where(LabResult.deleted.is_(False))
            .order_by(LabResult.observed_on, LabResult.created_at, LabResult.id)
        )
    )
    laboratory: list[dict] = []
    for row in lab_rows:
        key = f"lab.{sha256(row.id.encode()).hexdigest()[:20]}"
        known.append(key)
        laboratory.append(
            {
                "evidence_key": key,
                "analyte": row.analyte_name,
                "value_numeric": float(row.value_numeric) if row.value_numeric is not None else None,
                "value_text": row.value_text,
                "comparator": row.comparator,
                "unit": row.unit,
                "observed_on": row.observed_on.isoformat() if row.observed_on else None,
                "specimen": row.specimen,
                "method": row.method,
                "reference_low": float(row.reference_low) if row.reference_low is not None else None,
                "reference_high": float(row.reference_high) if row.reference_high is not None else None,
                "reference_text": row.reference_text,
                "status": row.status,
                "verified": row.verification_status == "verified",
            }
        )
    study_rows = list(
        db.scalars(
            select(StudyDocument)
            .where(StudyDocument.status == "complete")
            .order_by(StudyDocument.observed_on, StudyDocument.created_at, StudyDocument.id)
        )
    )
    studies: list[dict] = []
    for row in study_rows:
        base = f"study.{sha256(row.id.encode()).hexdigest()[:20]}"
        finding_items = []
        for index, finding in enumerate(row.findings or []):
            key = f"{base}.finding.{index + 1}"
            known.append(key)
            finding_items.append({"evidence_key": key, "text": finding})
        conclusion = None
        if row.conclusion:
            conclusion_key = f"{base}.conclusion"
            known.append(conclusion_key)
            conclusion = {"evidence_key": conclusion_key, "text": row.conclusion}
        studies.append(
            {
                "modality": row.modality,
                "observed_on": row.observed_on.isoformat() if row.observed_on else None,
                "verified": row.verified,
                "findings": finding_items,
                "conclusion": conclusion,
            }
        )
    history = {
        "weight": weight_series(db, settings.tz, "all").get("points") or [],
        "pressure": pressure_series(db, settings.tz, "all").get("points") or [],
        "activity_daily": activity_series(db, settings.tz, "all").get("daily") or [],
        "recovery_daily": recovery_series(db, settings.tz, "all").get("daily") or [],
    }
    for family in history:
        known.append(f"history.{family}")
    analysis = latest_analysis(db)
    payload = {
        "health_snapshot": json.loads(canonical_snapshot_json(snapshot)),
        "current_validated_recommendations": analysis.analysis.model_dump(mode="json")
        if analysis.status == "ready" and analysis.analysis is not None else None,
        "older_conversation_summary": summary.content if summary else "",
        "recent_messages": [{"role": row.role, "content": row.content} for row in previous],
        "all_structured_laboratory_results": laboratory,
        "all_structured_study_findings": studies,
        "aggregate_health_history": {
            family: {"evidence_key": f"history.{family}", "items": rows}
            for family, rows in history.items()
        },
        "user_question": question,
    }
    prompt = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(prompt.encode("utf-8")) > MAX_CHAT_CONTEXT_BYTES:
        raise ChatContextTooLarge("context_too_large")
    return prompt, sorted(set(known))


def _response_for_request(db: Session, client_request_id: str) -> dict | None:
    existing = db.scalar(
        select(AssistantMessage).where(
            AssistantMessage.client_request_id == client_request_id
        )
    )
    if existing is None:
        return None
    job = db.scalar(
        select(AssistantJob).where(AssistantJob.user_message_id == existing.id)
    )
    assistant = db.get(AssistantMessage, job.assistant_message_id) if job else existing
    return _message(assistant)


@router.get("/messages")
def list_messages(db: Session = Depends(get_db)) -> dict:
    rows = list(db.scalars(select(AssistantMessage).order_by(AssistantMessage.created_at, AssistantMessage.id)))
    analysis = latest_analysis(db)
    recommendations = []
    if analysis.status == "ready" and analysis.analysis is not None:
        recommendations = [item.model_dump(mode="json") for item in analysis.analysis.recommendations]
    return {"items": [_message(row) for row in rows], "recommendations": recommendations}


@router.post("/messages", status_code=status.HTTP_202_ACCEPTED)
def create_message(
    payload: MessageCreate,
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    _require_consent(db)
    existing_response = _response_for_request(db, payload.client_request_id)
    if existing_response is not None:
        return existing_response
    in_flight = db.scalar(
        select(AssistantMessage.id).where(
            AssistantMessage.role == "assistant",
            AssistantMessage.status.in_(["queued", "streaming", "validating"]),
        ).limit(1)
    )
    if in_flight is not None:
        raise HTTPException(status_code=409, detail="assistant_turn_in_progress")
    now = datetime.now(timezone.utc)
    user = AssistantMessage(
        id=str(uuid4()), client_request_id=payload.client_request_id, role="user",
        status="complete", content=payload.content, draft_segments=[], evidence_keys=[],
        created_at=now, updated_at=now, completed_at=now,
    )
    assistant = AssistantMessage(
        id=str(uuid4()), role="assistant", status="queued", content="",
        draft_segments=[], evidence_keys=[], created_at=now, updated_at=now,
    )
    try:
        db.add_all([user, assistant])
        db.flush()
        db.add(
            AssistantJob(
                user_message_id=user.id,
                assistant_message_id=assistant.id,
                status="pending",
                attempts=0,
                available_at=now,
                created_at=now,
            )
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        existing_response = _response_for_request(db, payload.client_request_id)
        if existing_response is None:
            raise
        return existing_response
    return _message(assistant)


@router.get("/messages/{message_id}/events")
async def message_events(message_id: str, request: Request) -> StreamingResponse:
    async def events():
        sent_segments = 0
        last_status: str | None = None
        last_heartbeat = datetime.now(timezone.utc)
        deadline = last_heartbeat + timedelta(minutes=10)
        while datetime.now(timezone.utc) < deadline and not await request.is_disconnected():
            with SessionLocal() as db:
                row = db.get(AssistantMessage, message_id)
                if row is None or row.role != "assistant":
                    yield "event: error\ndata: {\"code\":\"message_not_found\"}\n\n"
                    return
                if row.status != last_status:
                    yield f"event: status\ndata: {json.dumps({'status': row.status})}\n\n"
                    last_status = row.status
                segments = row.draft_segments or []
                if len(segments) < sent_segments:
                    yield "event: reset\ndata: {}\n\n"
                    sent_segments = 0
                while sent_segments < len(segments):
                    yield f"event: draft_segment\ndata: {json.dumps(segments[sent_segments], ensure_ascii=False)}\n\n"
                    sent_segments += 1
                if row.status == "complete":
                    yield f"event: complete\ndata: {json.dumps(_message(row), ensure_ascii=False, default=str)}\n\n"
                    return
                if row.status == "failed":
                    if sent_segments:
                        yield "event: reset\ndata: {}\n\n"
                    yield f"event: error\ndata: {json.dumps({'code': row.error_code or 'assistant_failed'})}\n\n"
                    return
            now = datetime.now(timezone.utc)
            if (now - last_heartbeat).total_seconds() >= 15:
                yield ": heartbeat\n\n"
                last_heartbeat = now
            await asyncio.sleep(0.75)
        yield "event: error\ndata: {\"code\":\"stream_timeout\"}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/messages/{message_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_message(
    message_id: str,
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    row = db.get(AssistantMessage, message_id)
    job = db.scalar(select(AssistantJob).where(AssistantJob.assistant_message_id == message_id))
    if row is None or job is None or row.status != "failed":
        raise HTTPException(status_code=409, detail="message_not_retryable")
    row.status, row.error_code, row.content, row.draft_segments = "queued", None, "", []
    job.status, job.error_code, job.attempts = "pending", None, 0
    job.available_at, job.lease_until, job.finished_at = datetime.now(timezone.utc), None, None
    db.commit()
    return _message(row)


@router.delete("/history", status_code=status.HTTP_204_NO_CONTENT)
def clear_history(
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    in_flight = db.scalar(select(AssistantJob.id).where(AssistantJob.status == "processing").limit(1))
    if in_flight is not None:
        raise HTTPException(status_code=409, detail="assistant_turn_in_progress")
    db.execute(delete(AssistantSummary))
    db.execute(delete(AssistantMessage))
    db.commit()
    return None
