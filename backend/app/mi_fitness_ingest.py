from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from pydantic import ValidationError
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .health_ingest import (
    MAX_BODY_BYTES,
    MAX_SIGNATURE_AGE_SECONDS,
    HealthIngestError,
    _aware,
    _iso_utc,
    _normalise_record,
    _validate_header_token,
    _verify_signature,
)
from .health_models import HealthConnectBatch, HealthConnectDevice, HealthConnectRecord
from .health_schemas import (
    MI_FITNESS_RECORD_TYPES,
    MiFitnessBatchAcceptedResponse,
    MiFitnessBatchInput,
    MiFitnessStatusInput,
    MiFitnessStatusResponse,
)
from .mi_fitness_models import (
    MiFitnessBatch,
    MiFitnessCoverage,
    MiFitnessRecord,
    MiFitnessSource,
    MiFitnessStatusReport,
)
from .models import Outbox


ACTIVATION_WINDOW = timedelta(days=3)
ACTIVATION_END_TOLERANCE = timedelta(minutes=15)


def _signed_device(
    db: Session,
    *,
    device_id: str,
    timestamp: str,
    nonce: str,
    request_id: str,
    signature: str,
    raw_body: bytes,
    now: datetime,
) -> tuple[HealthConnectDevice, datetime, str]:
    if len(raw_body) > MAX_BODY_BYTES:
        raise HealthIngestError(413, "batch_too_large")
    if not raw_body:
        raise HealthIngestError(400, "empty_batch")
    _validate_header_token(device_id, "device_id", 36)
    _validate_header_token(nonce, "nonce", 128)
    _validate_header_token(request_id, "batch_id", 128)
    if len(signature) > 512:
        raise HealthIngestError(400, "invalid_signature_header")
    try:
        client_time = datetime.fromtimestamp(int(timestamp), timezone.utc)
    except (ValueError, OverflowError, OSError) as exc:
        raise HealthIngestError(400, "invalid_timestamp") from exc
    if abs((now - client_time).total_seconds()) > MAX_SIGNATURE_AGE_SECONDS:
        raise HealthIngestError(401, "stale_signature")
    device = db.get(HealthConnectDevice, device_id)
    if device is None:
        raise HealthIngestError(401, "unknown_device")
    if device.status != "approved":
        raise HealthIngestError(403, "device_not_approved")
    _verify_signature(device, timestamp, nonce, request_id, signature, raw_body)
    return device, client_time, hashlib.sha256(raw_body).hexdigest()


def _nonce_exists(db: Session, device_id: str, nonce: str) -> bool:
    return any(
        value is not None
        for value in (
            db.scalar(
                select(HealthConnectBatch.id).where(
                    HealthConnectBatch.device_id == device_id,
                    HealthConnectBatch.nonce == nonce,
                )
            ),
            db.scalar(
                select(MiFitnessBatch.id).where(
                    MiFitnessBatch.device_id == device_id,
                    MiFitnessBatch.nonce == nonce,
                )
            ),
            db.scalar(
                select(MiFitnessStatusReport.id).where(
                    MiFitnessStatusReport.device_id == device_id,
                    MiFitnessStatusReport.nonce == nonce,
                )
            ),
        )
    )


def _status_response(
    db: Session,
    source: MiFitnessSource,
    now: datetime,
) -> MiFitnessStatusResponse:
    missing = _activation_missing(db, source, now) if source.enabled else []
    return MiFitnessStatusResponse(
        status=source.status,
        enabled=source.enabled,
        active=source.enabled and source.activated_at is not None,
        activation_missing_types=missing,
    )


def _coverage_spans(
    db: Session,
    source: MiFitnessSource,
    record_type: str,
    start: datetime,
    end: datetime,
) -> bool:
    if source.account_fingerprint is None:
        return False
    rows = list(
        db.scalars(
            select(MiFitnessCoverage)
            .where(
                MiFitnessCoverage.device_id == source.device_id,
                MiFitnessCoverage.account_fingerprint == source.account_fingerprint,
                MiFitnessCoverage.record_type == record_type,
                MiFitnessCoverage.range_end > start,
                MiFitnessCoverage.range_start < end,
            )
            .order_by(MiFitnessCoverage.range_start, MiFitnessCoverage.range_end)
        )
    )
    cursor = start
    for row in rows:
        row_start, row_end = _aware(row.range_start), _aware(row.range_end)
        if row_start > cursor:
            return False
        if row_end > cursor:
            cursor = row_end
        if cursor >= end:
            return True
    return False


def _heart_freshness_ready(db: Session, source: MiFitnessSource) -> bool:
    latest_hc = db.scalar(
        select(HealthConnectRecord.start_time)
        .join(HealthConnectDevice, HealthConnectRecord.device_id == HealthConnectDevice.id)
        .where(
            HealthConnectDevice.status == "approved",
            HealthConnectRecord.record_type == "heart_rate",
            HealthConnectRecord.is_deleted.is_(False),
        )
        .order_by(HealthConnectRecord.start_time.desc())
        .limit(1)
    )
    if latest_hc is None:
        return True
    latest_cloud = db.scalar(
        select(MiFitnessRecord.start_time)
        .join(
            MiFitnessCoverage,
            and_(
                MiFitnessCoverage.device_id == MiFitnessRecord.device_id,
                MiFitnessCoverage.snapshot_id == MiFitnessRecord.snapshot_id,
            ),
        )
        .where(
            MiFitnessRecord.device_id == source.device_id,
            MiFitnessRecord.account_fingerprint == source.account_fingerprint,
            MiFitnessRecord.record_type == "heart_rate",
            MiFitnessRecord.is_deleted.is_(False),
        )
        .order_by(MiFitnessRecord.start_time.desc())
        .limit(1)
    )
    return latest_cloud is not None and _aware(latest_cloud) > _aware(latest_hc)


def _activation_missing(
    db: Session,
    source: MiFitnessSource,
    now: datetime,
) -> list[str]:
    end = now - ACTIVATION_END_TOLERANCE
    # Phone workers begin each per-type window a few seconds apart. Use the same
    # bounded tolerance at both edges while still requiring essentially three days.
    start = now - ACTIVATION_WINDOW + ACTIVATION_END_TOLERANCE
    missing = [
        record_type
        for record_type in sorted(MI_FITNESS_RECORD_TYPES)
        if not _coverage_spans(db, source, record_type, start, end)
    ]
    if not missing and not _heart_freshness_ready(db, source):
        missing.append("heart_rate_freshness")
    return missing


def _maybe_activate(db: Session, source: MiFitnessSource, now: datetime) -> list[str]:
    missing = _activation_missing(db, source, now)
    if source.enabled and source.status == "success" and not missing:
        source.activated_at = source.activated_at or now
    return missing


def report_signed_status(
    db: Session,
    *,
    device_id: str,
    timestamp: str,
    nonce: str,
    report_id: str,
    signature: str,
    raw_body: bytes,
    now: datetime | None = None,
) -> MiFitnessStatusResponse:
    current = _iso_utc(now or datetime.now(timezone.utc))
    device, client_time, body_sha256 = _signed_device(
        db,
        device_id=device_id,
        timestamp=timestamp,
        nonce=nonce,
        request_id=report_id,
        signature=signature,
        raw_body=raw_body,
        now=current,
    )
    existing = db.scalar(
        select(MiFitnessStatusReport).where(
            MiFitnessStatusReport.device_id == device.id,
            MiFitnessStatusReport.report_id == report_id,
        )
    )
    if existing is not None:
        if existing.body_sha256 != body_sha256:
            raise HealthIngestError(409, "status_id_conflict")
        source = db.get(MiFitnessSource, device.id)
        if source is None:
            raise HealthIngestError(409, "status_replay_conflict")
        return _status_response(db, source, current)
    if _nonce_exists(db, device.id, nonce):
        raise HealthIngestError(409, "nonce_replay")
    try:
        payload = MiFitnessStatusInput.model_validate_json(raw_body)
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        raise HealthIngestError(422, "invalid_status_payload") from exc
    if payload.data_as_of is not None and payload.data_as_of > current + timedelta(days=1):
        raise HealthIngestError(422, "data_as_of_in_future")
    source = db.get(MiFitnessSource, device.id)
    if source is None:
        source = MiFitnessSource(device_id=device.id)
        db.add(source)
        db.flush()
    previous_status = source.status
    was_enabled = source.enabled
    if not payload.enabled:
        source.enabled = False
        source.status = "disabled"
        source.activated_at = None
        source.account_fingerprint = None
        source.region = None
        source.last_success_at = None
        source.data_as_of = None
        source.last_error_code = None
    else:
        if (
            source.enabled
            and source.account_fingerprint is not None
            and source.account_fingerprint != payload.account_fingerprint
        ):
            raise HealthIngestError(409, "mi_account_mismatch")
        if not was_enabled:
            source.activated_at = None
            source.last_success_at = None
            source.data_as_of = None
        source.enabled = True
        source.account_fingerprint = payload.account_fingerprint
        source.region = payload.region
        source.status = payload.status
        source.last_error_code = payload.error_code
        if payload.data_as_of is not None and (
            source.data_as_of is None or _aware(payload.data_as_of) > _aware(source.data_as_of)
        ):
            source.data_as_of = min(_iso_utc(payload.data_as_of), current)
        if payload.status == "success":
            source.last_success_at = current
        elif payload.status == "auth_required" and previous_status != "auth_required":
            source.auth_episode += 1
            db.add(
                Outbox(
                    event_key=f"mi-fitness-auth:{device.id}:{source.auth_episode}",
                    event_type="mi_fitness.auth_required",
                    payload={},
                    available_at=current,
                )
            )
    source.last_status_at = current
    _maybe_activate(db, source, current) if source.enabled else []
    db.add(
        MiFitnessStatusReport(
            device_id=device.id,
            report_id=report_id,
            nonce=nonce,
            client_timestamp=client_time,
            body_sha256=body_sha256,
            reported_at=current,
        )
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HealthIngestError(409, "status_replay_conflict") from exc
    return _status_response(db, source, current)


def _batch_response(
    db: Session,
    batch: MiFitnessBatch,
    source: MiFitnessSource,
    *,
    idempotent: bool,
) -> MiFitnessBatchAcceptedResponse:
    return MiFitnessBatchAcceptedResponse(
        batch_id=batch.batch_id,
        idempotent=idempotent,
        record_count=batch.record_count,
        changed_count=batch.changed_count,
        reconciled_count=batch.reconciled_count,
        coverage_published=batch.final_page,
        active=source.enabled and source.activated_at is not None,
        data_as_of=batch.data_as_of,
    )


def _snapshot_pages(
    db: Session, device_id: str, snapshot_id: str
) -> list[MiFitnessBatch]:
    return list(
        db.scalars(
            select(MiFitnessBatch)
            .where(
                MiFitnessBatch.device_id == device_id,
                MiFitnessBatch.snapshot_id == snapshot_id,
            )
            .order_by(MiFitnessBatch.page_index)
        )
    )


def _validate_sequence(
    db: Session,
    source: MiFitnessSource,
    payload: MiFitnessBatchInput,
) -> list[MiFitnessBatch]:
    pages = _snapshot_pages(db, source.device_id, payload.snapshot_id)
    if any(page.final_page for page in pages):
        raise HealthIngestError(409, "snapshot_already_finalised")
    if [page.page_index for page in pages] != list(range(payload.page_index)):
        raise HealthIngestError(409, "snapshot_page_out_of_order")
    seen: set[str] = set()
    for page in pages:
        if (
            page.record_type != payload.record_type
            or page.account_fingerprint != source.account_fingerprint
            or _aware(page.range_start) != _aware(payload.range_start)
            or _aware(page.range_end) != _aware(payload.range_end)
        ):
            raise HealthIngestError(409, "snapshot_metadata_conflict")
        seen.update(page.record_ids)
    if seen.intersection(record.record_id for record in payload.records):
        raise HealthIngestError(409, "snapshot_record_repeated")
    return pages


def _record_signature(row: MiFitnessRecord | None) -> tuple | None:
    if row is None:
        return None
    return (
        _aware(row.start_time),
        _aware(row.end_time),
        row.start_zone_offset_seconds,
        row.end_zone_offset_seconds,
        Decimal(row.primary_value) if row.primary_value is not None else None,
        row.primary_unit,
        row.subtype,
        row.metrics,
    )


def _currently_published_record(
    db: Session,
    source: MiFitnessSource,
    record_type: str,
    external_record_id: str,
    start: datetime,
    end: datetime,
) -> MiFitnessRecord | None:
    coverage = db.scalar(
        select(MiFitnessCoverage)
        .where(
            MiFitnessCoverage.device_id == source.device_id,
            MiFitnessCoverage.account_fingerprint == source.account_fingerprint,
            MiFitnessCoverage.record_type == record_type,
            MiFitnessCoverage.range_end > start,
            MiFitnessCoverage.range_start < end,
        )
        .order_by(MiFitnessCoverage.finalised_at.desc(), MiFitnessCoverage.id.desc())
        .limit(1)
    )
    if coverage is None:
        return None
    return db.scalar(
        select(MiFitnessRecord).where(
            MiFitnessRecord.device_id == source.device_id,
            MiFitnessRecord.snapshot_id == coverage.snapshot_id,
            MiFitnessRecord.record_type == record_type,
            MiFitnessRecord.external_record_id == external_record_id,
        )
    )


def ingest_signed_mi_fitness_batch(
    db: Session,
    *,
    device_id: str,
    timestamp: str,
    nonce: str,
    batch_id: str,
    signature: str,
    raw_body: bytes,
    now: datetime | None = None,
) -> MiFitnessBatchAcceptedResponse:
    current = _iso_utc(now or datetime.now(timezone.utc))
    device, client_time, body_sha256 = _signed_device(
        db,
        device_id=device_id,
        timestamp=timestamp,
        nonce=nonce,
        request_id=batch_id,
        signature=signature,
        raw_body=raw_body,
        now=current,
    )
    existing = db.scalar(
        select(MiFitnessBatch).where(
            MiFitnessBatch.device_id == device.id,
            MiFitnessBatch.batch_id == batch_id,
        )
    )
    source = db.get(MiFitnessSource, device.id)
    if existing is not None:
        if existing.body_sha256 != body_sha256:
            raise HealthIngestError(409, "batch_id_conflict")
        if source is None:
            raise HealthIngestError(409, "batch_replay_conflict")
        return _batch_response(db, existing, source, idempotent=True)
    if _nonce_exists(db, device.id, nonce):
        raise HealthIngestError(409, "nonce_replay")
    if source is None or not source.enabled or source.account_fingerprint is None:
        raise HealthIngestError(409, "mi_fitness_not_enabled")
    try:
        payload = MiFitnessBatchInput.model_validate_json(raw_body)
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        raise HealthIngestError(422, "invalid_batch_payload") from exc
    if payload.batch_id is not None and payload.batch_id != batch_id:
        raise HealthIngestError(409, "batch_id_header_mismatch")
    if payload.data_as_of > current + timedelta(days=1):
        raise HealthIngestError(422, "data_as_of_in_future")
    if payload.source_data_as_of is not None and payload.source_data_as_of > current + timedelta(days=1):
        raise HealthIngestError(422, "source_data_as_of_in_future")
    record_ids = [record.record_id for record in payload.records]
    if len(record_ids) != len(set(record_ids)):
        raise HealthIngestError(422, "duplicate_record_id")
    pages = _validate_sequence(db, source, payload)
    effective_data_as_of = min(_iso_utc(payload.data_as_of), current)
    batch = MiFitnessBatch(
        device_id=device.id,
        batch_id=batch_id,
        nonce=nonce,
        client_timestamp=client_time,
        body_sha256=body_sha256,
        account_fingerprint=source.account_fingerprint,
        schema_version=payload.schema_version,
        record_type=payload.record_type,
        data_as_of=effective_data_as_of,
        source_data_as_of=payload.source_data_as_of,
        snapshot_id=payload.snapshot_id,
        range_start=payload.range_start,
        range_end=payload.range_end,
        page_index=payload.page_index,
        final_page=payload.final_page,
        record_ids=record_ids,
        record_count=len(payload.records),
        accepted_at=current,
    )
    db.add(batch)
    db.flush()
    changed = 0
    for incoming in payload.records:
        assert incoming.start_time is not None
        record_end = incoming.end_time or incoming.start_time
        if incoming.start_time > current + timedelta(days=1) or record_end > current + timedelta(days=1):
            raise HealthIngestError(422, "record_time_in_future")
        primary, unit, subtype, metrics, start_offset, end_offset = _normalise_record(incoming)
        candidate_signature = (
            _iso_utc(incoming.start_time),
            _iso_utc(record_end),
            start_offset,
            end_offset,
            primary,
            unit,
            subtype,
            metrics,
        )
        published = _currently_published_record(
            db,
            source,
            payload.record_type,
            incoming.record_id,
            incoming.start_time,
            record_end,
        )
        if _record_signature(published) != candidate_signature:
            changed += 1
        db.add(
            MiFitnessRecord(
                device_id=device.id,
                external_record_id=incoming.record_id,
                snapshot_id=payload.snapshot_id,
                record_type=payload.record_type,
                account_fingerprint=source.account_fingerprint,
                start_time=incoming.start_time,
                end_time=record_end,
                start_zone_offset_seconds=start_offset,
                end_zone_offset_seconds=end_offset,
                primary_value=primary,
                primary_unit=unit,
                subtype=subtype,
                metrics=metrics,
                is_deleted=False,
                source_updated_at=incoming.updated_at
                or payload.source_data_as_of
                or effective_data_as_of,
                source_batch_id=batch.id,
            )
        )
    batch.changed_count = changed
    if payload.final_page:
        all_pages = [*pages, batch]
        if [page.page_index for page in all_pages] != list(range(payload.page_index + 1)):
            raise HealthIngestError(409, "snapshot_pages_incomplete")
        seen = {record_id for page in all_pages for record_id in page.record_ids}
        prior_ids = set(
            db.scalars(
                select(MiFitnessRecord.external_record_id)
                .join(
                    MiFitnessCoverage,
                    and_(
                        MiFitnessCoverage.device_id == MiFitnessRecord.device_id,
                        MiFitnessCoverage.snapshot_id == MiFitnessRecord.snapshot_id,
                    ),
                )
                .where(
                    MiFitnessRecord.device_id == device.id,
                    MiFitnessRecord.account_fingerprint == source.account_fingerprint,
                    MiFitnessRecord.record_type == payload.record_type,
                    MiFitnessRecord.start_time < payload.range_end,
                    MiFitnessRecord.end_time >= payload.range_start,
                )
            )
        )
        batch.changed_count = sum(page.changed_count for page in pages) + changed
        batch.reconciled_count = len(prior_ids - seen)
        if batch.reconciled_count:
            batch.changed_count += batch.reconciled_count
        db.add(
            MiFitnessCoverage(
                device_id=device.id,
                snapshot_id=payload.snapshot_id,
                record_type=payload.record_type,
                account_fingerprint=source.account_fingerprint,
                range_start=payload.range_start,
                range_end=payload.range_end,
                source_data_as_of=payload.source_data_as_of,
                confirmed_empty=not seen,
                finalised_at=current,
            )
        )
        if source.status == "success":
            _maybe_activate(db, source, current)
    source.last_status_at = current
    if payload.source_data_as_of is not None and (
        source.data_as_of is None or _aware(payload.source_data_as_of) > _aware(source.data_as_of)
    ):
        source.data_as_of = min(_iso_utc(payload.source_data_as_of), current)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raced = db.scalar(
            select(MiFitnessBatch).where(
                MiFitnessBatch.device_id == device.id,
                MiFitnessBatch.batch_id == batch_id,
            )
        )
        if raced is not None and raced.body_sha256 == body_sha256:
            return _batch_response(db, raced, source, idempotent=True)
        raise HealthIngestError(409, "batch_replay_conflict") from exc
    return _batch_response(db, batch, source, idempotent=False)
