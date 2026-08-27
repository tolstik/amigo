# Amigo v5

Personal, authenticated health dashboard for weight-program progress, activity,
recovery, descriptive blood-pressure history, laboratory results, study reports,
and a context-aware assistant. Withings remains
the only source of weight, body composition, blood pressure, and the pulse
recorded during a blood-pressure session. Xiaomi Smart Band 9 Pro data is read
directly from Xiaomi Health Cloud by the Android companion; Health Connect
remains an independently signed rollback history.

The weight program starts on 2026-08-15, while a separate view preserves the
complete earlier weight history. Amigo also sends immediate measurement
notifications and scheduled factual/AI-assisted reports to Telegram.

## Architecture

The production Docker Compose stack has seven services:

- `web` — authenticated FastAPI API, laboratory/study archive, update endpoint,
  and React dashboard;
- `worker` — Withings synchronization, outbox, Telegram, and scheduling;
- `db` — PostgreSQL 17;
- `ingest` — signed Android registration, pairing status, Health Connect
  rollback-history ingestion, and Xiaomi Cloud snapshot/status ingestion;
- `ai-worker` — PostgreSQL-backed asynchronous analysis queue;
- `ai-gateway` — isolated, single-concurrency Codex CLI process boundary;
- `lab-parser` — non-root, database-free PDF/image text extraction and OCR.

Only `web` and `ingest` are host-published, on loopback ports `18181` and
`18182`. Origin nginx exposes the dashboard at
`https://amigo.tolstik.ru/amigo/` and only the bounded registration/status,
Health Connect, and Xiaomi signed-ingest routes under `/amigo-ingest/v1/`. The
AI gateway and laboratory parser have no
host ports. The dashboard, JSON APIs, CSV, uploaded originals, APK update, and
assistant require the single local account; Android signed ingest is independent.
Health Connect appears there only as daily/weekly aggregates, without device or
pairing metadata. Published steps are stricter: only active, finalized Xiaomi
Cloud snapshots may reach the dashboard, CSV, Telegram, AI, correlations, or a
doctor PDF. Health Connect step rows remain in PostgreSQL solely as rollback
history. Sleep stays in minutes in storage and API/CSV/AI contracts, while
dashboard and PDF charts display the scale and tooltips in hours.
Its theme selector offers Light, Dark, Ocean, and Sunset. A fresh browser always
starts in Light regardless of the operating-system setting; an explicit choice
is persisted and applied to both the interface and charts. An explicit 30-day,
90-day, one-year, or all-time chart period is shared across chart pages and
survives reloads.

The watch heart-rate chart automatically groups hourly aggregates into a
readable `1h`, `3h`, `6h`, or daily view, preserves true minimum/maximum bounds,
uses a sample-count-weighted average, leaves visible gaps for missing data, and
offers a slider plus wheel/pinch zoom. Correlation cards explain Pearson `r`,
its direction and strength, and why it does not establish causality.

`backend/` contains FastAPI services, deterministic analytics, integrations,
and migrations. `frontend/` contains the React/TypeScript dashboard. `android/`
contains the hybrid Amigo Android app: its default tab is the secured dashboard
and its second tab is the native Health Connect companion. `deploy/` and
`docs/runbook.md` contain the production, verification, and rollback procedures.
Confirmed deferred defects and their correction criteria are tracked in
[`docs/technical-debt.md`](docs/technical-debt.md).

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
cached in PostgreSQL. Every displayed citation is resolved from that completed
run's immutable saved snapshot, so its value, date, and range cannot drift after
a later import or user correction; current PostgreSQL state is consulted only
to mark a deep-link target available or unavailable. Authenticated GET requests
only read that cache and never invoke Codex.

The private snapshot includes the configured height of 176 cm and a
deterministically calculated current BMI when a current Withings weight exists.
Prompt contract `amigo-health-v4` requires recommendations to name a concrete
action, a cadence or review period, and the exact supplied evidence. It may
suggest repeat measurements, a journal, sustainable food/activity/sleep steps,
or discussing a persistent pattern with a clinician. It cannot diagnose,
prescribe treatment, change medication or dosage, or set a fixed calorie target.
When pressure, heart, SpO2, or VO2 evidence is available, the result must include
at least one bounded measurement or medical recommendation. Laboratory results
must receive a cited assessment, and an out-of-reference result also requires a
bounded verification, repeat-test, or clinician-discussion step.

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

Laboratory uploads accept up to 25 PDF, JPG, PNG, and HEIC files per selection;
each file may be up to 20 MiB and each PDF up to 50 pages. PostgreSQL
`stored_files` owns every original. Laboratory originals are temporarily
dual-written under random keys in root-only `/srv/amigo/data/lab-files` so the
immediately previous release remains recoverable.
`lab-parser` performs text extraction and OCR without database, secrets, Codex
state, a shared file mount, or a host port. Results initially appear as
`unverified`. Backend code—not AI—prefers a range from the document and computes
the status and history. It can use a versioned catalog only when a reviewed
catalog is explicitly enabled and matches analyte/specimen/unit/profile exactly.
The initial fallback catalog is disabled, so only report-provided or
user-entered intervals are evaluated. Original extraction and user edits are
audited. Explicitly labelled OCR measurement dates override a conflicting or
implausible model date; bootstrap repairs the same unambiguous error pattern in
existing uncorrected rows. Each analyte history page includes an Amigo guide
describing the marker, why it is tested, and possible categories associated
with values below or above the report reference. Known guides are versioned
reference content. When extraction finds an unknown analyte, the isolated local
Codex gateway generates a general guide that is stored in PostgreSQL before the
document completes; a bounded batched queue backfills existing unknown markers,
and GET requests never trigger inference. The UI shows
sequential queue position/stage/progress over SSE, keeps
accepting new bounded batches while older files are processed, opens the
database original, searches/filters results, and charts incompatible units
separately.

The “Studies” section accepts the same report formats for ultrasound, MRI, CT,
X-ray, ECG, and other reports; DICOM is intentionally out of scope. It stores the
original, extracted findings, and conclusion in PostgreSQL and provides the same
queue, view, edit, confirmation, retry, and delete flow. Obvious identifier
header lines are removed before structured study facts can enter AI context.

Laboratory comparison accepts exactly two or three completed panels and matches
rows only by the persisted canonical `analyte_id`. Numeric deltas are shown only
for one value per panel with identical unit, specimen, and method; there is no
fuzzy name matching or implicit unit conversion.

Before the first laboratory upload or assistant question, the profile requires explicit
`amigo-ai-data-v1` consent. The disclosure states that Codex CLI runs locally,
but full extracted text and questions may be sent to OpenAI inference. The one
persistent `amigo-health-chat-v2` chat combines the complete structured
health/laboratory/study history, the last 12 messages, and an older local
summary. Assistant context never includes originals, filenames, study titles,
or OCR pages. It may explain evidence-backed hypotheses and alternatives while
remaining unable to assert a definitive diagnosis, prescribe treatment,
medication/dosage changes, or a fixed calorie target. Draft
segments stream through PostgreSQL/SSE; only a fully validated final answer is
retained as complete. The gateway uses ephemeral `codex app-server` turns with
a strict output schema; see the official
[`app-server` turns documentation](https://learn.chatgpt.com/docs/app-server#turns).

## Data quality, tasks, and doctor reports

The authenticated data-quality center summarizes the last 30 or 90 completed
days by source and metric. It distinguishes an available value, a finalized
provider-confirmed empty interval, and a genuinely missing day without exposing
device/account identity or provider payloads. Its steps policy is always
`xiaomi_finalized_only`; Health Connect contributes no published step coverage.

Health tasks support one-time, daily, weekly, and calendar-month recurrence.
They can be created manually or from a saved AI recommendation; the source
recommendation text and cited evidence IDs are copied into the task so later AI
runs cannot rewrite its meaning. The worker creates a deduplicated Telegram
reminder for each due occurrence. Telegram receives only the task title, due
time, and authenticated dashboard link, never its note or health evidence.

The “Doctor package” creates an immutable, authenticated report snapshot for
`30d`, `90d`, or `1y`. It contains only selected deterministic aggregates,
confirmed/corrected laboratory results, verified study findings/conclusions,
and optionally validated AI recommendations with evidence IDs. Filenames,
originals, OCR, chat, device/account identity, and raw provider data are
excluded. The locally rendered PDF is limited to 40 pages and 10 MiB; snapshot
and download expire after 24 hours and can be deleted earlier. Its step chart is
explicitly Xiaomi Cloud-only and its sleep chart is labelled in hours.

## Android app and Xiaomi/Health Connect companion

Amigo `1.4.1` (`versionCode 16`, package `ru.tolstik.amigo.sync`) opens the full
authenticated dashboard in a top-level WebView. It uses the same local account
and 90-day server session as a browser, while signed ingest remains independent.
Only the fixed production origin and known SPA routes are accepted; there is no
JavaScript bridge, third-party cookie access, mixed content, TLS bypass, or
medical offline cache. Verified App Links cover `/amigo` and `/amigo/...`.
Laboratory and study uploads use the system file picker with up to 25 selected
files, and authenticated CSV/original downloads use system “Save as” with an
exact same-origin allowlist and no redirects. Returning to a WebView that has
been backgrounded for 30 seconds refreshes it so current server data is shown.
The same download boundary accepts only an exact authenticated `GET` for
`/amigo/api/v1/reports/doctor/<canonical-lowercase-UUID>.pdf`; query strings,
fragments, redirects, malformed IDs, other methods, and other origins are
rejected. The doctor PDF has a separate 25 MiB client-side download ceiling.

The native synchronization tab reads steps, distance, active calories, workout
summaries, sleep, heart/resting heart rate, HRV, SpO2, and VO2 max directly from
Xiaomi Health Cloud. Xiaomi login is confined to an exact-host HTTPS WebView in
a separate process; the password remains on Xiaomi's page, and the resulting
session is stored only as Android-Keystore AES-GCM ciphertext. The WebView
accepts keyboard input and retains the in-progress Xiaomi form while the user
switches to email for a verification code. Credentials,
cookies, account identifiers, provider payloads, weight, blood pressure,
location, and exercise routes never reach the Amigo server. Health Connect can
remain enabled for the same allowlisted types as rollback history.
The recovery dashboard, CSV export, Telegram digests, and minimized AI snapshot
use daily average/minimum/maximum watch heart rate. The phone reduces raw cloud
heart-rate points to hourly min/average/max/count before upload; the server never
receives or persists those samples. Resting heart rate remains a distinct
provider metric.

Each Xiaomi Cloud run sends its signed source-status preflight before any cloud
fetch or batch upload. If the first status report was interrupted, the next
manual or background run repairs server-side enablement before sending data
instead of surfacing a misleading `invalid_cloud_response`.

Cloud batch retries use a range-stable timestamp and an ID bound to the full
canonical normalized body. The phone keeps only bounded hashes of already
acknowledged record IDs while a snapshot is unfinished, so Xiaomi's overlapping
pagination boundary cannot repeat a record on the next page. An old partial
snapshot that reports a batch or page-sequence conflict is restarted once for
that metric and range only; the encrypted Xiaomi session, pairing, completed
history, and every unrelated cursor remain intact.

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
WorkManager starts an immediate best-effort run, continues incomplete backfill
after one minute, and keeps the hourly job; the native screen shows bounded
background-run diagnostics. The in-app updater downloads only the authenticated
same-origin APK, verifies its declared size and SHA-256 plus package, higher
version code, and installed signing certificate, then delegates to the Android
system installer for explicit confirmation.

Release 1.4.1 retains the `1.4.0` and `1.2.4` Health Connect behavior:
modification timestamp as the batch freshness watermark, so Mi Fitness
intervals whose rounded end is still ahead do not stall synchronization. A
rejected record type no longer prevents later types such as sleep from being
attempted in the same bounded run. It performs one heart-rate-only full
reconciliation with a fresh changes token after the upgrade, while preserving
pairing, the device key, the selected origin, and all other type cursors. The
release keeps the earlier worker/run-ID, DNS, and empty-history fixes. Direct
cloud activation fixes its three-day window from the first qualifying recent
coverage in the current enablement episode, so bounded pagination cannot make
the gate recede while pages upload. It requires finalized coverage for all ten
mapped types from that episode and heart rate newer than the retained Health
Connect watermark. Partial snapshots are invisible; a final cloud snapshot,
including a confirmed-empty one, atomically takes precedence only for the same
metric and interval.

The 1.4.1 Xiaomi planner keeps routine three-day and weekly 30-day
reconciliation in a dedicated resumable recent lane while the historical
30-day backfill descends independently to `2000-01-01`. One persisted target
and width apply to all ten metrics in a recent round and its one-minute
continuations. A recent page therefore cannot overwrite or advance the exact
historical cursor, and current steps no longer wait for the full historical
backfill. Auth expiry stays visible and never silently switches the active
source.

Build, install, and phone setup are documented in
[android/README.md](android/README.md); production pairing and verification are
documented in [docs/runbook.md](docs/runbook.md).

The signed current companion is
[`Amigo-1.4.1.apk`](https://github.com/tolstik/amigo/releases/download/v5.2.2/Amigo-1.4.1.apk)
from release [`v5.2.2`](https://github.com/tolstik/amigo/releases/tag/v5.2.2).
Its SHA-256 is
`fd5a13cf89440a80d8ee44444607077bce9f5466f3653372c26cd153add965e5`; its
size is `3,520,750` bytes and its signing-certificate SHA-256 is
`25cc38ecb31081f6826ff049b807335a05e86ee9895470975e8521af95191c02`.
The previous signed companion is
[`Amigo-1.4.0.apk`](https://github.com/tolstik/amigo/releases/download/v5.2.1/Amigo-1.4.0.apk)
from release [`v5.2.1`](https://github.com/tolstik/amigo/releases/tag/v5.2.1),
SHA-256
`4a3a083c2b5c54482d2393526c0e6775087df53a0d3f6d6f9f568e80db32f995`.
Verify the checksum before installing an APK.

## Telegram schedule

- Weight and blood-pressure measurements keep their immediate notifications.
- A daily report is scheduled for `09:00 Europe/Moscow` except Monday.
- The Monday 09:00 weekly report replaces the daily report and sends the chart
  plus a separate full text message.
- Scheduled AI preparation begins at 08:45. If no validated AI result is ready,
  the report explicitly contains facts only.
- New laboratory values, units, ranges, status, and verification mark are added
  to the next scheduled digest without truncation. A validated cited AI
  assessment follows recent laboratory facts; supplied deviations include a
  bounded next step. Originals, filenames, OCR text, and assistant messages are
  never sent to Telegram.

Heart, SpO2, and VO2 max charts remain descriptive and have no severity colors
or app-side diagnosis. The blood-pressure dashboard has one bounded visual
exception for home measurements: neutral below `90/60`, green within
`90–134/60–84`, amber when systolic is `135–179` or diastolic is `85–119`, and
red when systolic is at least `180` or diastolic is at least `120`. The daily
view uses the category requiring the most attention among that day's sessions
(home guide, below guide, elevated, critically high), discloses the exact
thresholds, and remains a visual reference rather than a diagnosis. The main
trend labels the `135/85` elevated and `180/120` critical boundaries. The red
state asks the user to repeat the measurement and seek urgent help when symptoms
are present. Validated AI can turn a repeated pattern into
measurement/logging or clinician-discussion guidance, but never diagnosis,
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

The 28-day and 42-day summary changes use the actual difference between the
first and last non-outlier daily medians inside the selected calendar window.
When a qualifying span is shorter than the full window, Amigo reports only the
observed change and never scales it into a hypothetical full-period result.

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
all seven services, authentication, database-owned originals, the signed update
artifact, laboratory/AI isolation boundaries, and authenticated
HTTPS/API/upload/SSE contracts. GitHub Actions builds and publishes the tested
immutable `ghcr.io/tolstik/amigo:GIT_SHA` image; the weak production server only
pulls and verifies it. Before cutover the deploy's non-personal
synthetic smoke exercises live analysis, laboratory-extraction, analyte-guide,
and assistant-turn Codex contracts, then the release records deployed hashes
after cutover.

After the first interactive production cutover, repeat releases use the
root-owned `/usr/local/sbin/amigo-release GIT_SHA MODE` wrapper. The sudoers
policy grants `tolstik` passwordless access only to that validated wrapper; it
does not grant passwordless shell, Git, Docker, direct deploy-script, or global
root access. See the runbook for the exact command and validation contract.
