from __future__ import annotations

import csv
from io import StringIO
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .db import get_db
from .service import composition_series, insights, overview, pressure_series, weight_series


RangeParam = Annotated[Literal["program", "30d", "90d", "1y", "all"], Query()]
router = APIRouter(prefix="/api/v1")


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


@router.get("/insights")
def get_insights(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    return insights(db, settings.tz)


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
    kind: Literal["weight", "pressure", "composition"],
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
    payload = composition_series(db, settings.tz, range)
    rows = [
        [row["measured_at"], measurement_kind, row["value"], payload["units"].get(measurement_kind)]
        for measurement_kind, values in payload["series"].items()
        for row in values
    ]
    return _csv_response("amigo-composition.csv", ["measured_at", "kind", "value", "unit"], rows)
