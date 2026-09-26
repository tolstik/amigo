"""Synthetic private assets only; no personal geometry or texture fixtures."""

import base64
from io import BytesIO
import importlib.util
import json
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin
import pytest
from sqlalchemy import create_engine, inspect, text

from app.auth import require_session
from app.auth_models import UserBodyFace
from app.body_face import import_texture
from app.body_model_assets import (
    ANCHOR_NAMES,
    EAR_SIZE,
    HEAD_SIZE,
    MAX_HTML_BYTES,
    import_assets,
    normalize_assets_html,
)
from app.db import get_db
from app.main import app


def _image(size):
    output = BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("source", "synthetic source metadata")
    Image.new("RGB", size, (150, 120, 90)).save(output, format="PNG", pnginfo=metadata)
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def _data():
    head = {
        "nr": 2,
        "seg": 3,
        "q": 0.2,
        "pos": base64.b64encode(bytes(2 * (3 + 1) * 3 * 2)).decode("ascii"),
        "tex": _image(HEAD_SIZE),
        "skin": [150.0, 120.0, 90.0],
        "anchors": {key: [0.0, 0.0, 0.0] for key in ANCHOR_NAMES},
        "crown": 0.12,
        "bottom": -0.14,
    }
    ear = {
        "pos": [0, 0, 0, 0.02, 0, 0, 0, 0.02, 0],
        "uv": [0, 0, 1, 0, 0, 1],
        "idx": [0, 1, 2],
        "tex": _image(EAR_SIZE),
        "top": [0, 0.02, 0],
    }
    return head, ear


def _html(head, ear):
    return (
        '<script type="module">throw new Error("must not execute")</script>'
        '<script type="application/json" id="head-data">'
        + json.dumps(head)
        + '</script><script type="application/json" id="ear-data">'
        + json.dumps(ear)
        + "</script>"
    ).encode("utf-8")


def _png_from_url(value):
    assert value.startswith("data:image/png;base64,")
    return base64.b64decode(value.split(",", 1)[1])


def test_normalizes_both_textures_preserving_atlas_dimensions_and_stripping_metadata():
    head, ear = _data()
    html = _html(head, ear)
    content, legacy = normalize_assets_html(html)
    assert content == normalize_assets_html(html)[0]
    result = json.loads(content)
    assert result["version"] == 1
    assert set(result) == {"version", "head", "ear"}
    for data, size in ((result["head"], HEAD_SIZE), (result["ear"], EAR_SIZE)):
        with Image.open(BytesIO(_png_from_url(data["tex"]))) as image:
            assert image.format == "PNG"
            assert image.size == size
            assert image.info == {}
    with Image.open(BytesIO(legacy)) as image:
        assert image.format == "PNG"
        assert image.width <= 512 and image.height <= 512
        assert image.info == {}


def test_private_endpoint_no_store_and_reimport_preserves_legacy_texture(db):
    old = BytesIO()
    Image.new("RGB", (120, 120), (100, 80, 60)).save(old, format="PNG")
    import_texture(db, old.getvalue())
    row = db.get(UserBodyFace, 1)
    old_content = row.content
    head, ear = _data()
    html = _html(head, ear)
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/profile/body-model-assets").status_code == 401
            app.dependency_overrides[require_session] = lambda: object()
            unavailable = client.get("/api/v1/profile/body-model-assets")
            assert unavailable.status_code == 404
            assert unavailable.headers["cache-control"] == "no-store"
            assert import_assets(db, html) is True
            first_updated_at = row.updated_at
            assert import_assets(db, html) is False
            assert db.query(UserBodyFace).count() == 1
            assert row.content == old_content
            assert row.updated_at == first_updated_at
            response = client.get("/api/v1/profile/body-model-assets")
            assert response.status_code == 200
            assert response.headers["content-type"] == "application/json"
            assert response.headers["cache-control"] == "no-store"
            assert response.content == row.model_assets_content
            assert response.json()["version"] == 1
            legacy = client.get("/api/v1/profile/body-face")
            assert legacy.status_code == 200
            assert legacy.headers["cache-control"] == "no-store"
            assert legacy.content == old_content
    finally:
        app.dependency_overrides.clear()


def test_first_import_creates_legacy_fallback_for_previous_release(db):
    head, ear = _data()
    assert import_assets(db, _html(head, ear)) is True
    row = db.get(UserBodyFace, 1)
    assert row.model_assets_version == 1
    assert row.model_assets_content is not None
    with Image.open(BytesIO(row.content)) as image:
        assert image.format == "PNG"
        assert image.width <= 512 and image.height <= 512


@pytest.mark.parametrize("mutate", [
    lambda h, e: h.update(q=float("nan")),
    lambda h, e: h.update(pos="invalid!"),
    lambda h, e: h.update(unexpected="secret"),
    lambda h, e: e.update(idx=[0, 1, 3]),
    lambda h, e: e.update(uv=[0, 0]),
    lambda h, e: e.update(tex=_image((400, 400))),
])
def test_rejects_invalid_data_without_exposing_it(mutate):
    head, ear = _data()
    mutate(head, ear)
    with pytest.raises(ValueError, match="^private body model import failed$"):
        normalize_assets_html(_html(head, ear))


def test_rejects_duplicate_script_and_oversized_html():
    head, ear = _data()
    valid = _html(head, ear)
    with pytest.raises(ValueError, match="^private body model import failed$"):
        normalize_assets_html(valid + b'<script type="application/json" id="head-data">{}</script>')
    with pytest.raises(ValueError, match="^invalid private model HTML$"):
        normalize_assets_html(b"x" * (MAX_HTML_BYTES + 1))


def test_migration_downgrade_preserves_legacy_texture():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/20260926_0016_body_model_assets.py"
    spec = importlib.util.spec_from_file_location("body_model_assets_migration", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE user_body_face (id INTEGER PRIMARY KEY, content BLOB NOT NULL, updated_at DATETIME NOT NULL)"
        ))
        original = b"legacy image bytes"
        connection.execute(
            text("INSERT INTO user_body_face (id, content, updated_at) VALUES (1, :content, '2026-09-26')"),
            {"content": original},
        )
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        columns = {column["name"] for column in inspect(connection).get_columns("user_body_face")}
        assert {"content", "model_assets_version", "model_assets_content"} <= columns
        connection.execute(text(
            "UPDATE user_body_face SET model_assets_version=1, model_assets_content='private' WHERE id=1"
        ))
        migration.downgrade()
        columns = {column["name"] for column in inspect(connection).get_columns("user_body_face")}
        assert columns == {"id", "content", "updated_at"}
        assert connection.execute(text("SELECT content FROM user_body_face WHERE id=1")).scalar_one() == original
