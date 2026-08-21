from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .ai_contracts import AI_MODEL
from .ai_snapshot import enqueue_current_analysis
from .assistant_api import ChatContextTooLarge, build_chat_context
from .config import Settings
from .lab_contracts import (
    AnalyteGuideQuery,
    ChatAnswer,
    ChatSegment,
    GatewayChatRequest,
    GatewayChatResponse,
    GatewayAnalyteGuideRequest,
    GatewayAnalyteGuideResponse,
    GatewayLabRequest,
    GatewayLabResponse,
    LAB_ANALYTE_GUIDE_PROMPT_VERSION,
    LAB_EXTRACTION_PROMPT_VERSION,
    validate_chat_answer,
)
from .lab_models import (
    AssistantJob,
    AssistantMessage,
    AssistantSummary,
    LabDocument,
    LabAnalyte,
    LabAnalyteGuideJob,
    LabExtraction,
    LabProcessingJob,
    LabReport,
    LabResult,
    LabTextChunk,
    StudyDocument,
    StudyProcessingJob,
)
from .labs import (
    LAB_EXTRACTION_CHUNK_CHARS,
    bounded_page_chunks,
    claim_analyte_guide_jobs,
    claim_lab_job,
    has_analyte_guide,
    missing_analyte_guides,
    original_bytes,
    persist_analyte_guides,
    persist_extraction,
    replace_text_chunks,
)
from .studies import claim_study_job, structure_study_text


ANALYTE_GUIDE_BATCH_SIZE = 5


class WorkError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code if code in {
            "parser_unavailable", "parser_rejected", "gateway_busy", "gateway_unavailable",
            "gateway_rejected", "timeout", "invalid_response", "original_missing",
            "original_changed", "internal",
            "context_too_large",
        } else "internal"


class LabAssistantGateway:
    def __init__(self, settings: Settings, http: httpx.Client | None = None):
        self.settings = settings
        self.http = http or httpx.Client(timeout=httpx.Timeout(settings.lab_parser_timeout_seconds, connect=10))
        self._owns_http = http is None

    def close(self) -> None:
        if self._owns_http:
            self.http.close()

    def parse(self, document: LabDocument, content: bytes) -> dict:
        try:
            response = self.http.post(
                f"{self.settings.lab_parser_url}/parse",
                files={"file": (document.original_filename, content, document.media_type)},
                timeout=httpx.Timeout(self.settings.lab_parser_timeout_seconds, connect=10),
            )
        except httpx.TimeoutException as exc:
            raise WorkError("parser_unavailable") from exc
        except httpx.HTTPError as exc:
            raise WorkError("parser_unavailable") from exc
        if response.status_code == 422:
            raise WorkError("parser_rejected")
        if response.status_code != 200 or len(response.content) > 8 * 1024 * 1024:
            raise WorkError("parser_unavailable")
        try:
            payload = response.json()
            if not isinstance(payload.get("text"), str) or not isinstance(payload.get("pages"), list):
                raise ValueError
            return payload
        except (ValueError, AttributeError) as exc:
            raise WorkError("invalid_response") from exc

    def extract(self, request: GatewayLabRequest) -> GatewayLabResponse:
        try:
            response = self.http.post(
                f"{self.settings.ai_gateway_url}/extract-labs",
                json=request.model_dump(mode="json"),
                timeout=httpx.Timeout(self.settings.ai_gateway_timeout_seconds, connect=10),
            )
        except httpx.TimeoutException as exc:
            raise WorkError("timeout") from exc
        except httpx.HTTPError as exc:
            raise WorkError("gateway_unavailable") from exc
        if response.status_code == 429:
            raise WorkError("gateway_busy")
        if response.status_code in {502, 503, 504}:
            raise WorkError("gateway_unavailable" if response.status_code != 504 else "timeout")
        if response.status_code != 200 or len(response.content) > 300_000:
            raise WorkError("gateway_rejected")
        try:
            return GatewayLabResponse.model_validate(response.json())
        except ValueError as exc:
            raise WorkError("invalid_response") from exc

    def guides(
        self,
        request: GatewayAnalyteGuideRequest,
    ) -> GatewayAnalyteGuideResponse:
        try:
            response = self.http.post(
                f"{self.settings.ai_gateway_url}/generate-analyte-guides",
                json=request.model_dump(mode="json"),
                timeout=httpx.Timeout(
                    self.settings.ai_gateway_timeout_seconds,
                    connect=10,
                ),
            )
        except httpx.TimeoutException as exc:
            raise WorkError("timeout") from exc
        except httpx.HTTPError as exc:
            raise WorkError("gateway_unavailable") from exc
        if response.status_code == 429:
            raise WorkError("gateway_busy")
        if response.status_code in {502, 503, 504}:
            raise WorkError(
                "timeout" if response.status_code == 504 else "gateway_unavailable"
            )
        if response.status_code != 200 or len(response.content) > 150_000:
            raise WorkError("gateway_rejected")
        try:
            parsed = GatewayAnalyteGuideResponse.model_validate(response.json())
        except ValueError as exc:
            raise WorkError("invalid_response") from exc
        requested = {item.analyte_id for item in request.analytes}
        returned = [item.analyte_id for item in parsed.guides]
        if len(returned) != len(set(returned)) or set(returned) != requested:
            raise WorkError("invalid_response")
        return parsed

    def chat(self, request: GatewayChatRequest, on_event) -> GatewayChatResponse:
        try:
            with self.http.stream(
                "POST",
                f"{self.settings.ai_gateway_url}/chat",
                json=request.model_dump(mode="json"),
                timeout=httpx.Timeout(self.settings.ai_gateway_timeout_seconds, connect=10),
            ) as response:
                if response.status_code == 429:
                    raise WorkError("gateway_busy")
                if response.status_code != 200:
                    raise WorkError("gateway_unavailable")
                final: GatewayChatResponse | None = None
                for line in response.iter_lines():
                    if not line or len(line.encode("utf-8")) > 100_000:
                        continue
                    try:
                        event = json.loads(line)
                    except ValueError as exc:
                        raise WorkError("invalid_response") from exc
                    if event.get("type") == "draft_segment":
                        segment = ChatSegment.model_validate(event.get("segment"))
                        on_event(segment)
                    elif event.get("type") == "complete":
                        final = GatewayChatResponse.model_validate(event.get("response"))
                    elif event.get("type") == "error":
                        raise WorkError(str(event.get("code") or "gateway_rejected"))
                if final is None:
                    raise WorkError("invalid_response")
                return final
        except WorkError:
            raise
        except httpx.TimeoutException as exc:
            raise WorkError("timeout") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise WorkError("gateway_unavailable") from exc


def _fail_lab(db: Session, job: LabProcessingJob, document: LabDocument | None, error: WorkError, now: datetime) -> None:
    job.lease_until = None
    job.error_code = error.code
    if job.attempts < 3 and error.code not in {"parser_rejected", "original_missing", "original_changed"}:
        job.status = "pending"
        job.available_at = now + timedelta(seconds=30 * (2 ** (job.attempts - 1)))
        if document:
            document.status = "queued"
            document.processing_stage = "queued"
            document.progress_percent = 0
    else:
        job.status = "failed"
        job.finished_at = now
        if document:
            document.status = "failed"
            document.processing_stage = "failed"
            document.error_code = error.code
    db.commit()


def _generate_analyte_guides(
    db: Session,
    gateway: LabAssistantGateway,
    analytes: list[LabAnalyte],
    now: datetime,
) -> None:
    for offset in range(0, len(analytes), ANALYTE_GUIDE_BATCH_SIZE):
        chunk = analytes[offset : offset + ANALYTE_GUIDE_BATCH_SIZE]
        request = GatewayAnalyteGuideRequest(
            contract_version=LAB_ANALYTE_GUIDE_PROMPT_VERSION,
            model=AI_MODEL,
            analytes=[
                AnalyteGuideQuery(
                    analyte_id=analyte.id,
                    analyte_name=analyte.display_name,
                )
                for analyte in chunk
            ],
        )
        persist_analyte_guides(db, gateway.guides(request), now=now)


def process_lab_job(db: Session, settings: Settings, gateway: LabAssistantGateway, now: datetime) -> bool:
    job = claim_lab_job(db, now, lease_seconds=max(300, settings.ai_lease_seconds))
    if job is None:
        return False
    document = db.get(LabDocument, job.document_id)
    try:
        if document is None:
            raise WorkError("original_missing")
        try:
            content = original_bytes(db, document, settings.lab_storage_dir)
        except Exception as exc:
            code = str(exc) if str(exc) in {"original_missing", "original_changed"} else "original_missing"
            raise WorkError(code) from exc
        parsed = gateway.parse(document, content)
        document.processing_stage = "extracting"
        document.progress_percent = 40
        db.commit()
        pages = parsed["pages"]
        extraction_chunks: list[tuple[int, str, GatewayLabResponse]] = []
        chunks = bounded_page_chunks(pages, LAB_EXTRACTION_CHUNK_CHARS)
        for index, (page_from, page_to, text) in enumerate(chunks):
            request = GatewayLabRequest(
                document_id=document.id,
                chunk_index=index,
                page_from=page_from,
                page_to=page_to,
                text=text,
                contract_version=LAB_EXTRACTION_PROMPT_VERSION,
                model=AI_MODEL,
            )
            extraction_chunks.append((index, text, gateway.extract(request)))
            document.processing_stage = "extracting"
            document.progress_percent = 40 + round(45 * (index + 1) / max(1, len(chunks)))
            db.commit()

        db.execute(delete(LabResult).where(LabResult.document_id == document.id))
        db.execute(delete(LabReport).where(LabReport.document_id == document.id))
        db.execute(delete(LabExtraction).where(LabExtraction.document_id == document.id))
        db.execute(delete(LabTextChunk).where(LabTextChunk.document_id == document.id))
        document.extracted_text = parsed["text"]
        document.parser_pages = pages
        document.page_count = int(parsed.get("page_count") or len(pages))
        document.verified = False
        document.error_code = None
        replace_text_chunks(db, document, pages)
        source_offset = 0
        for index, source_text, response in extraction_chunks:
            source_offset += persist_extraction(
                db,
                document,
                response.extraction,
                chunk_index=index,
                model=AI_MODEL,
                contract_version=LAB_EXTRACTION_PROMPT_VERSION,
                source_offset=source_offset,
                source_text=source_text,
            )
        _generate_analyte_guides(
            db,
            gateway,
            missing_analyte_guides(db, document_id=document.id),
            now,
        )
        document.status = "complete"
        document.processing_stage = "complete"
        document.progress_percent = 100
        document.completed_at = now
        job.status = "success"
        job.lease_until = None
        job.finished_at = now
        job.error_code = None
        db.commit()
        enqueue_current_analysis(db, settings, trigger="manual", debounce_seconds=0)
        return True
    except WorkError as exc:
        db.rollback()
        job = db.get(LabProcessingJob, job.id)
        document = db.get(LabDocument, job.document_id) if job else None
        if job is None:
            return True
        _fail_lab(db, job, document, exc, now)
        return True
    except Exception:
        db.rollback()
        job = db.get(LabProcessingJob, job.id)
        document = db.get(LabDocument, job.document_id) if job else None
        if job:
            _fail_lab(db, job, document, WorkError("internal"), now)
        return True


def process_analyte_guide_job(
    db: Session,
    settings: Settings,
    gateway: LabAssistantGateway,
    now: datetime,
) -> bool:
    jobs = claim_analyte_guide_jobs(
        db,
        now,
        lease_seconds=max(180, settings.ai_lease_seconds),
    )
    if not jobs:
        return False
    job_ids = [job.id for job in jobs]
    try:
        analytes = [
            analyte
            for job in jobs
            if (analyte := db.get(LabAnalyte, job.analyte_id)) is not None
            and not has_analyte_guide(db, analyte.id)
        ]
        _generate_analyte_guides(db, gateway, analytes, now)
        for job in jobs:
            job.status = "success"
            job.lease_until = None
            job.error_code = None
            job.finished_at = now
        db.commit()
    except WorkError as exc:
        db.rollback()
        for job_id in job_ids:
            job = db.get(LabAnalyteGuideJob, job_id)
            if job is None:
                continue
            job.lease_until = None
            job.error_code = exc.code
            if job.attempts < 3:
                job.status = "pending"
                job.available_at = now + timedelta(
                    seconds=30 * (2 ** (job.attempts - 1))
                )
            else:
                job.status = "failed"
                job.finished_at = now
        db.commit()
    except Exception:
        db.rollback()
        for job_id in job_ids:
            job = db.get(LabAnalyteGuideJob, job_id)
            if job is None:
                continue
            job.lease_until = None
            job.error_code = "internal"
            if job.attempts < 3:
                job.status = "pending"
                job.available_at = now + timedelta(
                    seconds=30 * (2 ** (job.attempts - 1))
                )
            else:
                job.status = "failed"
                job.finished_at = now
        db.commit()
    return True


def _fail_study(
    db: Session,
    job: StudyProcessingJob,
    document: StudyDocument | None,
    error: WorkError,
    now: datetime,
) -> None:
    job.lease_until = None
    job.error_code = error.code
    if job.attempts < 3 and error.code not in {"parser_rejected", "original_missing", "original_changed"}:
        job.status = "pending"
        job.available_at = now + timedelta(seconds=30 * (2 ** (job.attempts - 1)))
        if document is not None:
            document.status = "queued"
            document.processing_stage = "queued"
            document.progress_percent = 0
    else:
        job.status = "failed"
        job.finished_at = now
        if document is not None:
            document.status = "failed"
            document.processing_stage = "failed"
            document.error_code = error.code
    db.commit()


def process_study_job(
    db: Session, settings: Settings, gateway: LabAssistantGateway, now: datetime
) -> bool:
    job = claim_study_job(db, now, lease_seconds=max(300, settings.ai_lease_seconds))
    if job is None:
        return False
    document = db.get(StudyDocument, job.document_id)
    try:
        if document is None:
            raise WorkError("original_missing")
        try:
            content = original_bytes(db, document, settings.lab_storage_dir)  # type: ignore[arg-type]
        except Exception as exc:
            code = str(exc) if str(exc) in {"original_missing", "original_changed"} else "original_missing"
            raise WorkError(code) from exc
        parsed = gateway.parse(document, content)  # type: ignore[arg-type]
        document.processing_stage = "structuring"
        document.progress_percent = 70
        db.commit()
        findings, conclusion = structure_study_text(parsed["text"])
        document.extracted_text = parsed["text"]
        document.page_count = int(parsed.get("page_count") or len(parsed.get("pages") or []))
        document.findings = findings
        document.conclusion = conclusion
        document.status = "complete"
        document.processing_stage = "complete"
        document.progress_percent = 100
        document.verified = False
        document.error_code = None
        document.completed_at = now
        job.status = "success"
        job.lease_until = None
        job.finished_at = now
        job.error_code = None
        db.commit()
        enqueue_current_analysis(db, settings, trigger="manual", debounce_seconds=0)
        return True
    except WorkError as exc:
        _fail_study(db, job, document, exc, now)
        return True
    except Exception:
        db.rollback()
        job = db.get(StudyProcessingJob, job.id)
        document = db.get(StudyDocument, job.document_id) if job else None
        if job is not None:
            _fail_study(db, job, document, WorkError("internal"), now)
        return True
def _claim_assistant_job(db: Session, now: datetime, lease_seconds: int) -> AssistantJob | None:
    expired = list(db.scalars(select(AssistantJob).where(AssistantJob.status == "processing", AssistantJob.lease_until < now)))
    for job in expired:
        job.status = "pending" if job.attempts < 2 else "failed"
        job.available_at, job.lease_until, job.error_code = now, None, "lease_expired"
        assistant = db.get(AssistantMessage, job.assistant_message_id)
        if assistant:
            assistant.status = "queued" if job.status == "pending" else "failed"
            assistant.draft_segments = []
            assistant.error_code = job.error_code
    db.flush()
    job = db.scalar(
        select(AssistantJob)
        .where(AssistantJob.status == "pending", AssistantJob.available_at <= now)
        .order_by(AssistantJob.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        db.commit()
        return None
    job.status, job.attempts = "processing", job.attempts + 1
    job.lease_until = now + timedelta(seconds=lease_seconds)
    assistant = db.get(AssistantMessage, job.assistant_message_id)
    if assistant:
        assistant.status, assistant.error_code = "streaming", None
    db.commit()
    return job


def _update_summary(db: Session) -> None:
    rows = list(
        db.scalars(
            select(AssistantMessage)
            .where(AssistantMessage.status == "complete")
            .order_by(AssistantMessage.created_at.desc(), AssistantMessage.id.desc())
        )
    )
    older = list(reversed(rows[12:]))
    if not older:
        return
    content = "\n".join(f"{row.role}: {row.content}" for row in older)[-20_000:]
    summary = db.get(AssistantSummary, 1)
    if summary is None:
        summary = AssistantSummary(id=1, content=content, summarized_through=older[-1].created_at)
        db.add(summary)
    else:
        summary.content, summary.summarized_through = content, older[-1].created_at


def process_assistant_job(db: Session, settings: Settings, gateway: LabAssistantGateway, now: datetime) -> bool:
    job = _claim_assistant_job(db, now, max(settings.ai_gateway_timeout_seconds + 30, 180))
    if job is None:
        return False
    user = db.get(AssistantMessage, job.user_message_id)
    assistant = db.get(AssistantMessage, job.assistant_message_id)
    try:
        if user is None or assistant is None:
            raise WorkError("internal")
        prompt, evidence = build_chat_context(db, settings, user.content)
        request = GatewayChatRequest(
            model=AI_MODEL,
            contract_version="amigo-health-chat-v2",
            message_id=assistant.id,
            attempt=min(2, max(1, job.attempts)),
            prompt=prompt,
            allowed_evidence_keys=evidence,
        )

        def draft(segment: ChatSegment) -> None:
            validate_chat_answer(ChatAnswer(segments=[segment]), set(evidence))
            assistant.draft_segments = [*(assistant.draft_segments or []), segment.model_dump(mode="json")]
            assistant.status = "streaming"
            db.commit()

        response = gateway.chat(request, draft)
        validate_chat_answer(response.answer, set(evidence))
        assistant.status = "validating"
        db.commit()
        assistant.content = "\n\n".join(segment.text for segment in response.answer.segments)
        assistant.evidence_keys = list(dict.fromkeys(
            key for segment in response.answer.segments for key in segment.evidence_keys
        ))
        assistant.draft_segments = [segment.model_dump(mode="json") for segment in response.answer.segments]
        assistant.status, assistant.completed_at, assistant.error_code = "complete", now, None
        job.status, job.finished_at, job.lease_until, job.error_code = "success", now, None, None
        _update_summary(db)
        db.commit()
        return True
    except ChatContextTooLarge:
        error = WorkError("context_too_large")
    except WorkError as exc:
        error = exc
    except Exception:
        db.rollback()
        job = db.get(AssistantJob, job.id)
        assistant = db.get(AssistantMessage, job.assistant_message_id) if job else None
        error = WorkError("internal")
    if job is not None:
        job.lease_until, job.error_code = None, error.code
        if error.code != "context_too_large" and job.attempts < settings.assistant_max_attempts:
            job.status = "pending"
            job.available_at = now + timedelta(seconds=10 * job.attempts)
            if assistant:
                assistant.status, assistant.draft_segments, assistant.error_code = "queued", [], error.code
        else:
            job.status, job.finished_at = "failed", now
            if assistant:
                assistant.status, assistant.draft_segments = "failed", []
                assistant.error_code, assistant.completed_at = error.code, now
        db.commit()
    return True
