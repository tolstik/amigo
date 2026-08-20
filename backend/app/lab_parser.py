from __future__ import annotations

from io import BytesIO
import logging
import math
import warnings

import fitz
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener
import pytesseract
import uvicorn

from .labs import MAX_IMAGE_PIXELS, MAX_LAB_FILE_BYTES, MAX_LAB_PAGES, LabFileError, detect_media_type


logger = logging.getLogger("amigo.lab.parser")
MAX_EXTRACTED_TEXT = 500_000
MAX_COORDINATE_BLOCKS = 5_000
register_heif_opener()
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


class ParserError(RuntimeError):
    pass


def _ocr(
    image: Image.Image,
    *,
    block_limit: int = MAX_COORDINATE_BLOCKS,
) -> tuple[str, list[dict[str, object]]]:
    if image.width * image.height > MAX_IMAGE_PIXELS:
        raise ParserError("image_too_large")
    image = ImageOps.exif_transpose(image)
    if image.width * image.height > MAX_IMAGE_PIXELS:
        raise ParserError("image_too_large")
    image = image.convert("RGB")
    text = pytesseract.image_to_string(image, lang="rus+eng", config="--psm 6").strip()
    if block_limit <= 0:
        return text, []
    data = pytesseract.image_to_data(
        image,
        lang="rus+eng",
        config="--psm 6",
        output_type=pytesseract.Output.DICT,
    )
    blocks: list[dict[str, object]] = []
    for index, token in enumerate(data.get("text", [])):
        value = str(token or "").strip()
        if not value:
            continue
        try:
            confidence = float(data["conf"][index])
        except (ValueError, TypeError, KeyError, IndexError):
            confidence = -1
        if confidence < 0:
            continue
        blocks.append(
            {
                "text": value[:120],
                "x": int(data["left"][index]),
                "y": int(data["top"][index]),
                "width": int(data["width"][index]),
                "height": int(data["height"][index]),
            }
        )
        if len(blocks) >= block_limit:
            break
    return text, blocks


def _pdf_raster_pixels(page: fitz.Page, zoom: float = 2.0) -> int:
    bounds = page.rect * fitz.Matrix(zoom, zoom)
    width = max(0, math.ceil(abs(bounds.width)))
    height = max(0, math.ceil(abs(bounds.height)))
    return width * height


def parse_document(data: bytes, filename: str) -> dict[str, object]:
    if not data or len(data) > MAX_LAB_FILE_BYTES:
        raise ParserError("invalid_size")
    try:
        media_type = detect_media_type(data[:64], filename)
    except LabFileError as exc:
        raise ParserError(str(exc)) from exc
    pages: list[dict[str, object]] = []
    coordinate_blocks = 0
    if media_type == "application/pdf":
        try:
            document = fitz.open(stream=data, filetype="pdf")
        except Exception as exc:
            raise ParserError("invalid_pdf") from exc
        try:
            if document.needs_pass:
                raise ParserError("encrypted_pdf")
            if document.page_count < 1 or document.page_count > MAX_LAB_PAGES:
                raise ParserError("page_limit")
            for page_number, page in enumerate(document, start=1):
                blocks = []
                extracted_blocks = page.get_text("blocks", sort=True)
                text = page.get_text("text", sort=True).strip()
                remaining_blocks = max(0, MAX_COORDINATE_BLOCKS - coordinate_blocks)
                for block in extracted_blocks[:remaining_blocks]:
                    block_text = str(block[4] or "").strip()
                    if block_text:
                        blocks.append(
                            {
                                "text": block_text[:1000],
                                "x": round(float(block[0]), 2),
                                "y": round(float(block[1]), 2),
                                "width": round(float(block[2] - block[0]), 2),
                                "height": round(float(block[3] - block[1]), 2),
                            }
                        )
                coordinate_blocks += len(blocks)
                if len(text) < 40:
                    if _pdf_raster_pixels(page) > MAX_IMAGE_PIXELS:
                        raise ParserError("image_too_large")
                    pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                    if pixmap.width * pixmap.height > MAX_IMAGE_PIXELS:
                        raise ParserError("image_too_large")
                    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                    text, blocks = _ocr(
                        image,
                        block_limit=max(0, MAX_COORDINATE_BLOCKS - coordinate_blocks),
                    )
                    coordinate_blocks += len(blocks)
                pages.append({"page": page_number, "text": text, "blocks": blocks})
        finally:
            document.close()
    else:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(data)) as image:
                    text, blocks = _ocr(image)
        except ParserError:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ParserError("image_too_large") from exc
        except Exception as exc:
            raise ParserError("invalid_image") from exc
        pages.append({"page": 1, "text": text, "blocks": blocks})
    full_text = "\n\n".join(str(page["text"]) for page in pages).strip()
    if not full_text:
        raise ParserError("no_text")
    if len(full_text) > MAX_EXTRACTED_TEXT:
        raise ParserError("text_too_large")
    return {"media_type": media_type, "page_count": len(pages), "text": full_text, "pages": pages}


app = FastAPI(title="Amigo isolated laboratory parser", docs_url=None, redoc_url=None, openapi_url=None)


@app.exception_handler(Exception)
async def unhandled(_request, error: Exception):
    logger.warning("lab parser request failed type=%s", type(error).__name__)
    return JSONResponse(status_code=500, content={"detail": "parser_failed"})


@app.get("/healthz")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/parse")
async def parse(file: UploadFile = File(...)) -> dict[str, object]:
    data = await file.read(MAX_LAB_FILE_BYTES + 1)
    try:
        return parse_document(data, file.filename or "analysis")
    except ParserError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run(app, host="0.0.0.0", port=8085, access_log=False, server_header=False)


if __name__ == "__main__":
    main()
