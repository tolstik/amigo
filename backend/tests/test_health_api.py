from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.health_api as health_api
from app.db import get_db
from app.health_api import ingest_router, public_router
from app.health_ingest import HealthIngestError


def test_health_routers_are_mountable_and_public_payload_is_aggregate_only(db):
    app = FastAPI()
    app.include_router(public_router)
    app.include_router(ingest_router)

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        activity = client.get("/api/v1/series/activity?range=30d")
        assert activity.status_code == 200
        assert {
            "daily",
            "weekly",
            "available_metrics",
            "correlations",
            "data_as_of",
            "meta",
        } <= activity.json().keys()
        assert "records" not in activity.json()
        assert "data_origin" not in activity.text
        recovery = client.get("/api/v1/series/recovery?range=30d")
        assert recovery.status_code == 200
        assert recovery.json()["daily"] == []

        missing_headers = client.post("/amigo-ingest/v1/health-connect/batches", content=b"{}")
        assert missing_headers.status_code == 400
        assert missing_headers.json()["detail"]["code"] == "missing_signature_header"


def test_health_ingest_rejection_logs_only_detail_code(db, monkeypatch, caplog):
    app = FastAPI()
    app.include_router(ingest_router)

    def override_db():
        yield db

    def reject_batch(*_args, **_kwargs):
        raise HealthIngestError(422, "invalid_step_count")

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(health_api, "ingest_signed_batch", reject_batch)
    sensitive_body = b'{"record_type":"steps","values":{"count":987654}}'
    sensitive_device_id = "device-private-identifier"
    sensitive_batch_id = "batch-private-identifier"
    headers = {
        "X-Amigo-Device-Id": sensitive_device_id,
        "X-Amigo-Timestamp": "1787155200",
        "X-Amigo-Nonce": "nonce-private-identifier",
        "X-Amigo-Batch-Id": sensitive_batch_id,
        "X-Amigo-Signature": "signature-private-value",
    }

    with caplog.at_level(logging.WARNING, logger="amigo.health_api"):
        with TestClient(app) as client:
            response = client.post(
                "/amigo-ingest/v1/health-connect/batches",
                headers=headers,
                content=sensitive_body,
            )

    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "invalid_step_count"}}
    matching = [
        record
        for record in caplog.records
        if record.name == "amigo.health_api"
        and "Health ingest rejected" in record.getMessage()
    ]
    assert len(matching) == 1
    assert matching[0].args == ("invalid_step_count",)
    assert "invalid_step_count" in caplog.text
    for private_value in (
        sensitive_body.decode(),
        "987654",
        sensitive_device_id,
        sensitive_batch_id,
        "nonce-private-identifier",
        "signature-private-value",
    ):
        assert private_value not in caplog.text
