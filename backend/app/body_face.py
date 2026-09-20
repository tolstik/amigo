"""Private body texture import. Input and image metadata are never logged."""
from datetime import datetime, timezone
from io import BytesIO
import sys

from PIL import Image
from sqlalchemy.orm import Session

from .auth_models import UserBodyFace
from .db import SessionLocal

MAX_TEXTURE_BYTES = 512 * 1024


def normalize_texture(content: bytes) -> bytes:
    if not content or len(content) > MAX_TEXTURE_BYTES:
        raise ValueError("invalid body texture size")
    try:
        with Image.open(BytesIO(content)) as source:
            if source.format != "PNG" or source.width > 1024 or source.height > 1024:
                raise ValueError("invalid body texture format")
            image = source.convert("RGBA")
            image.thumbnail((512, 512))
            image.info.clear()
            output = BytesIO()
            # Save only pixels: no filename, EXIF, comments or source metadata.
            image.save(output, format="PNG")
            result = output.getvalue()
    except Exception:
        raise ValueError("invalid body texture") from None
    if len(result) > MAX_TEXTURE_BYTES:
        raise ValueError("invalid body texture size")
    return result


def import_texture(db: Session, content: bytes) -> None:
    normalized = normalize_texture(content)
    row = db.get(UserBodyFace, 1)
    if row is None:
        db.add(UserBodyFace(id=1, content=normalized))
    elif row.content != normalized:
        row.content = normalized
        row.updated_at = datetime.now(timezone.utc)
    db.commit()


def main():
    try:
        content = sys.stdin.buffer.read(MAX_TEXTURE_BYTES + 1)
        with SessionLocal() as db:
            import_texture(db, content)
    except Exception:
        raise SystemExit("private body texture import failed") from None
    print("private body texture imported")


if __name__ == "__main__":
    main()
