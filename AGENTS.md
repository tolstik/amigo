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
- User height is 176 cm. AI snapshots may include this identifier-free profile
  fact and a deterministic current BMI derived from the latest Withings weight.
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
  rate limit. Health Connect step counts up to its documented 1,000,000 maximum
  are accepted. Ingest rejection logs contain only the stable `detail.code`,
  never health payloads, headers, device IDs, batch IDs, or validation details.
- Deterministic blood-pressure, heart, SpO2, and VO2 displays remain descriptive
  and never add severity colors or app-side diagnoses. Validated AI may use those
  metrics only for evidence-bound measurement/logging advice or discussion of a
  persistent pattern with a clinician. It must never diagnose, prescribe
  treatment, set medication/dosage, or prescribe a fixed calorie target.
- KPI, trends, plans, forecasts, outliers, baselines, and correlations are
  deterministic. Visible observations and recommendations are generated only
  from a validated AI result; never add template or rule-based narrative
  fallback text.
- The AI boundary sends only minimized, identifier-free derived facts and
  bounded daily aggregate series to the pinned Codex CLI using the fixed
  `gpt-5.6-terra` model. AI runs asynchronously after data changes or before a
  scheduled digest. Public GET handlers only read the validated PostgreSQL
  cache and must never call Codex or enqueue analysis.
- AI prompt contract `amigo-health-v2` requires concrete actions, a cadence or
  review period, and cited metric evidence; recommendations are shown before
  general observations in Telegram and on the overview dashboard. When any
  pressure, heart, SpO2, or VO2 evidence exists, validated output must contain
  at least one bounded medical/measurement recommendation.
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
- Verified at: `2026-08-19T20:38:42Z` (`2026-08-19 23:38:42 MSK`)
- Git SHA: `15b73450679bdd4cb4023d3dd1b1df26a0564063`
- Latest rollback snapshot: `/srv/amigo-rollbacks/20260819T203256Z`
- Installed config SHA-256: Compose `25372a82af6c15051bb241dcc63e94fd39d5b972bc37f663b6d600a08d621be5`; nginx locations `a1b07803174a6143014c32da55c166422253b8fdd9db8df5ed3b3afb61de4728`; nginx rate limit `1a1a7d7b3124592c960e600c9fa6ccc7a80f4200ff71464a3e4a689b97bc86fb`.
- Pinned Codex: `0.148.0` (`sha256:ac2cfed85fb647d61e0150b8548102b330e4799d9d81ad5d354de701edf6b074`).
- Verification: all six Compose services healthy; application services use the release image; PostgreSQL ready; web and ingest bound only to `127.0.0.1:18181` and `127.0.0.1:18182`; container secret boundaries, pinned Codex hash, isolated unpublished AI gateway, direct health, hidden public health routes, exact unsigned-ingest rejection, origin proxy, public HTTPS dashboard and overview/activity/recovery/AI JSON, relative `308`, security headers, route rollback rehearsal, cron isolation, and rollback assets passed.
- Installed image references and IDs:

- `web`: `amigo:15b73450679bdd4cb4023d3dd1b1df26a0564063` (`sha256:c0d23ec8f4488969335f9f27afad12492315410fe6ef632560b473712b26584d`)
- `worker`: `amigo:15b73450679bdd4cb4023d3dd1b1df26a0564063` (`sha256:c0d23ec8f4488969335f9f27afad12492315410fe6ef632560b473712b26584d`)
- `ingest`: `amigo:15b73450679bdd4cb4023d3dd1b1df26a0564063` (`sha256:c0d23ec8f4488969335f9f27afad12492315410fe6ef632560b473712b26584d`)
- `ai-worker`: `amigo:15b73450679bdd4cb4023d3dd1b1df26a0564063` (`sha256:c0d23ec8f4488969335f9f27afad12492315410fe6ef632560b473712b26584d`)
- `ai-gateway`: `amigo:15b73450679bdd4cb4023d3dd1b1df26a0564063` (`sha256:c0d23ec8f4488969335f9f27afad12492315410fe6ef632560b473712b26584d`)
- `db`: `postgres:17-alpine` (`sha256:1bea307dfb3ee30541a7acf7de14b58bcd6948da98e5d31a04c627c4d35ec64b`)
- Rollback command: `sudo /srv/amigo/deploy/rollback.sh /srv/amigo-rollbacks/20260819T203256Z`
<!-- END AMIGO PRODUCTION CHECKPOINT -->
