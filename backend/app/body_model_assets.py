"""Import a bounded, normalized private model bundle from Claude's local HTML.

Only the two JSON data scripts are parsed; no JavaScript or markup is executed.
The source HTML and its original image bytes never enter Git, logs, or the DB.
"""

from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
from html.parser import HTMLParser
from io import BytesIO
import json
import math
import sys
from typing import Any

from PIL import Image
from sqlalchemy.orm import Session

from .auth_models import UserBodyFace
from .db import SessionLocal


MODEL_ASSETS_VERSION = 1
MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_SOURCE_TEXTURE_BYTES = 1024 * 1024
MAX_HEAD_PNG_BYTES = 6 * 1024 * 1024
MAX_EAR_PNG_BYTES = 1024 * 1024
MAX_BUNDLE_BYTES = 9 * 1024 * 1024
HEAD_SIZE = (2048, 1024)
EAR_SIZE = (300, 501)
ANCHOR_NAMES = frozenset(
    {"eyeR", "eyeL", "nasion", "noseTip", "browR", "browL", "canthR", "canthL", "chin", "mouth"}
)


class _DataScripts(HTMLParser):
    """Capture only explicitly typed, unique data scripts."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.data: dict[str, str] = {}
        self._active: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "script":
            return
        attributes = dict(attrs)
        key = attributes.get("id")
        if key not in {"head-data", "ear-data"}:
            return
        if self._active is not None or key in self.data or attributes.get("type") != "application/json":
            raise ValueError("invalid private model data")
        self._active = key
        self._parts = []

    def handle_data(self, data: str) -> None:
        if self._active is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._active is not None:
            self.data[self._active] = "".join(self._parts)
            self._active = None
            self._parts = []


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("invalid private model data")
        value[key] = item
    return value


def _reject_constant(_: str) -> None:
    raise ValueError("invalid private model data")


def _json_object(value: str) -> dict[str, Any]:
    result = json.loads(value, object_pairs_hook=_unique_pairs, parse_constant=_reject_constant)
    if type(result) is not dict:
        raise ValueError("invalid private model data")
    return result


def _exact_object(value: Any, keys: set[str] | frozenset[str]) -> dict[str, Any]:
    if type(value) is not dict or value.keys() != keys:
        raise ValueError("invalid private model data")
    return value


def _number(value: Any, low: float, high: float) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError("invalid private model data")
    return float(value)


def _triplet(value: Any, low: float, high: float) -> list[float]:
    if type(value) is not list or len(value) != 3:
        raise ValueError("invalid private model data")
    return [_number(item, low, high) for item in value]


def _bytes_from_base64(value: Any, max_bytes: int) -> bytes:
    if type(value) is not str or not value or len(value) > (max_bytes + 2) // 3 * 4:
        raise ValueError("invalid private model data")
    try:
        result = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("invalid private model data") from None
    if not result or len(result) > max_bytes:
        raise ValueError("invalid private model data")
    return result


def _normalize_png(value: Any, size: tuple[int, int], limit: int) -> tuple[str, bytes]:
    if type(value) is not str:
        raise ValueError("invalid private model texture")
    prefixes = {"data:image/jpeg;base64,": "JPEG", "data:image/png;base64,": "PNG"}
    image_format = next((name for prefix, name in prefixes.items() if value.startswith(prefix)), None)
    if image_format is None:
        raise ValueError("invalid private model texture")
    source = _bytes_from_base64(value.split(",", 1)[1], MAX_SOURCE_TEXTURE_BYTES)
    try:
        with Image.open(BytesIO(source)) as image:
            if image.format != image_format or image.size != size:
                raise ValueError("invalid private model texture")
            image.load()
            pixels = image.convert("RGBA")
            pixels.info.clear()
            output = BytesIO()
            pixels.save(output, format="PNG")
            content = output.getvalue()
    except Exception:
        raise ValueError("invalid private model texture") from None
    if not content or len(content) > limit:
        raise ValueError("invalid private model texture")
    return "data:image/png;base64," + base64.b64encode(content).decode("ascii"), content


def _head(source: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
    _exact_object(source, {"nr", "seg", "q", "pos", "tex", "skin", "anchors", "crown", "bottom"})
    nr, seg = source["nr"], source["seg"]
    if type(nr) is not int or type(seg) is not int or not (2 <= nr <= 256 and 3 <= seg <= 256):
        raise ValueError("invalid private model data")
    vertices = nr * (seg + 1)
    if vertices > 40000:
        raise ValueError("invalid private model data")
    position = _bytes_from_base64(source["pos"], 40000 * 3 * 2)
    if len(position) != vertices * 3 * 2:
        raise ValueError("invalid private model data")
    anchors = _exact_object(source["anchors"], ANCHOR_NAMES)
    crown = _number(source["crown"], -1, 1)
    bottom = _number(source["bottom"], -1, 1)
    if crown <= bottom:
        raise ValueError("invalid private model data")
    texture_url, texture = _normalize_png(source["tex"], HEAD_SIZE, MAX_HEAD_PNG_BYTES)
    return {
        "nr": nr,
        "seg": seg,
        "q": _number(source["q"], 0.000001, 1),
        "pos": base64.b64encode(position).decode("ascii"),
        "skin": _triplet(source["skin"], 0, 255),
        "anchors": {key: _triplet(anchors[key], -1, 1) for key in sorted(ANCHOR_NAMES)},
        "crown": crown,
        "bottom": bottom,
        "tex": texture_url,
    }, texture


def _ear(source: dict[str, Any]) -> dict[str, Any]:
    _exact_object(source, {"pos", "uv", "idx", "tex", "top"})
    positions = source["pos"]
    uv = source["uv"]
    indices = source["idx"]
    if type(positions) is not list or not (9 <= len(positions) <= 6000) or len(positions) % 3:
        raise ValueError("invalid private model data")
    vertices = len(positions) // 3
    if type(uv) is not list or len(uv) != vertices * 2:
        raise ValueError("invalid private model data")
    if type(indices) is not list or not (3 <= len(indices) <= 30000) or len(indices) % 3:
        raise ValueError("invalid private model data")
    if any(type(index) is not int or not 0 <= index < vertices for index in indices):
        raise ValueError("invalid private model data")
    texture_url, _ = _normalize_png(source["tex"], EAR_SIZE, MAX_EAR_PNG_BYTES)
    return {
        "pos": [_number(item, -1, 1) for item in positions],
        "uv": [_number(item, -1, 2) for item in uv],
        "idx": indices,
        "top": _triplet(source["top"], -1, 1),
        "tex": texture_url,
    }


def normalize_assets_html(content: bytes) -> tuple[bytes, bytes]:
    """Return canonical JSON and a legacy fallback texture; reveal no source data."""
    if not content or len(content) > MAX_HTML_BYTES:
        raise ValueError("invalid private model HTML")
    try:
        parser = _DataScripts()
        parser.feed(content.decode("utf-8"))
        parser.close()
        if parser._active is not None or parser.data.keys() != {"head-data", "ear-data"}:
            raise ValueError("invalid private model HTML")
        head, head_png = _head(_json_object(parser.data["head-data"]))
        ear = _ear(_json_object(parser.data["ear-data"]))
        bundle = json.dumps(
            {"version": MODEL_ASSETS_VERSION, "head": head, "ear": ear},
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
        if len(bundle) > MAX_BUNDLE_BYTES:
            raise ValueError("invalid private model bundle")
        with Image.open(BytesIO(head_png)) as image:
            fallback = image.copy()
            fallback.thumbnail((512, 512))
            fallback.info.clear()
            output = BytesIO()
            fallback.save(output, format="PNG")
            legacy_texture = output.getvalue()
        if len(legacy_texture) > 512 * 1024:
            raise ValueError("invalid private model texture")
        return bundle, legacy_texture
    except Exception:
        raise ValueError("private body model import failed") from None


def import_assets(db: Session, content: bytes) -> bool:
    """Import once, or update only when normalized assets changed."""
    bundle, legacy_texture = normalize_assets_html(content)
    row = db.get(UserBodyFace, 1)
    if row is None:
        db.add(
            UserBodyFace(
                id=1,
                content=legacy_texture,
                model_assets_version=MODEL_ASSETS_VERSION,
                model_assets_content=bundle,
            )
        )
    elif row.model_assets_version == MODEL_ASSETS_VERSION and row.model_assets_content == bundle:
        return False
    else:
        row.model_assets_version = MODEL_ASSETS_VERSION
        row.model_assets_content = bundle
        row.updated_at = datetime.now(timezone.utc)
    db.commit()
    return True


def main() -> None:
    try:
        content = sys.stdin.buffer.read(MAX_HTML_BYTES + 1)
        with SessionLocal() as db:
            changed = import_assets(db, content)
    except Exception:
        raise SystemExit("private body model import failed") from None
    print("private body model assets imported" if changed else "private body model assets unchanged")


if __name__ == "__main__":
    main()
