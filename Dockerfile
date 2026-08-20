FROM node:22-alpine AS frontend-builder

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci \
    --fetch-retries=5 \
    --fetch-retry-mintimeout=20000 \
    --fetch-retry-maxtimeout=120000
COPY frontend/ ./
RUN npm run build


FROM python:3.12-slim-bookworm AS runtime

ARG AMIGO_BUILD_SHA=unknown
LABEL org.opencontainers.image.title="Amigo v3" \
      org.opencontainers.image.revision="${AMIGO_BUILD_SHA}" \
      org.opencontainers.image.source="/srv/amigo"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    AMIGO_STATIC_DIR=/app/static

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates fonts-dejavu-core libheif1 tesseract-ocr tesseract-ocr-eng tesseract-ocr-rus \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend
COPY backend/ ./
RUN python -m pip install --no-cache-dir .
COPY --from=frontend-builder /build/frontend/dist /app/static

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
