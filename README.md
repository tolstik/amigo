# Amigo v3

Personal, public read-only health dashboard for weight-program progress,
activity, recovery, and descriptive blood-pressure history. Withings remains
the only source of weight, body composition, blood pressure, and pulse. Xiaomi
Smart Band 9 Pro data follows `Mi Fitness -> Health Connect -> Amigo Sync`.

The weight program starts on 2026-08-15, while a separate view preserves the
complete earlier weight history. Amigo also sends immediate measurement
notifications and scheduled factual/AI-assisted reports to Telegram.

## Architecture

The production Docker Compose stack has six services:

- `web` — FastAPI public read-only API and the built React dashboard;
- `worker` — Withings synchronization, outbox, Telegram, and scheduling;
- `db` — PostgreSQL 17;
- `ingest` — signed Health Connect registration, pairing status, and batch
  ingestion;
- `ai-worker` — PostgreSQL-backed asynchronous analysis queue;
- `ai-gateway` — isolated, single-concurrency Codex CLI process boundary.

Only `web` and `ingest` are host-published, on loopback ports `18181` and
`18182`. Origin nginx exposes the dashboard at
`https://amigo.tolstik.ru/amigo/` and only the three exact signed-ingest
routes under `/amigo-ingest/v1/`. The AI gateway has no host port.
The dashboard is intentionally public and read-only; Health Connect is exposed
there only as daily/weekly aggregates, without device or pairing metadata.

`backend/` contains FastAPI services, deterministic analytics, integrations,
and migrations. `frontend/` contains the React/TypeScript dashboard. `android/`
contains the Health Connect companion. `deploy/` and `docs/runbook.md` contain
the production, verification, and rollback procedures.

## AI analysis boundary

All calculations remain deterministic: plan/fact, KPI, trends, forecasts,
outliers, personal baselines, and correlations are computed locally. The
application then builds a minimized, identifier-free snapshot of derived facts
and bounded daily series. It does not send raw provider payloads, device IDs,
GPS/location, or credentials to the model.

Analysis is generated asynchronously with a SHA-256-pinned Codex CLI and the
fixed `gpt-5.6-terra` model. The gateway runs `codex exec` in an ephemeral,
read-only sandbox with a strict JSON schema and no database, Withings, Telegram,
or Docker secrets. Results are validated against the supplied evidence keys and
cached in PostgreSQL. Public GET requests only read that cache and never invoke
Codex.

There is no generated or rule-based narrative fallback. When a newer snapshot
is waiting or regeneration fails, the previous result may be marked stale for
at most 24 hours; after that the UI reports AI as unavailable and Telegram sends
facts only. Weight, activity, recovery, and pressure charts continue to work.

See the official OpenAI documentation for
[`codex exec` and saved CLI authentication](https://learn.chatgpt.com/docs/non-interactive-mode)
and the current [Codex model catalog](https://learn.chatgpt.com/docs/models).

## Health Connect companion

Amigo Sync reads the history that Health Connect actually makes available for
steps, distance, calories, active minutes, workouts, sleep, heart/resting heart
rate, HRV, SpO2, and VO2 max. Availability varies by device and Mi Fitness.
The app never requests weight, blood pressure, location, or exercise routes.

Each installation creates a non-exportable P-256 key in Android Keystore.
Registration requires explicit server-side pairing approval; every batch is
ECDSA/SHA-256-signed, timestamped, nonce-protected, size-limited, replay-safe,
and idempotent. The backend stores normalized allowlisted records rather than
the provider payload or raw heart-rate samples. Initial full-history backfill
is resumable. Batches are deterministic, remain strictly below 1 MiB, contain
at most 2,000 records and 5,000 heart-rate samples per record, and are started
at most once every 1.1 seconds to stay below the production ingest limit. A
pairing reset rotates the Android Keystore identity before clearing local sync
state, so the replacement registration always has a new public-key fingerprint.

Build, install, and phone setup are documented in
[android/README.md](android/README.md); production pairing and verification are
documented in [docs/runbook.md](docs/runbook.md).

The signed companion from release `v3.0.0` is
[`Amigo-Sync-1.0.0.apk`](https://github.com/tolstik/amigo/releases/download/v3.0.0/Amigo-Sync-1.0.0.apk)
(SHA-256 `c8ba2c76698e99411938a51ce6026da840965ee7e68a3f7533d0f40bce3e2794`).
Verify the checksum before installing it.

## Telegram schedule

- Weight and blood-pressure measurements keep their immediate notifications.
- A daily report is scheduled for `09:00 Europe/Moscow` except Monday.
- The Monday 09:00 weekly report replaces the daily report and sends the chart
  plus a separate full text message.
- Scheduled AI preparation begins at 08:45. If no validated AI result is ready,
  the report explicitly contains facts only.

Blood pressure, heart, SpO2, and VO2 max metrics are descriptive observations only. They are
never classified by severity and are not used for diagnoses, treatment,
medication, or recommendations.

## Weekly plan/fact analytics

The program progress view includes two weekly charts backed by the `weekly`
array in `GET /api/v1/series/weight?range=program`:

- **Weight by week** — paired actual/plan average bars plus the actual weekly
  minimum line.
- **Change by week** — paired actual/plan change bars. A negative change means
  weight loss; a positive change means weight gain.

Buckets follow ISO weeks (Monday through Sunday) in `Europe/Moscow`. The first
bucket is clipped to 2026-08-15. The current bucket remains partial until the
next ISO week. Multiple readings on one local day become a daily median;
outliers are excluded from fact averages but reported separately. Empty weeks
remain present with null fact values, while the calendar plan remains
continuous.

Activity has a separate weekly fact-versus-personal-baseline chart. Its baseline
uses corresponding weekdays from the previous 28 complete days.

## Local development

Install the backend from `backend/pyproject.toml` and run FastAPI on port 8000;
run `npm ci && npm run dev` in `frontend/`. Vite serves the `/amigo/` base and
proxies its API prefix to the backend. Android requirements and Gradle commands
are in `android/README.md`.

Validate the shared production image and Compose model without starting
credential-dependent services:

```bash
export AMIGO_IMAGE_TAG="$(git rev-parse HEAD)"
docker compose --env-file .env.example config --quiet
docker compose --env-file .env.example build web
```

Runtime credentials and Codex authentication state belong only in ignored,
permission-restricted runtime paths. Never put their values in `.env`, examples,
Markdown, source code, fixtures, command output, or logs.

## Production

Follow [docs/runbook.md](docs/runbook.md). It preserves the legacy PHP
application and shared cron jobs, creates a verified rollback snapshot, checks
all six services and isolation boundaries, and records deployed hashes after
cutover.
