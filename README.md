# Amigo v2

Personal, read-only health dashboard that synchronizes weight, body composition,
blood pressure, and pulse measurements from Withings. It tracks the program that
started on 2026-08-15, keeps a separate all-history view, and sends concise
Telegram updates.

## Architecture

- `backend/` — FastAPI API, analytics, Withings synchronization, Telegram outbox,
  scheduler, and PostgreSQL migrations.
- `frontend/` — React/TypeScript dashboard built for the `/amigo/` base path.
- `deploy/` — safe backup, deployment, cutover, verification, and rollback tools.
- `docs/runbook.md` — production operating procedure and deployment checkpoint.

The production stack uses Docker Compose with `web`, `worker`, and `db` services.
Only the web service is published, on `127.0.0.1:18181`; host nginx exposes it at
`https://amigo.tolstik.ru/amigo/`.

## Local development

Install the backend from `backend/pyproject.toml` and run FastAPI at port 8000;
run `npm ci && npm run dev` in `frontend/`. Vite serves the `/amigo/` base and
proxies its API prefix to the backend.

The production image and Compose model can be validated without starting
credential-dependent services:

```bash
export AMIGO_IMAGE_TAG="$(git rev-parse HEAD)"
docker compose --env-file .env.example config --quiet
docker compose --env-file .env.example build web worker
```

Runtime credentials must be placed in untracked secret files. Never put real
values in `.env`, examples, Markdown, source code, test fixtures, or logs.
Production startup also requires the root-only `/srv/amigo/data/import` bind;
use the runbook instead of `docker compose up` from a development checkout.

## Production

Follow [docs/runbook.md](docs/runbook.md). The runbook is mandatory: it preserves
the legacy PHP application and shared cron jobs, creates a verified rollback
snapshot, and records the final deployed hashes and verification results.
