from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from .ai_snapshot import enqueue_current_analysis
from .config import Settings, get_settings
from .db import get_db
from .health_analytics import activity_series, recovery_series
from .health_ingest import (
    HealthIngestError,
    get_device_status,
    ingest_signed_batch,
    register_device,
)
from .health_schemas import (
    BatchAcceptedResponse,
    DeviceRegistrationRequest,
    DeviceRegistrationResponse,
    DeviceStatusResponse,
    HealthBatchInput,
)


logger = logging.getLogger("amigo.health_api")
HealthRangeParam = Annotated[Literal["30d", "90d", "1y", "all"], Query()]
public_router = APIRouter(prefix="/api/v1", tags=["health-analytics"])
ingest_router = APIRouter(prefix="/amigo-ingest/v1", tags=["health-connect-ingest"])


def _raise_http(exc: HealthIngestError) -> None:
    raise HTTPException(status_code=exc.status_code, detail={"code": exc.code}) from exc


@public_router.get("/series/activity")
def get_activity_series(
    range: HealthRangeParam = "90d",
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    return activity_series(db, settings.tz, range)


@public_router.get("/series/recovery")
def get_recovery_series(
    range: HealthRangeParam = "90d",
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    return recovery_series(db, settings.tz, range)


@ingest_router.post(
    "/devices/register",
    response_model=DeviceRegistrationResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_device_registration(
    payload: DeviceRegistrationRequest,
    db: Session = Depends(get_db),
) -> DeviceRegistrationResponse:
    try:
        return register_device(db, payload)
    except HealthIngestError as exc:
        _raise_http(exc)


@ingest_router.get(
    "/devices/{device_id}/status",
    response_model=DeviceStatusResponse,
)
def get_registration_status(
    device_id: str,
    db: Session = Depends(get_db),
) -> DeviceStatusResponse:
    try:
        return get_device_status(db, device_id)
    except HealthIngestError as exc:
        _raise_http(exc)


@ingest_router.post(
    "/health-connect/batches",
    response_model=BatchAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def post_health_connect_batch(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> BatchAcceptedResponse:
    headers = request.headers
    required = {
        "device_id": headers.get("x-amigo-device-id"),
        "timestamp": headers.get("x-amigo-timestamp"),
        "nonce": headers.get("x-amigo-nonce"),
        "batch_id": headers.get("x-amigo-batch-id"),
        "signature": headers.get("x-amigo-signature"),
    }
    if any(value is None for value in required.values()):
        raise HTTPException(
            status_code=400,
            detail={"code": "missing_signature_header"},
        )
    raw_body = await request.body()
    try:
        response = ingest_signed_batch(db, raw_body=raw_body, **required)  # type: ignore[arg-type]
    except HealthIngestError as exc:
        # This endpoint accepts sensitive health records. Keep rejection telemetry
        # deliberately limited to the stable, non-identifying error code: never
        # log bodies, headers, device IDs, batch IDs, or validation errors.
        logger.warning("Health ingest rejected detail.code=%s", exc.code)
        _raise_http(exc)
    if not response.idempotent:
        try:
            envelope = HealthBatchInput.model_validate_json(raw_body)
            enqueue_current_analysis(
                db,
                settings,
                trigger="measurement" if envelope.record_type in {"sleep", "exercise"} else "activity",
            )
        except Exception as exc:
            db.rollback()
            logger.warning("AI enqueue after health batch failed: %s", type(exc).__name__)
    return response
