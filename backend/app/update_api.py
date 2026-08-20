from __future__ import annotations

from functools import lru_cache
from hashlib import sha256
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from .config import Settings, get_settings


router = APIRouter(prefix="/api/v1/app-update", tags=["app-update"])


def _apk(settings: Settings):
    path = settings.android_apk_path
    if not path.is_file() or path.is_symlink():
        raise HTTPException(status_code=404, detail="update_not_available")
    return path


@lru_cache(maxsize=4)
def _apk_digest(path_value: str, size_bytes: int, modified_ns: int) -> str:
    path = Path(path_value)
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    if path.stat().st_size != size_bytes or path.stat().st_mtime_ns != modified_ns:
        raise HTTPException(status_code=503, detail="update_changed_during_verification")
    return digest.hexdigest()


@router.get("")
def update_metadata(settings: Settings = Depends(get_settings)) -> dict:
    path = _apk(settings)
    file_stat = path.stat()
    return {
        "version_code": settings.android_apk_version_code,
        "version_name": settings.android_apk_version_name,
        "size_bytes": file_stat.st_size,
        "sha256": _apk_digest(str(path), file_stat.st_size, file_stat.st_mtime_ns),
        "download_url": "/amigo/api/v1/app-update/apk",
    }


@router.get("/apk")
def download_update(settings: Settings = Depends(get_settings)) -> FileResponse:
    path = _apk(settings)
    return FileResponse(
        path,
        media_type="application/vnd.android.package-archive",
        filename=f"amigo-sync-{settings.android_apk_version_name}.apk",
    )
