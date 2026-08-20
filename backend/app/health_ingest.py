from __future__ import annotations

import base64
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import re
import secrets
import statistics
from typing import Any
from uuid import uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .health_models import HealthConnectBatch, HealthConnectDevice, HealthConnectRecord
from .health_schemas import (
    BatchAcceptedResponse,
    DeviceRegistrationRequest,
    DeviceRegistrationResponse,
    DeviceStatusResponse,
    HealthBatchInput,
    HealthRecordInput,
)


MAX_BODY_BYTES = 1_048_576
MAX_SIGNATURE_AGE_SECONDS = 300
PAIRING_TTL = timedelta(hours=24)
PAIRING_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
_HEADER_TOKEN = re.compile(r"^[A-Za-z0-9._:-]+$")


class HealthIngestError(RuntimeError):
    def __init__(self, status_code: int, code: str):
        super().__init__(code)
        self.status_code = status_code
        self.code = code


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _iso_utc(value: datetime) -> datetime:
    return _aware(value).astimezone(timezone.utc)


def _normalise_pairing_code(value: str) -> str:
    return "".join(character for character in value.upper() if character.isalnum())


def _pairing_hash(value: str) -> str:
    return hashlib.sha256(_normalise_pairing_code(value).encode("ascii")).hexdigest()


def _new_pairing_code(db: Session) -> tuple[str, str]:
    for _ in range(20):
        compact = "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(8))
        display = f"{compact[:4]}-{compact[4:]}"
        digest = _pairing_hash(display)
        if db.scalar(
            select(HealthConnectDevice.id).where(HealthConnectDevice.pairing_code_hash == digest)
        ) is None:
            return display, digest
    raise HealthIngestError(503, "pairing_code_unavailable")


def _normalise_public_key(value: str) -> tuple[str, str]:
    try:
        key = serialization.load_pem_public_key(value.encode("ascii"))
    except (ValueError, TypeError, UnicodeEncodeError) as exc:
        raise HealthIngestError(422, "invalid_public_key") from exc
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(
        key.curve, ec.SECP256R1  # gitleaks:allow -- public curve class, not a credential
    ):
        raise HealthIngestError(422, "public_key_must_be_p256")
    der = key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    pem = key.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    return pem, hashlib.sha256(der).hexdigest()


def register_device(
    db: Session,
    request: DeviceRegistrationRequest,
    now: datetime | None = None,
) -> DeviceRegistrationResponse:
    current = _iso_utc(now or datetime.now(timezone.utc))
    pem, fingerprint = _normalise_public_key(request.public_key_pem)
    existing = db.scalar(
        select(HealthConnectDevice).where(
            HealthConnectDevice.public_key_fingerprint == fingerprint
        )
    )
    if existing is not None and existing.status != "pending":
        return DeviceRegistrationResponse(device_id=existing.id, status=existing.status)

    code, code_hash = _new_pairing_code(db)
    if existing is None:
        existing = HealthConnectDevice(
            id=str(uuid4()),
            label=request.label,
            public_key_pem=pem,
            public_key_fingerprint=fingerprint,
            status="pending",
            created_at=current,
        )
        db.add(existing)
    else:
        # Registration retries deliberately rotate the one-time code. This
        # makes a lost registration response recoverable without storing the
        # plaintext code server-side.
        existing.label = request.label
        existing.public_key_pem = pem
    existing.pairing_code_hash = code_hash
    existing.pairing_expires_at = current + PAIRING_TTL
    existing.last_error = None
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HealthIngestError(409, "device_registration_conflict") from exc
    return DeviceRegistrationResponse(
        device_id=existing.id,
        status="pending",
        pairing_code=code,
        pairing_expires_at=existing.pairing_expires_at,
    )


def get_device_status(db: Session, device_id: str) -> DeviceStatusResponse:
    device = db.get(HealthConnectDevice, device_id)
    if device is None:
        raise HealthIngestError(404, "device_not_found")
    return DeviceStatusResponse(
        device_id=device.id,
        status=device.status,
        last_sync_at=device.last_sync_at,
        data_as_of=device.data_as_of,
        last_error=device.last_error,
    )


def approve_device(
    db: Session, pairing_code: str, now: datetime | None = None
) -> HealthConnectDevice:
    current = _iso_utc(now or datetime.now(timezone.utc))
    normalised = _normalise_pairing_code(pairing_code)
    if len(normalised) != 8 or any(character not in PAIRING_ALPHABET for character in normalised):
        raise HealthIngestError(404, "pairing_code_not_found")
    device = db.scalar(
        select(HealthConnectDevice).where(
            HealthConnectDevice.pairing_code_hash == _pairing_hash(normalised),
            HealthConnectDevice.status == "pending",
        )
    )
    if device is None:
        raise HealthIngestError(404, "pairing_code_not_found")
    if device.pairing_expires_at is None or _aware(device.pairing_expires_at) < current:
        raise HealthIngestError(410, "pairing_code_expired")
    device.status = "approved"
    device.approved_at = current
    device.pairing_code_hash = None
    device.pairing_expires_at = None
    device.last_error = None
    db.commit()
    return device


def pending_devices(db: Session, now: datetime | None = None) -> list[dict[str, Any]]:
    current = _iso_utc(now or datetime.now(timezone.utc))
    rows = db.scalars(
        select(HealthConnectDevice)
        .where(HealthConnectDevice.status == "pending")
        .order_by(HealthConnectDevice.created_at)
    )
    return [
        {
            "device_id": row.id,
            "label": row.label,
            "created_at": _aware(row.created_at).isoformat(),
            "pairing_expired": row.pairing_expires_at is None
            or _aware(row.pairing_expires_at) < current,
        }
        for row in rows
    ]


def _number(
    values: dict[str, Any],
    keys: tuple[str, ...],
    minimum: float,
    maximum: float,
    field: str,
) -> float:
    matches = [key for key in keys if key in values]
    if len(matches) != 1:
        raise HealthIngestError(422, f"invalid_{field}")
    value = values[matches[0]]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HealthIngestError(422, f"invalid_{field}")
    number = float(value)
    if not minimum <= number <= maximum:
        raise HealthIngestError(422, f"invalid_{field}")
    return number


def _zone_offsets(values: dict[str, Any]) -> tuple[int | None, int | None]:
    start = values.get("start_zone_offset_seconds", values.get("zone_offset_seconds"))
    end = values.get("end_zone_offset_seconds", values.get("zone_offset_seconds"))
    for value in (start, end):
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or int(value) != value
            or not -64_800 <= int(value) <= 64_800
        ):
            raise HealthIngestError(422, "invalid_zone_offset")
    return (
        int(start) if start is not None else None,
        int(end) if end is not None else None,
    )


def _normalise_record(
    record: HealthRecordInput,
) -> tuple[Decimal | None, str | None, str | None, dict[str, Any], int | None, int | None]:
    if record.deleted:
        return None, None, None, {}, None, None
    values = dict(record.values)
    start_offset, end_offset = _zone_offsets(values)
    for key in ("zone_offset_seconds", "start_zone_offset_seconds", "end_zone_offset_seconds"):
        values.pop(key, None)
    recording_method = values.pop("recording_method", None)
    if recording_method is not None and (
        isinstance(recording_method, bool)
        or not isinstance(recording_method, (int, float))
        or int(recording_method) != recording_method
        or not 0 <= int(recording_method) <= 20
    ):
        raise HealthIngestError(422, "invalid_recording_method")

    primary: float | None = None
    unit: str | None = None
    subtype: str | None = None
    metrics: dict[str, Any] = {}
    if record.type == "steps":
        if set(values) != {"count"}:
            raise HealthIngestError(422, "invalid_step_values")
        # Match Health Connect's StepsRecord upper bound. Mi Fitness may emit a single
        # provider record above a conventional daily total, and rejecting a value the
        # source API considers valid stalls the entire ordered initial backfill.
        primary = _number(values, ("count",), 0, 1_000_000, "step_count")
        if int(primary) != primary:
            raise HealthIngestError(422, "invalid_step_count")
        unit = "count"
        metrics = {"count": round(primary)}
    elif record.type == "distance":
        if set(values) != {"meters"}:
            raise HealthIngestError(422, "invalid_distance_values")
        primary = _number(values, ("meters",), 0, 1_000_000, "distance")
        unit = "m"
        metrics = {"meters": round(primary, 3)}
    elif record.type in ("active_calories", "total_calories"):
        if len(values) != 1 or not set(values) <= {"kilocalories", "kcal"}:
            raise HealthIngestError(422, "invalid_calorie_values")
        primary = _number(values, ("kilocalories", "kcal"), 0, 100_000, "calories")
        unit = "kcal"
        metrics = {"kilocalories": round(primary, 3)}
    elif record.type == "exercise":
        if not {"exercise_type"} <= set(values) or not set(values) <= {
            "exercise_type",
            "duration_seconds",
        }:
            raise HealthIngestError(422, "invalid_exercise_values")
        exercise_type = values["exercise_type"]
        if isinstance(exercise_type, bool):
            raise HealthIngestError(422, "invalid_exercise_type")
        if isinstance(exercise_type, (int, float)) and int(exercise_type) == exercise_type:
            if not 0 <= int(exercise_type) <= 100_000:
                raise HealthIngestError(422, "invalid_exercise_type")
            subtype = f"hc_{int(exercise_type)}"
        elif isinstance(exercise_type, str) and re.fullmatch(
            r"[a-z0-9_-]{1,64}", exercise_type
        ):
            subtype = exercise_type
        else:
            raise HealthIngestError(422, "invalid_exercise_type")
        assert record.start_time is not None
        duration = ((record.end_time or record.start_time) - record.start_time).total_seconds()
        if "duration_seconds" in values:
            declared = _number(
                values, ("duration_seconds",), 0, 604_800, "exercise_duration"
            )
            if abs(declared - duration) > 2:
                raise HealthIngestError(422, "exercise_duration_mismatch")
        primary = duration
        unit = "s"
        metrics = {"duration_seconds": round(duration, 3)}
    elif record.type == "sleep":
        allowed = {
            "stage",
            "stages",
            "duration_seconds",
            "awake_seconds",
            "light_seconds",
            "deep_seconds",
            "rem_seconds",
            "unknown_seconds",
        }
        if not set(values) <= allowed:
            raise HealthIngestError(422, "invalid_sleep_values")
        if "stage" in values:
            stage = values["stage"]
            if stage not in {"awake", "sleeping", "out_of_bed", "light", "deep", "rem", "unknown"}:
                raise HealthIngestError(422, "invalid_sleep_stage")
            subtype = str(stage)
        assert record.start_time is not None
        interval = ((record.end_time or record.start_time) - record.start_time).total_seconds()
        duration = (
            _number(values, ("duration_seconds",), 0, 172_800, "sleep_duration")
            if "duration_seconds" in values
            else interval
        )
        metrics["duration_seconds"] = round(duration, 3)
        if subtype is not None:
            metrics["stage"] = subtype
        for key in ("awake_seconds", "light_seconds", "deep_seconds", "rem_seconds", "unknown_seconds"):
            if key in values:
                metrics[key] = round(_number(values, (key,), 0, 172_800, key), 3)
        if "stages" in values:
            stages = values["stages"]
            if not isinstance(stages, list) or len(stages) > 500:
                raise HealthIngestError(422, "invalid_sleep_stages")
            totals: dict[str, float] = defaultdict(float)
            for stage in stages:
                if not isinstance(stage, dict) or set(stage) != {
                    "start_time",
                    "end_time",
                    "stage",
                }:
                    raise HealthIngestError(422, "invalid_sleep_stage")
                try:
                    stage_start = datetime.fromisoformat(str(stage["start_time"]).replace("Z", "+00:00"))
                    stage_end = datetime.fromisoformat(str(stage["end_time"]).replace("Z", "+00:00"))
                except ValueError as exc:
                    raise HealthIngestError(422, "invalid_sleep_stage_time") from exc
                if stage_start.tzinfo is None or stage_end.tzinfo is None:
                    raise HealthIngestError(422, "invalid_sleep_stage_time")
                stage_start = _iso_utc(stage_start)
                stage_end = _iso_utc(stage_end)
                session_start = _iso_utc(record.start_time)
                session_end = _iso_utc(record.end_time or record.start_time)
                if (
                    stage_end < stage_start
                    or stage_start < session_start - timedelta(seconds=1)
                    or stage_end > session_end + timedelta(seconds=1)
                ):
                    raise HealthIngestError(422, "invalid_sleep_stage_time")
                name = str(stage["stage"])
                aliases = {"awake_in_bed": "awake"}
                name = aliases.get(name, name)
                if name not in {
                    "awake",
                    "sleeping",
                    "out_of_bed",
                    "light",
                    "deep",
                    "rem",
                    "unknown",
                }:
                    raise HealthIngestError(422, "invalid_sleep_stage")
                totals[name] += (stage_end - stage_start).total_seconds()
            for name, seconds in totals.items():
                metrics[f"{name}_seconds"] = round(seconds, 3)
        primary = duration
        unit = "s"
    elif record.type == "heart_rate":
        if set(values) == {"samples"}:
            samples = values["samples"]
            if not isinstance(samples, list) or not samples or len(samples) > 5_000:
                raise HealthIngestError(422, "invalid_heart_rate_samples")
            sample_values: list[float] = []
            hourly_values: dict[datetime, list[float]] = defaultdict(list)
            assert record.start_time is not None
            record_start = _iso_utc(record.start_time)
            record_end = _iso_utc(record.end_time or record.start_time)
            for sample in samples:
                if not isinstance(sample, dict) or set(sample) != {"beats_per_minute", "time"}:
                    raise HealthIngestError(422, "invalid_heart_rate_sample")
                bpm = _number(sample, ("beats_per_minute",), 20, 300, "heart_rate")
                try:
                    sample_time = datetime.fromisoformat(str(sample["time"]).replace("Z", "+00:00"))
                except ValueError as exc:
                    raise HealthIngestError(422, "invalid_heart_rate_sample_time") from exc
                if sample_time.tzinfo is None:
                    raise HealthIngestError(422, "invalid_heart_rate_sample_time")
                sample_time = _iso_utc(sample_time)
                if not record_start - timedelta(seconds=1) <= sample_time <= record_end + timedelta(seconds=1):
                    raise HealthIngestError(422, "invalid_heart_rate_sample_time")
                sample_values.append(bpm)
                hour = sample_time.replace(minute=0, second=0, microsecond=0)
                hourly_values[hour].append(bpm)
            primary = statistics.fmean(sample_values)
            metrics = {
                "average_bpm": round(primary, 3),
                "minimum_bpm": round(min(sample_values), 3),
                "maximum_bpm": round(max(sample_values), 3),
                "sample_count": len(sample_values),
                "hourly": [
                    {
                        "at": hour.isoformat().replace("+00:00", "Z"),
                        "average_bpm": round(statistics.fmean(values), 3),
                        "minimum_bpm": round(min(values), 3),
                        "maximum_bpm": round(max(values), 3),
                        "sample_count": len(values),
                    }
                    for hour, values in sorted(hourly_values.items())
                ],
            }
        elif len(values) == 1 and set(values) <= {"bpm", "beats_per_minute"}:
            primary = _number(values, ("bpm", "beats_per_minute"), 20, 300, "heart_rate")
            metrics = {
                "average_bpm": round(primary, 3),
                "minimum_bpm": round(primary, 3),
                "maximum_bpm": round(primary, 3),
                "sample_count": 1,
                "hourly": [
                    {
                        "at": _iso_utc(record.start_time).replace(
                            minute=0, second=0, microsecond=0
                        ).isoformat().replace("+00:00", "Z"),
                        "average_bpm": round(primary, 3),
                        "minimum_bpm": round(primary, 3),
                        "maximum_bpm": round(primary, 3),
                        "sample_count": 1,
                    }
                ],
            }
        else:
            raise HealthIngestError(422, "invalid_heart_rate_values")
        unit = "bpm"
    elif record.type == "resting_heart_rate":
        if len(values) != 1 or not set(values) <= {"bpm", "beats_per_minute"}:
            raise HealthIngestError(422, "invalid_resting_heart_rate_values")
        primary = _number(values, ("bpm", "beats_per_minute"), 20, 300, "heart_rate")
        unit = "bpm"
        metrics = {"bpm": round(primary, 3)}
    elif record.type == "hrv_rmssd":
        if len(values) != 1 or not set(values) <= {"milliseconds", "ms", "rmssd_millis"}:
            raise HealthIngestError(422, "invalid_hrv_values")
        primary = _number(
            values, ("milliseconds", "ms", "rmssd_millis"), 0, 1_000, "hrv_rmssd"
        )
        unit = "ms"
        metrics = {"milliseconds": round(primary, 3)}
    elif record.type == "oxygen_saturation":
        if set(values) != {"percentage"}:
            raise HealthIngestError(422, "invalid_oxygen_saturation_values")
        primary = _number(values, ("percentage",), 0, 100, "oxygen_saturation")
        unit = "%"
        metrics = {"percentage": round(primary, 3)}
    elif record.type == "vo2_max":
        if not set(values) & {"milliliters_per_minute_kilogram", "ml_per_kg_min"} or not set(values) <= {
            "milliliters_per_minute_kilogram",
            "ml_per_kg_min",
            "measurement_method",
        }:
            raise HealthIngestError(422, "invalid_vo2_max_values")
        measurement_method = values.get("measurement_method")
        if measurement_method is not None and (
            isinstance(measurement_method, bool)
            or not isinstance(measurement_method, (int, float))
            or int(measurement_method) != measurement_method
            or not 0 <= int(measurement_method) <= 100
        ):
            raise HealthIngestError(422, "invalid_vo2_measurement_method")
        measure_values = {
            key: value for key, value in values.items() if key != "measurement_method"
        }
        primary = _number(
            measure_values,
            ("milliliters_per_minute_kilogram", "ml_per_kg_min"),
            0,
            100,
            "vo2_max",
        )
        unit = "ml/kg/min"
        metrics = {"milliliters_per_minute_kilogram": round(primary, 3)}
        if measurement_method is not None:
            subtype = f"method_{int(measurement_method)}"
    else:  # protected by the schema; retained as a fail-closed guard
        raise HealthIngestError(422, "record_type_not_allowed")

    return (
        Decimal(str(primary)) if primary is not None else None,
        unit,
        subtype,
        metrics,
        start_offset,
        end_offset,
    )


def _validate_header_token(value: str, name: str, maximum: int) -> str:
    if not value or len(value) > maximum or not _HEADER_TOKEN.fullmatch(value):
        raise HealthIngestError(400, f"invalid_{name}")
    return value


def _verify_signature(
    device: HealthConnectDevice,
    timestamp: str,
    nonce: str,
    batch_id: str,
    signature: str,
    raw_body: bytes,
) -> None:
    try:
        signature_bytes = base64.b64decode(signature, validate=True)
        public_key = serialization.load_pem_public_key(device.public_key_pem.encode("ascii"))
        assert isinstance(public_key, ec.EllipticCurvePublicKey)
        signed = f"{timestamp}\n{nonce}\n{batch_id}\n".encode("ascii") + raw_body
        public_key.verify(signature_bytes, signed, ec.ECDSA(hashes.SHA256()))
    except (ValueError, TypeError, UnicodeEncodeError, InvalidSignature, AssertionError) as exc:
        raise HealthIngestError(401, "invalid_signature") from exc


def _batch_response(batch: HealthConnectBatch, idempotent: bool) -> BatchAcceptedResponse:
    return BatchAcceptedResponse(
        batch_id=batch.batch_id,
        idempotent=idempotent,
        record_count=batch.record_count,
        upserted_count=batch.upserted_count,
        deleted_count=batch.deleted_count,
        reconciled_count=batch.reconciled_count,
        data_as_of=batch.data_as_of,
    )


def _reconcile_snapshot(
    db: Session,
    device: HealthConnectDevice,
    batch: HealthConnectBatch,
    payload: HealthBatchInput,
) -> int:
    assert payload.snapshot_id is not None
    assert payload.range_start is not None
    assert payload.range_end is not None
    assert payload.page_index is not None
    pages = list(
        db.scalars(
            select(HealthConnectBatch)
            .where(
                HealthConnectBatch.device_id == device.id,
                HealthConnectBatch.snapshot_id == payload.snapshot_id,
            )
            .order_by(HealthConnectBatch.page_index)
        )
    )
    indexes = [page.page_index for page in pages]
    if indexes != list(range(payload.page_index + 1)):
        raise HealthIngestError(409, "snapshot_pages_incomplete")
    for page in pages:
        if (
            page.mode != "snapshot"
            or page.record_type != payload.record_type
            or page.data_origin != payload.data_origin
            or _aware(page.range_start) != _aware(payload.range_start)
            or _aware(page.range_end) != _aware(payload.range_end)
        ):
            raise HealthIngestError(409, "snapshot_metadata_conflict")
    seen = {record_id for page in pages for record_id in page.record_ids}
    candidates = list(
        db.scalars(
            select(HealthConnectRecord).where(
                HealthConnectRecord.device_id == device.id,
                HealthConnectRecord.record_type == payload.record_type,
                HealthConnectRecord.data_origin == payload.data_origin,
                HealthConnectRecord.is_deleted.is_(False),
                HealthConnectRecord.start_time < payload.range_end,
                or_(
                    HealthConnectRecord.end_time >= payload.range_start,
                    HealthConnectRecord.end_time.is_(None),
                ),
            )
        )
    )
    reconciled = 0
    for row in candidates:
        if row.external_record_id in seen:
            continue
        row.is_deleted = True
        row.metrics = {}
        row.primary_value = None
        row.primary_unit = None
        row.subtype = None
        row.source_updated_at = payload.data_as_of
        row.source_batch_id = batch.id
        reconciled += 1
    return reconciled


def _validate_snapshot_sequence(
    db: Session,
    device: HealthConnectDevice,
    payload: HealthBatchInput,
) -> None:
    if payload.mode != "snapshot":
        return
    assert payload.snapshot_id is not None
    assert payload.page_index is not None
    assert payload.range_start is not None
    assert payload.range_end is not None
    pages = list(
        db.scalars(
            select(HealthConnectBatch)
            .where(
                HealthConnectBatch.device_id == device.id,
                HealthConnectBatch.snapshot_id == payload.snapshot_id,
            )
            .order_by(HealthConnectBatch.page_index)
        )
    )
    if any(page.final_page for page in pages):
        raise HealthIngestError(409, "snapshot_already_finalised")
    if [page.page_index for page in pages] != list(range(payload.page_index)):
        raise HealthIngestError(409, "snapshot_page_out_of_order")
    for page in pages:
        if (
            page.mode != "snapshot"
            or page.record_type != payload.record_type
            or page.data_origin != payload.data_origin
            or page.range_start is None
            or page.range_end is None
            or _aware(page.range_start) != _aware(payload.range_start)
            or _aware(page.range_end) != _aware(payload.range_end)
        ):
            raise HealthIngestError(409, "snapshot_metadata_conflict")


def ingest_signed_batch(
    db: Session,
    *,
    device_id: str,
    timestamp: str,
    nonce: str,
    batch_id: str,
    signature: str,
    raw_body: bytes,
    now: datetime | None = None,
) -> BatchAcceptedResponse:
    current = _iso_utc(now or datetime.now(timezone.utc))
    if len(raw_body) > MAX_BODY_BYTES:
        raise HealthIngestError(413, "batch_too_large")
    if not raw_body:
        raise HealthIngestError(400, "empty_batch")
    _validate_header_token(device_id, "device_id", 36)
    _validate_header_token(nonce, "nonce", 128)
    _validate_header_token(batch_id, "batch_id", 128)
    if len(signature) > 512:
        raise HealthIngestError(400, "invalid_signature_header")
    try:
        timestamp_seconds = int(timestamp)
        client_time = datetime.fromtimestamp(timestamp_seconds, timezone.utc)
    except (ValueError, OverflowError, OSError) as exc:
        raise HealthIngestError(400, "invalid_timestamp") from exc
    if abs((current - client_time).total_seconds()) > MAX_SIGNATURE_AGE_SECONDS:
        raise HealthIngestError(401, "stale_signature")

    device = db.get(HealthConnectDevice, device_id)
    if device is None:
        raise HealthIngestError(401, "unknown_device")
    if device.status != "approved":
        raise HealthIngestError(403, "device_not_approved")
    _verify_signature(device, timestamp, nonce, batch_id, signature, raw_body)
    body_sha256 = hashlib.sha256(raw_body).hexdigest()

    existing = db.scalar(
        select(HealthConnectBatch).where(
            HealthConnectBatch.device_id == device.id,
            HealthConnectBatch.batch_id == batch_id,
        )
    )
    if existing is not None:
        if existing.body_sha256 != body_sha256:
            raise HealthIngestError(409, "batch_id_conflict")
        return _batch_response(existing, True)
    if db.scalar(
        select(HealthConnectBatch.id).where(
            HealthConnectBatch.device_id == device.id,
            HealthConnectBatch.nonce == nonce,
        )
    ) is not None:
        raise HealthIngestError(409, "nonce_replay")

    try:
        payload = HealthBatchInput.model_validate_json(raw_body)
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        raise HealthIngestError(422, "invalid_batch_payload") from exc
    if payload.batch_id is not None and payload.batch_id != batch_id:
        raise HealthIngestError(409, "batch_id_header_mismatch")
    if payload.data_as_of > current + timedelta(minutes=5):
        raise HealthIngestError(422, "data_as_of_in_future")
    record_ids = [record.record_id for record in payload.records]
    if len(record_ids) != len(set(record_ids)):
        raise HealthIngestError(422, "duplicate_record_id")
    if device.data_origin is None:
        device.data_origin = payload.data_origin
    elif device.data_origin != payload.data_origin:
        raise HealthIngestError(403, "data_origin_mismatch")
    _validate_snapshot_sequence(db, device, payload)

    batch = HealthConnectBatch(
        device_id=device.id,
        batch_id=batch_id,
        nonce=nonce,
        client_timestamp=client_time,
        body_sha256=body_sha256,
        schema_version=payload.schema_version,
        mode=payload.mode,
        record_type=payload.record_type,
        data_origin=payload.data_origin,
        data_as_of=payload.data_as_of,
        snapshot_id=payload.snapshot_id,
        range_start=payload.range_start,
        range_end=payload.range_end,
        page_index=payload.page_index,
        final_page=payload.final_page,
        record_ids=[record.record_id for record in payload.records if not record.deleted],
        record_count=len(payload.records),
        accepted_at=current,
    )
    db.add(batch)
    db.flush()
    upserted = deleted = 0
    for incoming in payload.records:
        # Health Connect DeletionChange intentionally contains no provider
        # timestamp. Use signed server acceptance time for its ordering; using
        # an envelope watermark (which may be EPOCH for a deletion-only page)
        # would incorrectly treat a real deletion as older than the record.
        effective_updated_at = incoming.updated_at or (
            current if incoming.deleted else payload.data_as_of
        )
        if _aware(effective_updated_at) > current + timedelta(days=1):
            raise HealthIngestError(422, "record_update_time_in_future")
        if incoming.start_time is not None and incoming.start_time > current + timedelta(days=1):
            raise HealthIngestError(422, "record_time_in_future")
        if incoming.end_time is not None and incoming.end_time > current + timedelta(days=1):
            raise HealthIngestError(422, "record_time_in_future")
        stored = db.scalar(
            select(HealthConnectRecord).where(
                HealthConnectRecord.device_id == device.id,
                HealthConnectRecord.external_record_id == incoming.record_id,
            )
        )
        if stored is not None and stored.source_updated_at is not None and (
            _aware(stored.source_updated_at) > _aware(effective_updated_at)
        ):
            continue
        if stored is not None and stored.record_type != incoming.type:
            raise HealthIngestError(409, "record_type_conflict")
        primary, unit, subtype, metrics, start_offset, end_offset = _normalise_record(incoming)
        if stored is None:
            stored = HealthConnectRecord(
                device_id=device.id,
                external_record_id=incoming.record_id,
                record_type=incoming.type,
                data_origin=incoming.data_origin,
            )
            db.add(stored)
        stored.data_origin = incoming.data_origin
        if incoming.start_time is not None:
            stored.start_time = incoming.start_time
        if incoming.end_time is not None:
            stored.end_time = incoming.end_time
        elif not incoming.deleted and incoming.start_time is not None:
            stored.end_time = incoming.start_time
        stored.start_zone_offset_seconds = start_offset
        stored.end_zone_offset_seconds = end_offset
        stored.primary_value = primary
        stored.primary_unit = unit
        stored.subtype = subtype
        stored.metrics = metrics
        stored.is_deleted = incoming.deleted
        stored.source_updated_at = effective_updated_at
        stored.source_batch_id = batch.id
        if incoming.deleted:
            deleted += 1
        else:
            upserted += 1

    batch.upserted_count = upserted
    batch.deleted_count = deleted
    if payload.mode == "snapshot" and payload.final_page:
        db.flush()
        batch.reconciled_count = _reconcile_snapshot(db, device, batch, payload)
    device.last_sync_at = current
    if device.data_as_of is None or _aware(payload.data_as_of) > _aware(device.data_as_of):
        device.data_as_of = payload.data_as_of
    device.last_error = None
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raced = db.scalar(
            select(HealthConnectBatch).where(
                HealthConnectBatch.device_id == device.id,
                HealthConnectBatch.batch_id == batch_id,
            )
        )
        if raced is not None and raced.body_sha256 == body_sha256:
            return _batch_response(raced, True)
        raise HealthIngestError(409, "batch_replay_conflict") from exc
    return _batch_response(batch, False)
