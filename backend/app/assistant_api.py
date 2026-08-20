from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import delete, func, literal_column, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .ai_contracts import canonical_snapshot_json, snapshot_evidence_keys
from .ai_queue import latest_analysis
from .ai_snapshot import build_analysis_snapshot
from .auth import AI_DATA_CONSENT_VERSION, AuthContext, require_csrf
from .auth_models import UserProfile
from .config import Settings, get_settings
from .db import SessionLocal, get_db
from .lab_models import AssistantJob, AssistantMessage, AssistantSummary, LabTextChunk


router = APIRouter(prefix="/api/v1/assistant", tags=["assistant"])


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


def _retrieval_terms(question: str) -> set[str]:
    terms = {
        token for token in re.findall(r"[a-zа-яё0-9]{4,}", question.casefold())
        if token not in {"который", "какие", "почему", "когда", "этого", "меня", "моих"}
    }
    return terms | {token[:6] for token in terms if len(token) >= 7}


def _chunk_evidence_key(chunk: LabTextChunk) -> str:
    identity = (
        f"{chunk.document_id}:{chunk.chunk_index}:{chunk.page_from}:{chunk.page_to}:"
        f"{sha256(chunk.content.encode('utf-8')).hexdigest()}"
    )
    return f"lab.text.{sha256(identity.encode('utf-8')).hexdigest()[:24]}"


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
    terms = _retrieval_terms(question)
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        language = literal_column("'russian'::regconfig")
        vector = func.to_tsvector(language, LabTextChunk.content)
        query = func.websearch_to_tsquery(language, question)
        candidates = list(
            db.scalars(
                select(LabTextChunk)
                .where(vector.op("@@")(query))
                .order_by(func.ts_rank_cd(vector, query).desc(), LabTextChunk.id.desc())
                .limit(8)
            )
        )
    else:
        candidates = list(
            db.scalars(select(LabTextChunk).order_by(LabTextChunk.id.desc()).limit(500))
        )
    ranked: list[tuple[int, LabTextChunk]] = []
    for chunk in candidates:
        folded = chunk.content.casefold()
        score = sum(folded.count(term) for term in terms)
        if score:
            ranked.append((score, chunk))
    relevant = [item for _, item in sorted(ranked, key=lambda pair: (-pair[0], pair[1].id))[:8]]
    chunk_evidence = {chunk.id: _chunk_evidence_key(chunk) for chunk in relevant}
    known.extend(chunk_evidence.values())
    analysis = latest_analysis(db)
    payload = {
        "health_snapshot": json.loads(canonical_snapshot_json(snapshot)),
        "current_validated_recommendations": analysis.analysis.model_dump(mode="json")
        if analysis.status == "ready" and analysis.analysis is not None else None,
        "older_conversation_summary": summary.content if summary else "",
        "recent_messages": [{"role": row.role, "content": row.content} for row in previous],
        "relevant_document_text": [
            {
                "evidence_key": chunk_evidence[chunk.id],
                "pages": [chunk.page_from, chunk.page_to],
                "text": chunk.content,
            }
            for chunk in relevant
        ],
        "user_question": question,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")), sorted(set(known))


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
