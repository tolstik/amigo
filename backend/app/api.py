from __future__ import annotations

import csv
from datetime import date, datetime, timezone
from decimal import Decimal
from io import StringIO
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .db import get_db
from .ai_queue import public_analysis_payload
from .auth import AuthContext, require_csrf
from .health_analytics import activity_series, recovery_series
from .body_measurements_models import BodyCircumference
from .medication_models import Medication
from .service import circumference_series, composition_series, overview, pressure_series, weight_series


RangeParam = Annotated[Literal["program", "30d", "90d", "1y", "all"], Query()]
router = APIRouter(prefix="/api/v1")


class CircumferenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    waist_cm: Decimal | None = Field(default=None, ge=20, le=300, max_digits=6, decimal_places=2)
    hip_cm: Decimal | None = Field(default=None, ge=20, le=300, max_digits=6, decimal_places=2)

    @model_validator(mode="after")
    def has_value(self) -> "CircumferenceInput":
        if self.waist_cm is None and self.hip_cm is None:
            raise ValueError("at least one circumference is required")
        return self


class MedicationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    dosage: str = Field(min_length=1, max_length=80)
    schedule: str | None = Field(default=None, max_length=120)


class MedicationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=120)
    dosage: str | None = Field(default=None, min_length=1, max_length=80)
    schedule: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def has_value(self) -> "MedicationPatch":
        if not self.model_fields_set:
            raise ValueError("at least one medication field is required")
        return self


def _medication(row: Medication) -> dict[str, object]:
    return {
        "id": row.id,
        "name": row.name,
        "dosage": row.dosage,
        "schedule": row.schedule,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _enqueue_medication_analysis(db: Session, settings: Settings) -> None:
    # Keep the API import graph light and mirror profile/laboratory mutations.
    from .ai_snapshot import enqueue_current_analysis

    enqueue_current_analysis(db, settings, trigger="manual", debounce_seconds=0)


def _ensure_medication_capacity(db: Session) -> None:
    if (db.scalar(select(func.count()).select_from(Medication)) or 0) >= 32:
        raise HTTPException(status_code=422, detail="too_many_medications")


@router.get("/medications")
def get_medications(db: Session = Depends(get_db)) -> dict[str, list[dict[str, object]]]:
    rows = db.scalars(select(Medication).order_by(Medication.name, Medication.id)).all()
    return {"items": [_medication(row) for row in rows]}


@router.post("/medications", status_code=status.HTTP_201_CREATED)
def create_medication(
    payload: MedicationInput,
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    _ensure_medication_capacity(db)
    row = Medication(
        name=payload.name,
        dosage=payload.dosage,
        schedule=payload.schedule or None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    _enqueue_medication_analysis(db, settings)
    return _medication(row)


@router.patch("/medications/{medication_id}")
def patch_medication(
    medication_id: str,
    payload: MedicationPatch,
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    row = db.get(Medication, medication_id)
    if row is None:
        raise HTTPException(status_code=404, detail="medication_not_found")
    values = payload.model_dump(exclude_unset=True)
    if "name" in values:
        row.name = values["name"]
    if "dosage" in values:
        row.dosage = values["dosage"]
    if "schedule" in values:
        row.schedule = values["schedule"] or None
    db.commit()
    db.refresh(row)
    _enqueue_medication_analysis(db, settings)
    return _medication(row)


@router.delete("/medications/{medication_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_medication(
    medication_id: str,
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    row = db.get(Medication, medication_id)
    if row is None:
        raise HTTPException(status_code=404, detail="medication_not_found")
    db.delete(row)
    db.commit()
    _enqueue_medication_analysis(db, settings)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _circumference(row: BodyCircumference) -> dict[str, object]:
    return {
        "measured_on": row.measured_on,
        "waist_cm": float(row.waist_cm) if row.waist_cm is not None else None,
        "hip_cm": float(row.hip_cm) if row.hip_cm is not None else None,
    }


@router.get("/overview")
def get_overview(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    return overview(db, settings.tz)


@router.get("/series/weight")
def get_weight_series(
    range: RangeParam = "program",
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    return weight_series(db, settings.tz, range)


@router.get("/series/pressure")
def get_pressure_series(
    range: RangeParam = "program",
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    return pressure_series(db, settings.tz, range)


@router.get("/series/composition")
def get_composition_series(
    range: RangeParam = "program",
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    return composition_series(db, settings.tz, range)


@router.get("/series/circumference")
def get_circumference_series(
    range: RangeParam = "all",
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    return circumference_series(db, settings.tz, range)


@router.put("/body-measurements/{measured_on}", status_code=status.HTTP_200_OK)
def upsert_circumference(
    payload: CircumferenceInput,
    measured_on: date = Path(...),
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    today = datetime.now(timezone.utc).astimezone(settings.tz).date()
    if measured_on > today:
        raise HTTPException(status_code=422, detail="future_measurement_date")
    row = db.scalar(
        select(BodyCircumference)
        .where(BodyCircumference.measured_on == measured_on)
        .with_for_update()
    )
    if row is None:
        row = BodyCircumference(measured_on=measured_on)
        db.add(row)
    row.waist_cm = payload.waist_cm
    row.hip_cm = payload.hip_cm
    db.commit()
    db.refresh(row)
    return _circumference(row)


@router.delete("/body-measurements/{measured_on}", status_code=status.HTTP_204_NO_CONTENT)
def delete_circumference(
    measured_on: date = Path(...),
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> Response:
    row = db.scalar(select(BodyCircumference).where(BodyCircumference.measured_on == measured_on))
    if row is not None:
        db.delete(row)
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/insights")
def get_insights(db: Session = Depends(get_db)) -> dict:
    payload = _public_ai_analysis(db)
    items = [
        {
            "id": item["id"],
            "title": item["title"],
            "message": item["text"],
            "text": item["text"],
            "tone": item.get("tone", "neutral"),
            "evidence_ids": item["evidence_ids"],
        }
        for item in payload["insights"]
    ]
    cited = {
        key
        for item in items
        for key in item["evidence_ids"]
        if isinstance(key, str)
    }
    return {
        "generated_at": payload["generated_at"],
        "data_as_of": payload["data_as_of"],
        "status": payload["status"],
        "ai_generated": True,
        "items": items,
        "evidence": {
            key: descriptor
            for key, descriptor in payload["evidence"].items()
            if key in cited
        },
    }


def _public_ai_analysis(db: Session) -> dict:
    stored = public_analysis_payload(db)
    analysis = stored.get("analysis") if isinstance(stored.get("analysis"), dict) else {}
    status = {"ready": "fresh", "stale": "stale", "pending": "pending"}.get(
        str(stored.get("status")), "unavailable"
    )
    observations = analysis.get("observations") if isinstance(analysis.get("observations"), list) else []
    recommendations = analysis.get("recommendations") if isinstance(analysis.get("recommendations"), list) else []
    evidence = stored.get("evidence") if isinstance(stored.get("evidence"), dict) else {}

    def items(values: list, prefix: str) -> list[dict]:
        result = []
        for index, value in enumerate(values):
            if not isinstance(value, dict) or not isinstance(value.get("text"), str):
                continue
            result.append(
                {
                    "id": f"{prefix}-{index + 1}",
                    "title": str(value.get("title") or "Наблюдение"),
                    "text": value["text"],
                    "tone": str(value.get("tone") or "neutral"),
                    "evidence_ids": [
                        key for key in value.get("evidence_keys", []) if isinstance(key, str)
                    ],
                }
            )
        return result

    return {
        "analysis_id": stored.get("analysis_id"),
        "status": status,
        "headline": analysis.get("headline") if isinstance(analysis.get("headline"), str) else None,
        "summary": analysis.get("summary") if isinstance(analysis.get("summary"), str) else None,
        "insights": items(observations, "observation"),
        "recommendations": items(recommendations, "recommendation"),
        "limitations": [
            value for value in analysis.get("limitations", []) if isinstance(value, str)
        ],
        "confidence": analysis.get("confidence"),
        "generated_at": stored.get("generated_at"),
        "data_as_of": stored.get("source_through"),
        "model": stored.get("model"),
        "prompt_version": stored.get("prompt_version"),
        "ai_generated": True,
        "evidence": {
            key: value
            for key, value in evidence.items()
            if isinstance(key, str) and isinstance(value, dict)
        },
    }


@router.get("/ai-analysis")
def get_ai_analysis(db: Session = Depends(get_db)) -> dict:
    return _public_ai_analysis(db)


def _csv_response(filename: str, header: list[str], rows: list[list[object]]) -> Response:
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(header)
    writer.writerows(rows)
    return Response(
        output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/export/{kind}.csv")
def export_csv(
    kind: Literal["weight", "pressure", "composition", "activity", "recovery", "circumference"],
    range: RangeParam = "all",
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    if kind == "weight":
        payload = weight_series(db, settings.tz, range)
        rows = [[row["measured_at"], row["value"], "kg"] for row in payload["raw"]]
        return _csv_response("amigo-weight.csv", ["measured_at", "value", "unit"], rows)
    if kind == "pressure":
        payload = pressure_series(db, settings.tz, range)
        rows = [
            [
                row["measured_at"],
                row["systolic"],
                row["diastolic"],
                row["pulse"],
                row["pulse_pressure"],
                row["sample_count"],
            ]
            for row in payload["sessions"]
        ]
        return _csv_response(
            "amigo-pressure.csv",
            ["measured_at", "systolic_mmHg", "diastolic_mmHg", "pulse_bpm", "pulse_pressure", "samples"],
            rows,
        )
    if kind == "circumference":
        payload = circumference_series(db, settings.tz, range)
        rows = [
            [row["measured_on"], row["waist_cm"], row["hip_cm"], "cm"]
            for row in payload["points"]
        ]
        return _csv_response(
            "amigo-circumference.csv",
            ["measured_on", "waist_cm", "hip_cm", "unit"],
            rows,
        )
    if kind == "activity":
        payload = activity_series(db, settings.tz, "all" if range == "program" else range)
        rows = [
            [
                row["date"],
                row.get("steps"),
                row.get("distance_km"),
                row.get("active_calories_kcal"),
                row.get("active_minutes"),
                row.get("workouts"),
            ]
            for row in payload["daily"]
        ]
        return _csv_response(
            "amigo-activity.csv",
            ["date", "steps", "distance_km", "active_calories_kcal", "active_minutes", "workouts"],
            rows,
        )
    if kind == "recovery":
        payload = recovery_series(db, settings.tz, "all" if range == "program" else range)
        rows = [
            [
                row["date"],
                row.get("sleep_minutes"),
                row.get("deep_sleep_minutes"),
                row.get("rem_sleep_minutes"),
                row.get("average_heart_rate_bpm"),
                row.get("minimum_heart_rate_bpm"),
                row.get("maximum_heart_rate_bpm"),
                row.get("resting_heart_rate_bpm"),
                row.get("hrv_rmssd_ms"),
                row.get("spo2_pct"),
                row.get("vo2_max"),
            ]
            for row in payload["daily"]
        ]
        return _csv_response(
            "amigo-recovery.csv",
            [
                "date",
                "sleep_minutes",
                "deep_sleep_minutes",
                "rem_sleep_minutes",
                "average_heart_rate_bpm",
                "minimum_heart_rate_bpm",
                "maximum_heart_rate_bpm",
                "resting_heart_rate_bpm",
                "hrv_rmssd_ms",
                "spo2_pct",
                "vo2_max",
            ],
            rows,
        )
    payload = composition_series(db, settings.tz, range)
    rows = [
        [row["measured_at"], measurement_kind, row["value"], payload["units"].get(measurement_kind)]
        for measurement_kind, values in payload["series"].items()
        for row in values
    ]
    return _csv_response("amigo-composition.csv", ["measured_at", "kind", "value", "unit"], rows)
