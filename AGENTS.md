# Amigo project instructions

## Safety and production

- The production dashboard URL is `https://amigo.tolstik.ru/amigo/`.
- Production runs on `192.168.31.3`; application files belong under `/srv/amigo`.
- `/srv/cron` is shared with unrelated systems. Never edit it recursively and never remove the shared `send_telergam.php all` job.
- The legacy application in `/srv/www/amigo` and the legacy MariaDB database `amigo` must remain intact until a documented rollback no longer requires them.
- Never commit or document passwords, OAuth credentials, Telegram tokens, chat IDs, cookies, or authorization headers.
- Before every production cutover, create and verify the backup described in `docs/runbook.md`.
- Repeat deployments must use `deploy/deploy.sh` with exactly one notification
  mode (`--send-telegram-test` or `--skip-telegram-test`); the script stops an
  existing data worker, AI worker, and signed-ingest service before migrations
  and one-shot synchronization and restarts them automatically after an early
  pre-cutover failure.
- Before re-enabling the legacy Withings cron, stop the Amigo worker and
  complete the secret-free OAuth token handback from PostgreSQL to the single
  legacy MariaDB token row.
- Production has exactly six Compose services: `web`, `worker`, `db`, `ingest`,
  `ai-worker`, and `ai-gateway`. Only `web` and `ingest` may be host-published,
  on `127.0.0.1:18181` and `127.0.0.1:18182`; `ai-gateway` must never have a
  host-published port.
- The Codex binary and its dedicated auth state are prepared only with
  `deploy/prepare-ai-runtime.sh`. Never copy, print, inspect, or document
  `auth.json`; use `--refresh-auth` after an interactive login by the service
  owner.
- The deployed application SHA is recorded in `/var/lib/amigo/current-release`; verification must use it rather than assuming the documentation checkout HEAD equals the running image.
- After every production deployment, update this file and the runbook checkpoint with the deployed Git SHA/image IDs, verification results, production URL, and latest rollback location before reporting completion.

## Product invariants

- Program start: 2026-08-15 in `Europe/Moscow`; baseline weight: 127.03 kg.
- Plan: lose 4 kg per calendar month, interpolated between matching month-days, capped at 76.5 kg.
- Pre-program weight data is visible in the all-history view but excluded from program KPIs and forecasts.
- Withings is the only source of weight, body composition, blood pressure, and
  pulse. Mi Fitness data arrives only through Health Connect and the signed
  Android companion; never import Health Connect weight, blood pressure,
  GPS/location, or exercise routes.
- Android pairing reset must rotate the non-exportable device key before local
  pairing state is cleared. Health batches stay below 1 MiB, contain at most
  2,000 records and 5,000 heart-rate samples per record, and start no faster
  than once every 1.1 seconds so resumable backfill remains below the origin
  rate limit.
- Blood pressure, heart, SpO2, and VO2 max metrics are descriptive statistics only: do not add
  diagnostic categories, severity colors, medical thresholds, treatment or
  medication advice, or recommendations derived from those metrics.
- KPI, trends, plans, forecasts, outliers, baselines, and correlations are
  deterministic. Visible observations and recommendations are generated only
  from a validated AI result; never add template or rule-based narrative
  fallback text.
- The AI boundary sends only minimized, identifier-free derived facts and
  bounded daily aggregate series to the pinned Codex CLI using the fixed
  `gpt-5.6-terra` model. AI runs asynchronously after data changes or before a
  scheduled digest. Public GET handlers only read the validated PostgreSQL
  cache and must never call Codex or enqueue analysis.
- Production must keep AI enabled and must use exactly
  `http://ai-gateway:8090`; never redirect minimized health snapshots to an
  override endpoint.
- The dashboard is intentionally public and read-only. Public Health Connect
  data is limited to daily/weekly aggregates; do not expose device identity,
  pairing state, signatures, nonces, raw provider payloads, or raw heart-rate
  samples. Configuration, pairing, and integration mutations remain server-side
  only.
- Daily Telegram reports run at `09:00 Europe/Moscow`; the Monday 09:00 weekly
  report replaces that day's daily report. Immediate Withings weight/pressure
  notifications remain enabled. When AI is unavailable, Telegram explicitly
  sends facts only.
- `web`, `ingest`, and `ai-worker` receive only the PostgreSQL secret. `worker`
  receives the eight integration/database secrets. `ai-gateway` receives no
  Docker secrets or database access; only its pinned binary and dedicated Codex
  auth state are mounted.

## Latest production checkpoint

<!-- BEGIN AMIGO PRODUCTION CHECKPOINT -->
- Status: **deployed and verified**
- Production URL: `https://amigo.tolstik.ru/amigo/`
- Verified at: `2026-08-19T17:49:14Z` (`2026-08-19 20:49:14 MSK`)
- Git SHA: `77a6699f9af2d564bbb252212ed32baeea00e746`
- Latest rollback snapshot: `/srv/amigo-rollbacks/20260819T174504Z`
- Installed config SHA-256: Compose `6727c935a9960a6a83c332c83b304d8814c26a328dd04d5a82ee63b2009847d7`; nginx locations `37ca8718449885e28e44fb04ded159913169d43b839024960c861063186d574e`; nginx rate limit `a887ddf70734dda6821fcd4db984d99dcb70993eff64a5ae4a2108e517a93362`.
- Verification: Compose `web`, `worker`, and `db` running; PostgreSQL ready; web bound to `127.0.0.1:18181`; direct health, hidden public health routes, origin proxy, public HTTPS dashboard/API, relative `308`, security headers, route rollback rehearsal, cron isolation, and rollback assets passed.
- Installed image references and IDs:

- `web`: `amigo:77a6699f9af2d564bbb252212ed32baeea00e746` (`sha256:8b53c6bff5b9db086df6de412296b0c972ac20d23c199028cf6ba52fc0271227`)
- `worker`: `amigo:77a6699f9af2d564bbb252212ed32baeea00e746` (`sha256:8b53c6bff5b9db086df6de412296b0c972ac20d23c199028cf6ba52fc0271227`)
- `db`: `postgres:17-alpine` (`sha256:1bea307dfb3ee30541a7acf7de14b58bcd6948da98e5d31a04c627c4d35ec64b`)
- Rollback command: `sudo /srv/amigo/deploy/rollback.sh /srv/amigo-rollbacks/20260819T174504Z`
<!-- END AMIGO PRODUCTION CHECKPOINT -->
