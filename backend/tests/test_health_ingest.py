from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import select

from app.health_ingest import (
    HealthIngestError,
    approve_device,
    get_device_status,
    ingest_signed_batch,
    register_device,
)
from app.health_models import HealthConnectBatch, HealthConnectRecord
from app.health_schemas import DeviceRegistrationRequest


def key_material():
    private = ec.generate_private_key(ec.SECP256R1())
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    return private, public_pem


def register_and_approve(db, now):
    private, public_pem = key_material()
    registration = register_device(
        db,
        DeviceRegistrationRequest(label="Smart Band phone", public_key_pem=public_pem),
        now,
    )
    assert registration.status == "pending"
    assert registration.pairing_code
    assert get_device_status(db, registration.device_id).status == "pending"
    approved = approve_device(db, registration.pairing_code, now + timedelta(minutes=1))
    assert approved.status == "approved"
    assert get_device_status(db, registration.device_id).status == "approved"
    return private, registration.device_id


def send(db, private, device_id, payload, now, *, batch_id=None, nonce=None):
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    timestamp = str(int(now.timestamp()))
    batch_id = batch_id or str(uuid4())
    nonce = nonce or uuid4().hex
    signed = f"{timestamp}\n{nonce}\n{batch_id}\n".encode("ascii") + raw
    signature = base64.b64encode(private.sign(signed, ec.ECDSA(hashes.SHA256()))).decode()
    return ingest_signed_batch(
        db,
        device_id=device_id,
        timestamp=timestamp,
        nonce=nonce,
        batch_id=batch_id,
        signature=signature,
        raw_body=raw,
        now=now,
    )


def changes_payload(now, records, record_type="steps", origin="com.mi.health"):
    return {
        "schema_version": 1,
        "mode": "changes",
        "record_type": record_type,
        "data_origin": origin,
        "data_as_of": now.isoformat(),
        "records": records,
    }


def step(record_id, now, count):
    return {
        "record_id": record_id,
        "type": "steps",
        "data_origin": "com.mi.health",
        "start_time": (now - timedelta(hours=1)).isoformat(),
        "end_time": now.isoformat(),
        "values": {"count": count, "zone_offset_seconds": 10800},
    }


def test_registration_approval_and_invalid_curve(db):
    now = datetime(2026, 8, 19, 8, tzinfo=timezone.utc)
    _, device_id = register_and_approve(db, now)
    assert get_device_status(db, device_id).status == "approved"

    other_key = ec.generate_private_key(ec.SECP384R1()).public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    with pytest.raises(HealthIngestError, match="public_key_must_be_p256"):
        register_device(
            db,
            DeviceRegistrationRequest(label="wrong curve", public_key_pem=other_key),
            now,
        )


def test_signed_batch_is_normalised_idempotent_and_replay_safe(db):
    now = datetime(2026, 8, 19, 8, tzinfo=timezone.utc)
    private, device_id = register_and_approve(db, now - timedelta(minutes=2))
    payload = changes_payload(now, [step("steps-1", now, 4321)])
    batch_id, nonce = "batch-1", "nonce-1"
    accepted = send(db, private, device_id, payload, now, batch_id=batch_id, nonce=nonce)
    assert accepted.upserted_count == 1
    assert accepted.idempotent is False
    row = db.scalar(select(HealthConnectRecord))
    assert row is not None
    assert float(row.primary_value) == 4321
    assert row.primary_unit == "count"
    assert row.metrics == {"count": 4321}
    assert row.start_zone_offset_seconds == 10800

    retried = send(
        db,
        private,
        device_id,
        payload,
        now,
        batch_id=batch_id,
        nonce="new-nonce-for-idempotent-retry",
    )
    assert retried.idempotent is True
    assert db.query(HealthConnectBatch).count() == 1

    second = changes_payload(now, [step("steps-2", now, 100)])
    with pytest.raises(HealthIngestError, match="nonce_replay"):
        send(db, private, device_id, second, now, batch_id="batch-2", nonce=nonce)


def test_deletion_origin_lock_and_allowlist(db):
    now = datetime(2026, 8, 19, 8, tzinfo=timezone.utc)
    private, device_id = register_and_approve(db, now - timedelta(minutes=2))
    send(db, private, device_id, changes_payload(now, [step("steps-1", now, 100)]), now)

    deletion = {
        "record_id": "steps-1",
        "type": "steps",
        "data_origin": "com.mi.health",
        "deleted": True,
    }
    deletion_watermark = datetime(1970, 1, 1, tzinfo=timezone.utc)
    result = send(
        db,
        private,
        device_id,
        changes_payload(deletion_watermark, [deletion]),
        now + timedelta(seconds=1),
    )
    assert result.deleted_count == 1
    assert db.scalar(select(HealthConnectRecord)).is_deleted is True

    wrong_origin = changes_payload(
        now + timedelta(seconds=2),
        [],
        origin="com.untrusted.fitness",
    )
    with pytest.raises(HealthIngestError, match="data_origin_mismatch"):
        send(db, private, device_id, wrong_origin, now + timedelta(seconds=2))
    db.rollback()

    forbidden = changes_payload(now, [], record_type="steps")
    forbidden["record_type"] = "weight"
    with pytest.raises(HealthIngestError, match="invalid_batch_payload"):
        send(db, private, device_id, forbidden, now + timedelta(seconds=3))


def test_final_snapshot_page_reconciles_absent_records(db):
    now = datetime(2026, 8, 19, 8, tzinfo=timezone.utc)
    private, device_id = register_and_approve(db, now - timedelta(minutes=2))
    send(
        db,
        private,
        device_id,
        changes_payload(now, [step("keep", now, 100), step("remove", now, 200)]),
        now,
    )
    snapshot = {
        "schema_version": 1,
        "mode": "snapshot",
        "record_type": "steps",
        "data_origin": "com.mi.health",
        "data_as_of": (now + timedelta(seconds=1)).isoformat(),
        "range_start": (now - timedelta(days=1)).isoformat(),
        "range_end": (now + timedelta(hours=1)).isoformat(),
        "snapshot_id": "steps-window-1",
        "page_index": 0,
        "final_page": True,
        "records": [step("keep", now, 101)],
    }
    accepted = send(db, private, device_id, snapshot, now + timedelta(seconds=1))
    assert accepted.reconciled_count == 1
    rows = {
        row.external_record_id: row
        for row in db.scalars(select(HealthConnectRecord).order_by(HealthConnectRecord.id))
    }
    assert rows["keep"].is_deleted is False
    assert float(rows["keep"].primary_value) == 101
    assert rows["remove"].is_deleted is True


def test_tampered_body_and_oversized_batch_are_rejected(db):
    now = datetime(2026, 8, 19, 8, tzinfo=timezone.utc)
    private, device_id = register_and_approve(db, now - timedelta(minutes=2))
    payload = changes_payload(now, [step("steps-1", now, 1)])
    raw = json.dumps(payload, separators=(",", ":")).encode()
    timestamp, nonce, batch_id = str(int(now.timestamp())), "nonce", "batch"
    signed = f"{timestamp}\n{nonce}\n{batch_id}\n".encode() + raw
    signature = base64.b64encode(private.sign(signed, ec.ECDSA(hashes.SHA256()))).decode()
    with pytest.raises(HealthIngestError, match="invalid_signature"):
        ingest_signed_batch(
            db,
            device_id=device_id,
            timestamp=timestamp,
            nonce=nonce,
            batch_id=batch_id,
            signature=signature,
            raw_body=raw + b" ",
            now=now,
        )
    with pytest.raises(HealthIngestError, match="batch_too_large"):
        ingest_signed_batch(
            db,
            device_id=device_id,
            timestamp=timestamp,
            nonce=nonce,
            batch_id=batch_id,
            signature=signature,
            raw_body=b"x" * 1_048_577,
            now=now,
        )


def test_android_wire_aliases_and_sampled_records_are_normalised(db):
    now = datetime(2026, 8, 19, 8, tzinfo=timezone.utc)
    private, device_id = register_and_approve(db, now - timedelta(minutes=2))
    batch_id = "android-heart-batch"
    heart = {
        "record_id": "heart-1",
        "type": "heart_rate",
        "data_origin": "com.mi.health",
        "start_time": (now - timedelta(minutes=2)).isoformat(),
        "end_time": now.isoformat(),
        "last_modified_time": now.isoformat(),
        "values": {
            "recording_method": 1,
            "start_zone_offset_seconds": 10800,
            "end_zone_offset_seconds": 10800,
            "samples": [
                {
                    "time": (now - timedelta(minutes=1)).isoformat(),
                    "beats_per_minute": 60,
                },
                {"time": now.isoformat(), "beats_per_minute": 80},
            ],
        },
    }
    payload = changes_payload(now, [heart], record_type="heart_rate")
    payload["batch_id"] = batch_id
    accepted = send(db, private, device_id, payload, now, batch_id=batch_id)
    assert accepted.upserted_count == 1
    row = db.scalar(select(HealthConnectRecord))
    assert float(row.primary_value) == 70
    assert row.metrics == {
        "average_bpm": 70.0,
        "minimum_bpm": 60.0,
        "maximum_bpm": 80.0,
        "sample_count": 2,
    }

    sleep_batch_id = "android-sleep-batch"
    sleep_start = now - timedelta(hours=8)
    sleep = {
        "record_id": "sleep-1",
        "type": "sleep",
        "data_origin": "com.mi.health",
        "start_time": sleep_start.isoformat(),
        "end_time": now.isoformat(),
        "last_modified_time": now.isoformat(),
        "values": {
            "recording_method": 1,
            "duration_seconds": 8 * 3600,
            "stages": [
                {
                    "start_time": sleep_start.isoformat(),
                    "end_time": (sleep_start + timedelta(hours=1)).isoformat(),
                    "stage": "awake_in_bed",
                },
                {
                    "start_time": (sleep_start + timedelta(hours=1)).isoformat(),
                    "end_time": (sleep_start + timedelta(hours=7)).isoformat(),
                    "stage": "sleeping",
                },
                {
                    "start_time": (sleep_start + timedelta(hours=7)).isoformat(),
                    "end_time": now.isoformat(),
                    "stage": "out_of_bed",
                },
            ],
        },
    }
    sleep_payload = changes_payload(now, [sleep], record_type="sleep")
    sleep_payload["batch_id"] = sleep_batch_id
    send(
        db,
        private,
        device_id,
        sleep_payload,
        now + timedelta(seconds=1),
        batch_id=sleep_batch_id,
    )
    sleep_row = db.scalar(
        select(HealthConnectRecord).where(HealthConnectRecord.record_type == "sleep")
    )
    assert sleep_row.metrics["awake_seconds"] == 3600
    assert sleep_row.metrics["sleeping_seconds"] == 21600
    assert sleep_row.metrics["out_of_bed_seconds"] == 3600
