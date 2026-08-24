from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .ai_contracts import canonical_snapshot_json
from .ai_queue import latest_analysis
from .ai_snapshot import build_analysis_snapshot
from .auth import AI_DATA_CONSENT_VERSION, AuthContext, require_csrf
from .auth_models import UserProfile
from .config import Settings, get_settings
from .db import SessionLocal, get_db
from .evidence import snapshot_evidence_descriptors
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


@dataclass(frozen=True)
class ChatContext:
    prompt: str
    allowed_keys: tuple[str, ...]
    catalog: dict[str, dict[str, Any]]


_EVIDENCE_TARGETS = {
    "profile": "/profile",
    "weight": "/progress",
    "composition": "/composition",
    "activity": "/activity",
    "sleep": "/recovery",
    "recovery": "/recovery",
    "heart": "/recovery",
    "oxygen": "/recovery",
    "vo2": "/recovery",
    "pressure": "/pressure",
    "quality": "/data-quality",
    "correlation": "/activity",
    "laboratory": "/labs",
}


def _evidence_target(metric: str, path: str | None = None) -> dict[str, Any]:
    return {
        "path": path or _EVIDENCE_TARGETS.get(metric, "/"),
        "available": True,
    }


def _snapshot_catalog(snapshot) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    for item in snapshot.facts:
        catalog[item.key] = {
            "kind": "fact",
            "metric": item.scope,
            "value": item.value,
            "unit": item.unit,
            "period": item.period,
            "observed_on": item.observed_on.isoformat() if item.observed_on else None,
            "target": _evidence_target(item.scope),
        }
    for item in snapshot.series:
        catalog[item.key] = {
            "kind": "series",
            "metric": item.scope,
            "unit": item.unit,
            "range_start": item.points[0].day.isoformat(),
            "range_end": item.points[-1].day.isoformat(),
            "count": len(item.points),
            "target": _evidence_target(item.scope),
        }
    return catalog


def _lab_evidence(row: LabResult) -> dict[str, Any]:
    return {
        "kind": "laboratory_result",
        "metric": "laboratory",
        "label": row.analyte_name,
        "value_numeric": float(row.value_numeric) if row.value_numeric is not None else None,
        "value_text": row.value_text,
        "comparator": row.comparator,
        "unit": row.unit,
        "observed_on": row.observed_on.isoformat() if row.observed_on else None,
        "reference_low": float(row.reference_low) if row.reference_low is not None else None,
        "reference_high": float(row.reference_high) if row.reference_high is not None else None,
        "reference_text": row.reference_text,
        "status": row.status,
        "verification": "verified" if row.verification_status == "verified" else "unverified",
        "target": _evidence_target(
            "laboratory",
            f"/labs/documents/{row.document_id}#result-{row.id}",
        ),
    }


def _snapshot_lab_evidence(item, target: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "laboratory_result",
        "metric": "laboratory",
        "label": item.analyte,
        "value_numeric": item.value_numeric,
        "value_text": item.value_text,
        "comparator": item.comparator,
        "unit": item.unit,
        "observed_on": item.observed_on.isoformat(),
        "reference_low": item.reference_low,
        "reference_high": item.reference_high,
        "reference_text": item.reference_text,
        "status": item.status,
        "verification": "verified" if item.verified else "unverified",
        "target": target,
    }


def _history_evidence(family: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    metric = family.removesuffix("_daily")
    dates = sorted(
        str(value)[:10]
        for row in rows
        if (value := row.get("measured_at") or row.get("date"))
    )
    return {
        "kind": "history",
        "metric": metric,
        "period": "all",
        "range_start": dates[0] if dates else None,
        "range_end": dates[-1] if dates else None,
        "count": len(rows),
        "target": _evidence_target(metric),
    }


def _resolved_evidence_targets(
    db: Session | None,
    stored: dict[str, dict[str, Any]] | None,
) -> dict[str, dict[str, Any]] | None:
    """Keep captured evidence immutable while resolving whether deep links still exist."""

    if stored is None or db is None:
        return stored
    if not isinstance(stored, dict):
        return None
    resolved: dict[str, dict[str, Any]] = {}
    lab_targets: dict[str, str] = {}
    study_targets: dict[str, str] = {}
    for key, descriptor in stored.items():
        if not isinstance(descriptor, dict):
            continue
        copy = {**descriptor}
        target = descriptor.get("target")
        if isinstance(target, dict):
            copy["target"] = {**target}
            path = target.get("path")
            kind = descriptor.get("kind")
            if kind == "laboratory_result" and isinstance(path, str):
                marker = "#result-"
                if path.startswith("/labs/documents/") and marker in path:
                    lab_targets[key] = path.split(marker, 1)[1]
                else:
                    copy["target"]["available"] = False
            elif kind in {"study_finding", "study_conclusion"} and isinstance(path, str):
                prefix = "/studies/"
                if path.startswith(prefix) and "#" in path:
                    study_targets[key] = path[len(prefix) :].split("#", 1)[0]
                else:
                    copy["target"]["available"] = False
        resolved[key] = copy

    live_labs = set(
        db.scalars(
            select(LabResult.id).where(
                LabResult.id.in_(set(lab_targets.values())),
                LabResult.deleted.is_(False),
            )
        )
    ) if lab_targets else set()
    live_studies = set(
        db.scalars(
            select(StudyDocument.id).where(
                StudyDocument.id.in_(set(study_targets.values())),
                StudyDocument.status == "complete",
            )
        )
    ) if study_targets else set()
    for key, result_id in lab_targets.items():
        resolved[key]["target"]["available"] = result_id in live_labs
    for key, study_id in study_targets.items():
        resolved[key]["target"]["available"] = study_id in live_studies
    return resolved


def _message(row: AssistantMessage, db: Session | None = None) -> dict:
    return {
        "id": row.id,
        "role": row.role,
        "status": row.status,
        "content": row.content,
        "draft_segments": row.draft_segments or [],
        "evidence_keys": row.evidence_keys or [],
        "evidence": _resolved_evidence_targets(db, row.evidence_snapshot)
        if row.role == "assistant" and row.status == "complete"
        else None,
        "error_code": row.error_code,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _require_consent(db: Session) -> None:
    profile = db.get(UserProfile, 1)
    if profile is None or profile.ai_data_consent_version != AI_DATA_CONSENT_VERSION:
        raise HTTPException(status_code=409, detail="ai_data_consent_required")


def build_chat_context(db: Session, settings: Settings, question: str) -> ChatContext:
    snapshot = build_analysis_snapshot(db, settings.tz, user_height_cm=settings.user_height_cm)
    catalog = _snapshot_catalog(snapshot)
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
        catalog[key] = _lab_evidence(row)
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
    for item in snapshot.labs:
        target = catalog.get(item.key, {}).get("target")
        catalog[item.key] = _snapshot_lab_evidence(
            item,
            target if isinstance(target, dict) else _evidence_target("laboratory"),
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
            catalog[key] = {
                "kind": "study_finding",
                "metric": "study",
                "label": row.modality,
                "text": finding,
                "observed_on": row.observed_on.isoformat() if row.observed_on else None,
                "verification": "verified" if row.verified else "unverified",
                "target": _evidence_target(
                    "study",
                    f"/studies/{row.id}#finding-{index + 1}",
                ),
            }
            finding_items.append({"evidence_key": key, "text": finding})
        conclusion = None
        if row.conclusion:
            conclusion_key = f"{base}.conclusion"
            catalog[conclusion_key] = {
                "kind": "study_conclusion",
                "metric": "study",
                "label": row.modality,
                "text": row.conclusion,
                "observed_on": row.observed_on.isoformat() if row.observed_on else None,
                "verification": "verified" if row.verified else "unverified",
                "target": _evidence_target(
                    "study",
                    f"/studies/{row.id}#conclusion",
                ),
            }
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
    for family, rows in history.items():
        catalog[f"history.{family}"] = _history_evidence(family, rows)
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
    return ChatContext(
        prompt=prompt,
        allowed_keys=tuple(sorted(catalog)),
        catalog=catalog,
    )


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
    return _message(assistant, db)


@router.get("/messages")
def list_messages(db: Session = Depends(get_db)) -> dict:
    rows = list(db.scalars(select(AssistantMessage).order_by(AssistantMessage.created_at, AssistantMessage.id)))
    analysis = latest_analysis(db)
    recommendations = []
    analysis_id = None
    evidence = {}
    if (
        analysis.status == "ready"
        and analysis.result_id is not None
        and analysis.analysis is not None
        and analysis.snapshot is not None
    ):
        analysis_id = analysis.result_id
        recommendations = [
            {
                "id": f"recommendation-{index + 1}",
                "title": item.title,
                "text": item.text,
                "evidence_ids": list(item.evidence_keys),
            }
            for index, item in enumerate(analysis.analysis.recommendations)
        ]
        evidence = snapshot_evidence_descriptors(
            analysis.snapshot,
            [key for item in analysis.analysis.recommendations for key in item.evidence_keys],
            db=db,
        )
    return {
        "items": [_message(row, db) for row in rows],
        "analysis_id": analysis_id,
        "recommendations": recommendations,
        "evidence": evidence,
    }


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
    return _message(assistant, db)


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
                    yield f"event: complete\ndata: {json.dumps(_message(row, db), ensure_ascii=False, default=str)}\n\n"
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
    row.evidence_keys, row.evidence_snapshot = [], None
    job.status, job.error_code, job.attempts = "pending", None, 0
    job.available_at, job.lease_until, job.finished_at = datetime.now(timezone.utc), None, None
    db.commit()
    return _message(row, db)


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
