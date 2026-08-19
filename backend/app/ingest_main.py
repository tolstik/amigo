from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from sqlalchemy import text

from . import __version__
from .db import SessionLocal
from .health_api import ingest_router


app = FastAPI(
    title="Amigo Health Connect ingest",
    version=__version__,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.include_router(ingest_router)


@app.middleware("http")
async def ingest_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, noarchive"
    return response


@app.get("/healthz", include_in_schema=False)
def health() -> dict[str, str]:
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ok", "version": __version__}
