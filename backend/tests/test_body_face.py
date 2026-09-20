from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin
import pytest

from app.auth import require_session
from app.auth_models import UserBodyFace
from app.body_face import import_texture, normalize_texture
from app.db import get_db
from app.main import app


def texture():
    output = BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("source", "private source metadata")
    Image.new("RGBA", (384, 512), (150, 120, 90, 255)).save(output, format="PNG", pnginfo=metadata)
    return output.getvalue()


def test_body_face_normalizes_and_strips_source_metadata():
    with Image.open(BytesIO(normalize_texture(texture()))) as result:
        assert result.size == (384, 512)
        assert result.info == {}
        assert result.mode == "RGBA"
    for invalid in (b"", b"not an image", b"x" * (512 * 1024 + 1)):
        with pytest.raises(ValueError):
            normalize_texture(invalid)


def test_body_face_rejects_oversized_dimensions():
    output = BytesIO()
    Image.new("RGB", (1025, 1)).save(output, format="PNG")
    with pytest.raises(ValueError):
        normalize_texture(output.getvalue())


def test_face_is_private_idempotent_and_returned_as_no_store_png(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/profile/body-face").status_code == 401
            app.dependency_overrides[require_session] = lambda: object()
            assert client.get("/api/v1/profile/body-face").status_code == 404
            import_texture(db, texture())
            import_texture(db, texture())
            assert db.query(UserBodyFace).count() == 1
            response = client.get("/api/v1/profile/body-face")
            assert response.status_code == 200
            assert response.headers["content-type"] == "image/png"
            assert response.headers["cache-control"] == "no-store"
            assert response.content == normalize_texture(texture())
    finally:
        app.dependency_overrides.clear()
