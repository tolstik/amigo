from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import get_db
from app.health_api import ingest_router, public_router


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
