# Amigo v3

Personal, authenticated health dashboard for weight-program progress, activity,
recovery, descriptive blood-pressure history, laboratory results, and a
context-aware assistant. Withings remains
the only source of weight, body composition, blood pressure, and the pulse
recorded during a blood-pressure session. Xiaomi Smart Band 9 Pro data,
including ordinary heart-rate samples, follows
`Mi Fitness -> Health Connect -> Amigo Sync`.

The weight program starts on 2026-08-15, while a separate view preserves the
complete earlier weight history. Amigo also sends immediate measurement
notifications and scheduled factual/AI-assisted reports to Telegram.

## Architecture

The production Docker Compose stack has seven services:

- `web` — authenticated FastAPI API, laboratory archive, and React dashboard;
- `worker` — Withings synchronization, outbox, Telegram, and scheduling;
- `db` — PostgreSQL 17;
- `ingest` — signed Health Connect registration, pairing status, and batch
  ingestion;
- `ai-worker` — PostgreSQL-backed asynchronous analysis queue;
- `ai-gateway` — isolated, single-concurrency Codex CLI process boundary;
- `lab-parser` — non-root, database-free PDF/image text extraction and OCR.

Only `web` and `ingest` are host-published, on loopback ports `18181` and
`18182`. Origin nginx exposes the dashboard at
`https://amigo.tolstik.ru/amigo/` and only the three exact signed-ingest
routes under `/amigo-ingest/v1/`. The AI gateway and laboratory parser have no
host ports. The dashboard, JSON APIs, CSV, laboratory originals, and assistant
require the single local account; Android signed ingest is independent.
Health Connect appears there only as daily/weekly aggregates, without device or
pairing metadata.
Its theme selector offers Light, Dark, Ocean, and Sunset. A fresh browser always
starts in Light regardless of the operating-system setting; an explicit choice
is persisted and applied to both the interface and charts.

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
fixed `gpt-5.6-sol` model. The gateway runs `codex exec` in an ephemeral,
read-only sandbox with a strict JSON schema and no database, Withings, Telegram,
or Docker secrets. Results are validated against the supplied evidence keys and
cached in PostgreSQL. Authenticated GET requests only read that cache and never invoke
Codex.

The private snapshot includes the configured height of 176 cm and a
deterministically calculated current BMI when a current Withings weight exists.
Prompt contract `amigo-health-v3` requires recommendations to name a concrete
action, a cadence or review period, and the exact supplied evidence. It may
suggest repeat measurements, a journal, sustainable food/activity/sleep steps,
or discussing a persistent pattern with a clinician. It cannot diagnose,
prescribe treatment, change medication or dosage, or set a fixed calorie target.
When pressure, heart, SpO2, or VO2 evidence is available, the result must include
at least one bounded measurement or medical recommendation.

There is no generated or rule-based narrative fallback. When a newer snapshot
is waiting or regeneration fails, the previous result may be marked stale for
at most 24 hours; after that the UI reports AI as unavailable and Telegram sends
facts only. Weight, activity, recovery, and pressure charts continue to work.
Withings overlap polling treats an identical provider group as unchanged, so
only a new or structurally changed measurement group requests another analysis.

See the official OpenAI documentation for
[`codex exec` and saved CLI authentication](https://learn.chatgpt.com/docs/non-interactive-mode)
and the current [`gpt-5.6-sol` model](https://developers.openai.com/api/docs/models/gpt-5.6-sol).

## Authentication, laboratory archive, and assistant

The application has one local Argon2id account. Opaque sessions live in
PostgreSQL for 90 days; only SHA-256 token digests are stored. Cookies are
`Secure` and `SameSite=Strict`; every mutation requires the exact production
Origin and CSRF token. The password is created or rotated only through the
root-only CLI and is never passed as a process argument.

Laboratory uploads accept PDF, JPG, PNG, and HEIC up to 20 MiB (PDF up to 50
pages). Originals use random keys in root-only `/srv/amigo/data/lab-files`.
`lab-parser` performs text extraction and OCR without database, secrets, Codex
state, a shared file mount, or a host port. Results initially appear as
`unverified`. Backend code—not AI—prefers a range from the document and computes
the status and history. It can use a versioned catalog only when a reviewed
catalog is explicitly enabled and matches analyte/specimen/unit/profile exactly.
The initial fallback catalog is disabled, so only report-provided or
user-entered intervals are evaluated. Original extraction and user edits are
audited.

Before the first upload or assistant question, the profile requires explicit
`amigo-ai-data-v1` consent. The disclosure states that Codex CLI runs locally,
but full extracted text and questions may be sent to OpenAI inference. The one
persistent chat combines deterministic health/laboratory history, the last 12
messages, an older local summary, and locally retrieved OCR chunks. Draft
segments stream through PostgreSQL/SSE; only a fully validated final answer is
retained as complete. The gateway uses ephemeral `codex app-server` turns with
a strict output schema; see the official
[`app-server` turns documentation](https://learn.chatgpt.com/docs/app-server#turns).

## Health Connect companion

Amigo Sync reads the history that Health Connect actually makes available for
steps, distance, calories, active minutes, workouts, sleep, heart/resting heart
rate, HRV, SpO2, and VO2 max. Availability varies by device and Mi Fitness.
The app never requests weight, blood pressure, location, or exercise routes.
The recovery dashboard, CSV export, Telegram digests, and minimized AI snapshot
use daily average/minimum/maximum watch heart rate. Resting heart rate remains a
separate metric and is shown only when Health Connect supplies its dedicated
record type.

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

The signed companion from release `v3.1.0` is
[`Amigo-Sync-1.0.2.apk`](https://github.com/tolstik/amigo/releases/download/v3.1.0/Amigo-Sync-1.0.2.apk)
(SHA-256 `ca5612ad7a642bde582478b5eebf8edc7d83a87337cf5df71d522026cecc94fd`).
Verify the checksum before installing it.

## Telegram schedule

- Weight and blood-pressure measurements keep their immediate notifications.
- A daily report is scheduled for `09:00 Europe/Moscow` except Monday.
- The Monday 09:00 weekly report replaces the daily report and sends the chart
  plus a separate full text message.
- Scheduled AI preparation begins at 08:45. If no validated AI result is ready,
  the report explicitly contains facts only.
- New laboratory values, units, ranges, status, and verification mark are added
  to the next scheduled digest without truncation. Originals, filenames, OCR
  text, and assistant messages are never sent to Telegram.

Blood pressure, heart, SpO2, and VO2 max charts remain descriptive and have no
severity colors or app-side diagnosis. Validated AI can turn a repeated pattern
into measurement/logging or clinician-discussion guidance, but never diagnosis,
treatment, medication changes, or fixed calorie prescriptions.

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
all seven services, authentication, laboratory/AI isolation boundaries, and
authenticated HTTPS/API/upload/SSE contracts. Before cutover its non-personal
synthetic smoke exercises live analysis, laboratory-extraction, and assistant-turn
Codex contracts, then the release records deployed hashes after cutover.

After the first interactive production cutover, repeat releases use the
root-owned `/usr/local/sbin/amigo-release GIT_SHA MODE` wrapper. The sudoers
policy grants `tolstik` passwordless access only to that validated wrapper; it
does not grant passwordless shell, Git, Docker, direct deploy-script, or global
root access. See the runbook for the exact command and validation contract.
