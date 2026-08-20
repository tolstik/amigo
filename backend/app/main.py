from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from . import __version__
from .api import router
from .auth import auth_router, profile_router, require_session
from .assistant_api import router as assistant_router
from .health_api import public_router as health_public_router
from .labs_api import router as labs_router
from .config import get_settings
from .db import SessionLocal


app = FastAPI(
    title="Amigo API",
    version=__version__,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.include_router(auth_router)
app.include_router(profile_router, dependencies=[Depends(require_session)])
app.include_router(router, dependencies=[Depends(require_session)])
app.include_router(health_public_router, dependencies=[Depends(require_session)])
app.include_router(labs_router, dependencies=[Depends(require_session)])
app.include_router(assistant_router, dependencies=[Depends(require_session)])


@app.middleware("http")
async def privacy_headers(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/assets/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
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


@app.get("/internal/health", include_in_schema=False)
def internal_health() -> dict[str, str]:
    return health()


static_root = get_settings().static_dir
if (static_root / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=static_root / "assets"), name="assets")


@app.get("/{full_path:path}", include_in_schema=False)
def spa_fallback(full_path: str):
    if full_path.startswith(("api/", "internal/")) or full_path == "healthz":
        raise HTTPException(status_code=404, detail="not found")
    index = static_root / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=404, detail="frontend is not installed")
    return FileResponse(index)
