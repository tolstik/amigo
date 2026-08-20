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
  and one-shot synchronization. After the verified snapshot exists, every
  failure restores the exact previous application and PostgreSQL images on the
  preserved PostgreSQL volume; it never restores the PostgreSQL dump.
- Automatic recovery must call `restore-previous-release.sh` and must never
  activate legacy PHP. Legacy is an explicit disaster fallback requiring
  `rollback.sh --to-legacy SNAPSHOT`. From an already active legacy route/cron,
  first use `takeover-from-legacy.sh --resume-recorded-release SNAPSHOT` so the
  live MariaDB OAuth pair is handed safely back to PostgreSQL.
- A responding but unhealthy legacy origin may be bypassed only with takeover's
  explicit `--allow-unhealthy-legacy-origin` flag. In that mode failure reversal
  must never treat legacy as healthy, enable its Withings cron, or stop a
  route-serving Amigo runtime.
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
- Production verification must wait for a `withings-incremental` `JobRun` that
  started at or after the current worker container's `StartedAt` and finished
  with status `success`. The verification query may read only job name, status,
  and timestamps; never read or print `details`, provider payloads, or secrets.
- After every production deployment, update this file and the runbook checkpoint with the deployed Git SHA/image IDs, verification results, production URL, and latest rollback location before reporting completion.

## Product invariants

- Program start: 2026-08-15 in `Europe/Moscow`; baseline weight: 127.03 kg.
- User height is 176 cm. AI snapshots may include this identifier-free profile
  fact and a deterministic current BMI derived from the latest Withings weight.
- Plan: lose 4 kg per calendar month, interpolated between matching month-days, capped at 76.5 kg.
- Pre-program weight data is visible in the all-history view but excluded from program KPIs and forecasts.
- Withings is the only source of weight, body composition, blood pressure, and
  pulse recorded during a blood-pressure session. Mi Fitness data, including
  ordinary watch heart rate, arrives only through Health Connect and the signed
  Android companion; never import Health Connect weight, blood pressure,
  GPS/location, or exercise routes. Dashboard, CSV, Telegram, and minimized AI
  snapshots use daily average/minimum/maximum watch heart rate; resting heart
  rate remains a distinct metric and must never be inferred from ordinary
  samples.
- Identical Withings groups replayed by the overlap window are not updates and
  must not enqueue another AI analysis. Only newly created or structurally
  changed provider groups may trigger measurement-driven regeneration.
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
  `gpt-5.6-sol` model. AI runs asynchronously after data changes or before a
  scheduled digest. Public GET handlers only read the validated PostgreSQL
  cache and must never call Codex or enqueue analysis.
- Historical AI rows remain in PostgreSQL, but workers and public cache reads
  consider only the active model and prompt contract. The explicit deployment
  enqueue may retry a failed/superseded same-key active job; background enqueue
  must not revive terminal history.
- Deployment AI readiness permits only `ai-ready` exits `0` and `75`, prepares
  one explicit retry while the persistent worker is stopped, and runs at most
  four foreground queue attempts. It must never add an unbounded gateway or AI
  retry loop.
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
- The dashboard offers Light, Dark, Ocean, and Sunset themes. With no stored
  choice it must always start in Light regardless of the operating-system color
  scheme; an explicit selection persists and recolors both UI and charts.
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
- Verified at: `2026-08-20T07:57:43Z` (`2026-08-20 10:57:43 MSK`)
- Git SHA: `c997d450d5f4281fe2a4b04ea85abad364fc1256`
- Latest rollback snapshot: `/srv/amigo-rollbacks/20260820T075052Z`
- Installed config SHA-256: Compose `05d80cfec15d859ad686069652c0b4a33d0f210fdfe6bd4502cce5a111558c49`; nginx locations `a1b07803174a6143014c32da55c166422253b8fdd9db8df5ed3b3afb61de4728`; nginx rate limit `1a1a7d7b3124592c960e600c9fa6ccc7a80f4200ff71464a3e4a689b97bc86fb`.
- Pinned Codex: `0.148.0` (`sha256:ac2cfed85fb647d61e0150b8548102b330e4799d9d81ad5d354de701edf6b074`).
- Verification: all six Compose services healthy; application services use the release image; PostgreSQL ready; the current worker completed a successful post-start Withings incremental job; web and ingest are bound only to `127.0.0.1:18181` and `127.0.0.1:18182`; container secret boundaries, pinned Codex hash, isolated unpublished AI gateway, fixed `gpt-5.6-sol` gateway health and public AI payload, direct health, hidden public health routes, exact unsigned-ingest rejection, origin proxy, public HTTPS dashboard and overview/activity/recovery/AI JSON, hashed JavaScript/CSS cache policy, relative `308`, security headers, cron isolation, previous-release recovery assets, and the explicit legacy disaster-fallback guard passed.
- Installed image references and IDs:

- `web`: `amigo:c997d450d5f4281fe2a4b04ea85abad364fc1256` (`sha256:90ab52fce41cae605968d749a7f4cf12fef9f32a08c864ffc6c8fe551699cc18`)
- `worker`: `amigo:c997d450d5f4281fe2a4b04ea85abad364fc1256` (`sha256:90ab52fce41cae605968d749a7f4cf12fef9f32a08c864ffc6c8fe551699cc18`)
- `ingest`: `amigo:c997d450d5f4281fe2a4b04ea85abad364fc1256` (`sha256:90ab52fce41cae605968d749a7f4cf12fef9f32a08c864ffc6c8fe551699cc18`)
- `ai-worker`: `amigo:c997d450d5f4281fe2a4b04ea85abad364fc1256` (`sha256:90ab52fce41cae605968d749a7f4cf12fef9f32a08c864ffc6c8fe551699cc18`)
- `ai-gateway`: `amigo:c997d450d5f4281fe2a4b04ea85abad364fc1256` (`sha256:90ab52fce41cae605968d749a7f4cf12fef9f32a08c864ffc6c8fe551699cc18`)
- `db`: `postgres:17-alpine` (`sha256:1bea307dfb3ee30541a7acf7de14b58bcd6948da98e5d31a04c627c4d35ec64b`)
- Previous-release recovery command: `sudo /srv/amigo/deploy/restore-previous-release.sh /srv/amigo-rollbacks/20260820T075052Z`
- Legacy disaster fallback command: `sudo /srv/amigo/deploy/rollback.sh --to-legacy /srv/amigo-rollbacks/20260820T075052Z`
<!-- END AMIGO PRODUCTION CHECKPOINT -->
