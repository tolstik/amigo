from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import select

from app.health_analytics import _records
from app.health_ingest import HealthIngestError, approve_device, register_device
from app.health_models import HealthConnectDevice, HealthConnectRecord
from app.health_schemas import DeviceRegistrationRequest, MI_FITNESS_RECORD_TYPES
from app.mi_fitness_ingest import ingest_signed_mi_fitness_batch, report_signed_status
from app.mi_fitness_models import MiFitnessBatch, MiFitnessCoverage, MiFitnessRecord, MiFitnessSource
from app.models import Outbox


def paired(db, now):
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    registration = register_device(
        db,
        DeviceRegistrationRequest(label="Xiaomi cloud phone", public_key_pem=public),
        now - timedelta(minutes=2),
    )
    approve_device(db, registration.pairing_code, now - timedelta(minutes=1))
    return private, registration.device_id


def signed_call(function, db, private, device_id, payload, now, *, request_id=None, nonce=None):
    raw = json.dumps(payload, separators=(",", ":")).encode()
    timestamp = str(int(now.timestamp()))
    request_id = request_id or str(uuid4())
    nonce = nonce or uuid4().hex
    signature = base64.b64encode(
        private.sign(
            f"{timestamp}\n{nonce}\n{request_id}\n".encode() + raw,
            ec.ECDSA(hashes.SHA256()),
        )
    ).decode()
    request_key = "report_id" if function is report_signed_status else "batch_id"
    return function(
        db,
        device_id=device_id,
        timestamp=timestamp,
        nonce=nonce,
        signature=signature,
        raw_body=raw,
        now=now,
        **{request_key: request_id},
    )


def status_payload(status, *, enabled=True, fingerprint="a" * 64, region="ru", error=None):
    payload = {
        "schema_version": 1,
        "enabled": enabled,
        "status": status,
    }
    if enabled:
        payload.update(account_fingerprint=fingerprint, region=region)
    if error:
        payload["error_code"] = error
    return payload


def batch_payload(record_type, start, end, snapshot_id, records, *, page=0, final=True):
    return {
        "schema_version": 1,
        "record_type": record_type,
        "data_as_of": end.isoformat(),
        "source_data_as_of": (end - timedelta(minutes=1)).isoformat(),
        "range_start": start.isoformat(),
        "range_end": end.isoformat(),
        "snapshot_id": snapshot_id,
        "page_index": page,
        "final_page": final,
        "records": records,
    }


def step_record(record_id, at, count=123):
    return {
        "record_id": record_id,
        "type": "steps",
        "data_origin": "xiaomi_cloud",
        "start_time": at.isoformat(),
        "end_time": (at + timedelta(minutes=1)).isoformat(),
        "values": {"count": count, "zone_offset_seconds": 10800},
    }


def test_status_is_signed_replay_safe_and_auth_alert_is_deduplicated(db):
    now = datetime(2026, 8, 24, 8, tzinfo=timezone.utc)
    private, device_id = paired(db, now)
    pending = signed_call(
        report_signed_status,
        db,
        private,
        device_id,
        status_payload("pending"),
        now,
        request_id="status-pending",
        nonce="status-nonce-1",
    )
    assert pending.status == "pending"

    first = signed_call(
        report_signed_status,
        db,
        private,
        device_id,
        status_payload("auth_required", error="auth_required"),
        now + timedelta(seconds=1),
        request_id="status-auth-1",
        nonce="status-nonce-2",
    )
    assert first.status == "auth_required"
    signed_call(
        report_signed_status,
        db,
        private,
        device_id,
        status_payload("auth_required", error="auth_required"),
        now + timedelta(seconds=2),
        request_id="status-auth-2",
        nonce="status-nonce-3",
    )
    assert [row.event_type for row in db.scalars(select(Outbox))] == [
        "mi_fitness.auth_required"
    ]

    replay = signed_call(
        report_signed_status,
        db,
        private,
        device_id,
        status_payload("pending"),
        now + timedelta(seconds=3),
        request_id="status-pending",
        nonce="different-nonce",
    )
    assert replay.status == "auth_required"
    with pytest.raises(HealthIngestError, match="nonce_replay"):
        signed_call(
            report_signed_status,
            db,
            private,
            device_id,
            status_payload("pending"),
            now + timedelta(seconds=4),
            request_id="status-other",
            nonce="status-nonce-2",
        )


def test_disable_clears_active_source_freshness_before_another_account(db):
    now = datetime(2026, 8, 24, 8, tzinfo=timezone.utc)
    private, device_id = paired(db, now)
    signed_call(
        report_signed_status,
        db,
        private,
        device_id,
        {
            **status_payload("success"),
            "data_as_of": now.isoformat(),
        },
        now,
        request_id="status-first-account",
    )
    source = db.get(MiFitnessSource, device_id)
    source.activated_at = now
    db.commit()

    signed_call(
        report_signed_status,
        db,
        private,
        device_id,
        status_payload("disabled", enabled=False),
        now + timedelta(seconds=1),
        request_id="status-disabled",
    )
    db.refresh(source)
    assert source.enabled is False
    assert source.account_fingerprint is None
    assert source.activated_at is None
    assert source.last_success_at is None
    assert source.data_as_of is None

    signed_call(
        report_signed_status,
        db,
        private,
        device_id,
        status_payload("pending", fingerprint="b" * 64),
        now + timedelta(seconds=2),
        request_id="status-second-account",
    )
    db.refresh(source)
    assert source.account_fingerprint == "b" * 64
    assert source.activated_at is None
    assert source.data_as_of is None


def test_partial_snapshot_is_invisible_then_cloud_coverage_wins(db):
    now = datetime(2026, 8, 24, 8, tzinfo=timezone.utc)
    start, end = now - timedelta(days=1), now
    private, device_id = paired(db, now)
    signed_call(
        report_signed_status,
        db,
        private,
        device_id,
        status_payload("pending"),
        now,
    )
    device = db.get(HealthConnectDevice, device_id)
    db.add(
        HealthConnectRecord(
            device_id=device_id,
            external_record_id="hc-step",
            record_type="steps",
            data_origin="com.mi.health",
            start_time=now - timedelta(hours=2),
            end_time=now - timedelta(hours=2) + timedelta(minutes=1),
            primary_value=999,
            primary_unit="count",
            metrics={"count": 999},
            is_deleted=False,
        )
    )
    source = db.get(MiFitnessSource, device_id)
    source.activated_at = now
    db.commit()

    first = batch_payload(
        "steps", start, end, "steps-two-pages", [step_record("cloud-step", now - timedelta(hours=2), 321)],
        page=0, final=False,
    )
    result = signed_call(ingest_signed_mi_fitness_batch, db, private, device_id, first, now)
    assert result.coverage_published is False
    assert [row.external_record_id for row in _records(db, frozenset({"steps"}), timezone.utc)] == [
        "hc-step"
    ]

    final = batch_payload("steps", start, end, "steps-two-pages", [], page=1, final=True)
    result = signed_call(
        ingest_signed_mi_fitness_batch,
        db,
        private,
        device_id,
        final,
        now + timedelta(seconds=1),
    )
    assert result.coverage_published is True
    assert [row.external_record_id for row in _records(db, frozenset({"steps"}), timezone.utc)] == [
        "cloud-step"
    ]


def test_identical_snapshot_is_structural_noop_and_empty_snapshot_suppresses(db):
    now = datetime(2026, 8, 24, 8, tzinfo=timezone.utc)
    start, end = now - timedelta(days=1), now
    private, device_id = paired(db, now)
    signed_call(
        report_signed_status,
        db,
        private,
        device_id,
        status_payload("pending"),
        now,
    )
    record = step_record("same", now - timedelta(hours=1), 456)
    first = signed_call(
        ingest_signed_mi_fitness_batch,
        db,
        private,
        device_id,
        batch_payload("steps", start, end, "snapshot-one", [record]),
        now,
    )
    assert first.changed_count == 1
    second = signed_call(
        ingest_signed_mi_fitness_batch,
        db,
        private,
        device_id,
        batch_payload("steps", start, end, "snapshot-two", [record]),
        now + timedelta(seconds=1),
    )
    assert second.changed_count == 0

    source = db.get(MiFitnessSource, device_id)
    source.activated_at = now
    db.commit()
    empty = signed_call(
        ingest_signed_mi_fitness_batch,
        db,
        private,
        device_id,
        batch_payload("steps", start, end, "snapshot-empty", []),
        now + timedelta(seconds=2),
    )
    assert empty.reconciled_count == 1
    assert _records(db, frozenset({"steps"}), timezone.utc) == []


def test_recent_all_type_coverage_activates_only_with_fresher_cloud_heart_rate(db):
    now = datetime(2026, 8, 24, 8, tzinfo=timezone.utc)
    start, end = now - timedelta(days=3), now
    private, device_id = paired(db, now)
    signed_call(
        report_signed_status,
        db,
        private,
        device_id,
        status_payload("pending"),
        now,
    )
    db.add(
        HealthConnectRecord(
            device_id=device_id,
            external_record_id="hc-heart",
            record_type="heart_rate",
            data_origin="com.mi.health",
            start_time=now - timedelta(hours=2),
            end_time=now - timedelta(hours=2),
            primary_value=60,
            primary_unit="bpm",
            metrics={"average_bpm": 60, "minimum_bpm": 60, "maximum_bpm": 60, "sample_count": 1},
            is_deleted=False,
        )
    )
    db.commit()
    for index, record_type in enumerate(sorted(MI_FITNESS_RECORD_TYPES)):
        records = []
        if record_type == "heart_rate":
            records = [{
                "record_id": "cloud-heart-new",
                "type": "heart_rate",
                "data_origin": "xiaomi_cloud",
                "start_time": (now - timedelta(hours=1)).isoformat(),
                "end_time": now.isoformat(),
                "values": {
                    "average_bpm": 65,
                    "minimum_bpm": 50,
                    "maximum_bpm": 90,
                    "sample_count": 12,
                },
            }]
        signed_call(
            ingest_signed_mi_fitness_batch,
            db,
            private,
            device_id,
            batch_payload(record_type, start, end, f"activation-{record_type}", records),
            now + timedelta(seconds=index),
        )
    success = signed_call(
        report_signed_status,
        db,
        private,
        device_id,
        status_payload("success"),
        now + timedelta(seconds=20),
    )
    assert success.active is True
    assert success.activation_missing_types == []
    assert db.query(MiFitnessCoverage).count() == len(MI_FITNESS_RECORD_TYPES)
    assert db.query(MiFitnessBatch).count() == len(MI_FITNESS_RECORD_TYPES)
    assert db.query(MiFitnessRecord).count() == 1
