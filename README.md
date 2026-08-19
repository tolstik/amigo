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

## Weekly plan/fact analytics

The program progress view includes two weekly charts backed by the `weekly`
array in `GET /api/v1/series/weight?range=program`:

- **Weight by week** — paired actual/plan average bars plus the actual weekly
  minimum line.
- **Change by week** — paired actual/plan change bars. A negative change means
  weight loss; a positive change means weight gain.

Buckets follow ISO weeks (Monday through Sunday) in `Europe/Moscow`. The first
bucket is clipped to the program start on 2026-08-15. The current bucket ends at
the local `as_of` date and remains `is_partial` until the next ISO week begins;
the clipped first bucket is partial as well. Multiple readings on one local day
are reduced to a daily median.
Weekly actual average and minimum values use the available non-outlier daily
medians without interpolation. Measurement-day and raw-sample counts still
describe all observed data, while `outlier_days` is reported separately.

The weekly plan average uses the planned weight for every elapsed calendar day
in that bucket, including days without measurements. Every calendar bucket from
program start through `as_of` is preserved: a week without usable measurements
has null actual values instead of disappearing. Changes compare a bucket with
the immediately preceding calendar bucket, so actual change stays null after an
empty week while planned change continues. The accessible weekly table exposes
the same bucket bounds, values, deviation, counts, and partial-week marker as
the charts.

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
