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
  existing data worker, AI worker, signed-ingest service, and laboratory parser before migrations
  and one-shot synchronization. After the verified snapshot exists, every
  failure restores the exact previous application and PostgreSQL images on the
  preserved PostgreSQL volume; it never restores the PostgreSQL dump.
- Unattended release preparation uses only the root-owned
  `/usr/local/sbin/amigo-release GIT_SHA MODE` wrapper. Its sudoers rule grants
  `tolstik` no-password access to that wrapper only, never `NOPASSWD: ALL`; the
  wrapper accepts exactly the current `origin/main` commit, requires a clean
  root-owned checkout and a descendant of the recorded production release, and
  then invokes `deploy/deploy.sh` with exactly one notification mode.
- Production application images are built and tested by GitHub Actions, then
  published as immutable `ghcr.io/tolstik/amigo:GIT_SHA` images. The weak
  production host must pull and verify that exact revision label and must never
  build the application image during deployment. The signed Android asset must
  already exist at the pinned release URL and match the pinned SHA-256 before a
  cutover snapshot is accepted.
- Default automatic recovery must call `restore-previous-release.sh` and must never
  activate legacy PHP. Legacy is an explicit disaster fallback requiring
  `rollback.sh --to-legacy SNAPSHOT`. From an already active legacy route/cron,
  first use `takeover-from-legacy.sh --resume-recorded-release SNAPSHOT` so the
  live MariaDB OAuth pair is handed safely back to PostgreSQL.
- The optional `--no-auto-recovery` wrapper flag may be used only after explicit
  user authorization for the current operator session. It still requires a
  verified snapshot and records only a fully started candidate runtime so the
  next descendant release can fix forward; normal future deployments omit it.
- A previous release without `backend/app/auth.py` is never allowed to make the
  dashboard public again: recovery installs the auth-floor maintenance route,
  returns `503` for `/amigo/`, and keeps only signed Android ingest available.
  A retry from that state may snapshot only installed locations and HTTP files
  that exactly match the candidate's versioned maintenance snippet and HTTP
  configuration.
- Managed regex routes for laboratory documents/results and assistant turns use
  named captures with explicit upstream URIs; never reintroduce the generic
  `rewrite ^/amigo/(.*)$` form because nginx can clobber its numeric capture.
- Assistant SSE sends `X-Accel-Buffering: no` through the origin nginx. The
  public nginx edge consumes that control header, so production verification
  checks it at the origin boundary and checks content type, cache policy, and
  the bounded event body over public HTTPS.
- Every managed nginx rate limit returns explicit `429`; never rely on nginx's
  default `503`, because the shared origin error handler can remap it.
- A responding but unhealthy legacy origin may be bypassed only with takeover's
  explicit `--allow-unhealthy-legacy-origin` flag. In that mode failure reversal
  must never treat legacy as healthy, enable its Withings cron, or stop a
  route-serving Amigo runtime.
- Before re-enabling the legacy Withings cron, stop the Amigo worker and
  complete the secret-free OAuth token handback from PostgreSQL to the single
  legacy MariaDB token row.
- Production has exactly seven Compose services: `web`, `worker`, `db`, `ingest`,
  `ai-worker`, `ai-gateway`, and `lab-parser`. Only `web` and `ingest` may be
  host-published, on `127.0.0.1:18181` and `127.0.0.1:18182`; `ai-gateway` and
  `lab-parser` must never have a host-published port.
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
- `deploy/checkpoint.sh` must leave the root-owned production checkout clean by
  creating a local documentation-only commit and durable
  `refs/amigo/checkpoints/GIT_SHA` ref. Copy the checkpoint facts into the
  canonical repository in the same work cycle; runtime identity still comes
  only from `/var/lib/amigo/current-release`.

## Product invariants

- Program start: 2026-08-15 in `Europe/Moscow`; baseline weight: 127.03 kg.
- User height is 176 cm. AI snapshots may include this identifier-free profile
  fact and a deterministic current BMI derived from the latest Withings weight.
- Plan: lose 4 kg per calendar month, interpolated between matching month-days, capped at 76.5 kg.
- Pre-program weight data is visible in the all-history view but excluded from program KPIs and forecasts.
- Withings is the only source of weight, body composition, blood pressure, and
  pulse recorded during a blood-pressure session. Mi Fitness watch data arrives
  through the Xiaomi Health Cloud client in the signed Android companion;
  Health Connect remains an independently uploaded rollback history. A
  completed Xiaomi snapshot, including a confirmed-empty interval, takes
  precedence over Health Connect for the same metric and time only after the
  direct source passes its three-day activation gate. The gate is anchored by
  the first qualifying recent finalized coverage in the current source-enable
  episode, counts only coverage finalized in that episode, and must not move
  while bounded provider pages upload. Never import cloud or
  Health Connect weight, blood pressure, GPS/location, or exercise routes.
  Dashboard, CSV, Telegram, and minimized AI
  snapshots use daily average/minimum/maximum watch heart rate, while the watch
  heart-rate chart may additionally use persisted hourly
  minimum/average/maximum aggregates. Raw heart-rate samples are never
  persisted. Resting heart rate remains a distinct metric and must never be
  inferred from ordinary samples.
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
- Provider-confirmed empty Health Connect snapshot ranges may span more than 31
  days only as one empty final page with page index zero, and are capped at 50
  years. Android must use that contract to skip empty historical prefixes and
  gaps, while every snapshot that contains records remains at most 30 days.
- Android `ru.tolstik.amigo.sync` opens the authenticated dashboard in a
  top-level WebView by default and keeps native Health Connect sync on a second
  tab. The WebView is fixed to `https://amigo.tolstik.ru`, has no JavaScript
  bridge, blocks mixed content/TLS bypass/third-party cookies/unknown routes,
  and never receives ingest pairing state. App Links cover only `/amigo` and
  `/amigo/...`; authenticated CSV and laboratory-original downloads use the
  system document picker and forward in-memory cookies only to exact allowlisted
  same-origin GET routes, without redirects.
- Android `1.3.4` (`versionCode 14`) accepts up to 25 dashboard uploads from the
  system picker, refreshes a stale foreground WebView, records allowlisted
  background-sync diagnostics, and schedules immediate, hourly, and bounded
  one-minute backfill continuation work. Its in-app updater may download only
  the authenticated exact-origin APK route and must verify size, SHA-256,
  package ID, higher version code, and the installed signing certificate before
  handing the file to the Android system installer for explicit confirmation.
  Backfill continuations append to the active unique-work chain and never
  replace their own running worker; background run IDs prevent stale workers
  from overwriting status. Retryable DNS failures do not advance sync cursors.
  Existing monthly empty cursors fast-forward without resetting pairing, the
  device key, changes tokens, or current cursor state. Health Connect freshness
  prefers the provider modification timestamp over an interval's possibly
  future rounded end, and one failed record type does not prevent later types
  such as sleep from being attempted in the same bounded run. Upgrading from
  an earlier release performs one full heart-rate-only reconciliation with a
  fresh changes token; pairing, the device key, the selected origin, and every
  unrelated record-type cursor remain intact. Direct Xiaomi login runs in a
  separate WebView process with an isolated data directory and an exact HTTPS
  allowlist. The Xiaomi login activity accepts IME input and preserves its
  transient browser state when backgrounded for email verification; completing
  or cancelling the flow clears that state. Only Android-Keystore AES-GCM
  ciphertext crosses back to the main process. Xiaomi credentials, cookies, tokens, account identifiers, and raw
  provider payloads never reach Amigo's server or logs. Cloud synchronization
  uses bounded regional discovery and passToken refresh. Every cloud run first
  reasserts the signed server-side source status before fetching or uploading,
  so a missed initial status request cannot leave all batches rejected as
  `mi_fitness_not_enabled`. Cloud batch identity binds the full canonical
  normalized body and its retry timestamp is fixed to the persisted range end.
  The cursor keeps only bounded SHA-256 record-ID hashes to remove Xiaomi's
  cross-page overlap. A legacy batch/sequence conflict restarts exactly the
  affected unfinished metric snapshot once, preserving credentials, pairing,
  completed history, and every unrelated cursor. It uploads only normalized
  allowlisted records, aggregates ordinary heart rate by hour on the phone, and
  keeps 3-day hourly plus 30-day weekly reconciliation after the descending
  30-day backfill reaches `2000-01-01`.
- Deterministic blood-pressure, heart, SpO2, and VO2 displays remain descriptive
  and never add severity colors or app-side diagnoses. Validated AI may use those
  metrics only for evidence-bound measurement/logging advice or discussion of a
  persistent pattern with a clinician. It must never diagnose, prescribe
  treatment, set medication/dosage, or prescribe a fixed calorie target.
- KPI, trends, plans, forecasts, outliers, baselines, and correlations are
  deterministic. Visible observations and recommendations are generated only
  from a validated AI result; never add template or rule-based narrative
  fallback text.
- The routine health-analysis boundary sends minimized, identifier-free derived
  facts and bounded daily aggregate series to the pinned Codex CLI using the
  fixed `gpt-5.6-sol` model. With explicit `amigo-ai-data-v1` consent, laboratory
  extraction and assistant turns may also send full OCR text, the question, and
  locally selected relevant chunks to OpenAI inference. AI runs asynchronously;
  authenticated GET handlers only read PostgreSQL and never call Codex or enqueue
  analysis.
- Historical AI rows remain in PostgreSQL, but workers and authenticated cache reads
  consider only the active model and prompt contract. The explicit deployment
  enqueue may retry a failed/superseded same-key active job; background enqueue
  must not revive terminal history.
- Deployment AI readiness permits only `ai-ready` exits `0` and `75`, prepares
  one explicit retry while the persistent worker is stopped, and runs at most
  four foreground queue attempts. It must never add an unbounded gateway or AI
  retry loop. Routine health analysis has a separate 150-second Codex deadline
  and 180-second worker client timeout; laboratory extraction, analyte guides,
  and assistant turns retain the fixed 75-second Codex deadline.
- The pre-cutover synthetic AI smoke must exercise the live analysis,
  laboratory-extraction, analyte-guide, and assistant-turn gateway contracts
  with bounded non-personal fixtures. Analysis, laboratory extraction, and
  analyte-guide generation run once; only an
  invalid/error assistant result may be attempted exactly once more with
  `attempt=2`, and the second result must validate fully. Gateway/parser health
  alone is not release readiness.
- AI prompt contract `amigo-health-v4` requires concrete actions, a cadence or
  review period, and cited metric evidence; recommendations are shown before
  general observations in Telegram and on the overview dashboard. When any
  pressure, heart, SpO2, or VO2 evidence exists, validated output must contain
  at least one bounded medical/measurement recommendation. Laboratory evidence
  requires a cited assessment; supplied out-of-reference results also require a
  bounded verification, repeat-test, or clinician-discussion recommendation.
- Production must keep AI enabled and must use exactly
  `http://ai-gateway:8090`; never redirect minimized health snapshots to an
  override endpoint.
- Dashboard, health APIs, CSV, laboratory archive, originals, and assistant are
  protected by one local Argon2id account, 90-day opaque server sessions,
  `Secure`/`SameSite=Strict` cookies, exact-Origin checks, and double-submit CSRF.
  Android signed ingest remains independent. Never expose device identity,
  pairing state, signatures, nonces, raw provider payloads, or raw heart-rate
  samples through the authenticated dashboard.
- Laboratory uploads accept up to 25 PDF/JPG/PNG/HEIC files per selection, each
  up to 20 MiB and each PDF up to 50 pages. PostgreSQL `stored_files` is the
  source of truth for originals; laboratory files are temporarily dual-written
  under random keys in root-owned `/srv/amigo/data/lab-files` (`0700`; files
  `0600`) so the immediately previous release remains recoverable. `web` mounts
  that directory read-write, `ai-worker` read-only, and the isolated non-root
  parser has no file mount, database, secret, or external network. Extracted
  results publish as `unverified`; document ranges override the versioned
  deterministic catalog, and user edits/confirmation are audited. An
  unambiguous explicitly labelled OCR measurement date overrides a model date;
  idempotent archive repair may update inherited non-corrected dates but must
  never overwrite a user-corrected result. Known analyte guides remain versioned
  deterministic reference content. A previously unknown analyte is enriched
  through the isolated local Codex gateway during import and persisted in
  PostgreSQL before that document completes. Contract
  `amigo-lab-analyte-guide-v2` uses batches of at most five; a versioned
  three-attempt queue gradually backfills existing unknown analytes and retries
  terminal work only when the contract changes. Authenticated GET handlers only
  read the persisted guide and never invoke or enqueue inference.
  Backfill never starves interactive assistant or routine analysis work: after
  one guide batch the worker offers foreground AI queues before taking another.
  Production verification requires current-contract backfill progress and no
  terminal current-contract job; it never delays cutover until the entire
  historical backlog drains. The superseded v1 batch of 20 exceeded the pinned
  Codex deadline and must not be restored.
  Laboratory extraction sends at most 3,000 OCR characters per gateway call.
  A timed-out extraction chunk may be divided at most twice; the document job
  still has only three bounded attempts. The exact recovery command may requeue
  only intact terminal `timeout` documents that failed at extraction progress
  40, and must not revive unrelated terminal jobs.
- Study-report uploads support the same bounded formats and queue for ultrasound,
  MRI, CT, X-ray, ECG, and other reports; DICOM is not supported. PostgreSQL
  stores the original plus structured findings/conclusion. Obvious identifier
  header lines are removed before structured study facts can enter AI context.
- The persistent assistant uses `amigo-health-chat-v2`, all structured health,
  laboratory, and study history, the last 12 messages, and a deterministic older
  summary. Originals, filenames, study titles, and OCR pages are excluded from
  assistant context. It may discuss evidence-backed hypotheses and alternatives,
  but streaming drafts remain untrusted until the final result passes evidence
  validation and the hard prohibitions on definitive diagnosis, treatment,
  medication/dosage instructions, and fixed calorie prescriptions.
- Laboratory and study queue screens use PostgreSQL `LISTEN/NOTIFY` plus SSE;
  background workers use bounded notification waits with 60-second fallback
  polling. Healthchecks are lightweight and infrequent, the data worker does not
  write minute heartbeat rows, and browser overview refresh runs only while the
  page is visible.
- The dashboard offers Light, Dark, Ocean, and Sunset themes. With no stored
  choice it must always start in Light regardless of the operating-system color
  scheme; an explicit selection persists and recolors both UI and charts.
- An explicit `30d`, `90d`, `1y`, or `all` chart-period selection is shared by
  the weight, pressure, composition, activity, and recovery pages and persists
  across navigation and page reloads.
- Daily Telegram reports run at `09:00 Europe/Moscow`; the Monday 09:00 weekly
  report replaces that day's daily report. Immediate Withings weight/pressure
  notifications remain enabled. New laboratory facts include verification state
  and are split without truncation; a ready validated AI result adds a separate
  cited laboratory assessment and bounded next step for supplied deviations.
  Filenames, OCR text, originals, and chat are never sent. When AI is
  unavailable, Telegram explicitly sends facts only.
- `web`, `ingest`, and `ai-worker` receive only the PostgreSQL secret. `worker`
  receives the eight integration/database secrets. `ai-gateway` and `lab-parser`
  receive no Docker secrets or database access; only the gateway has its pinned
  binary and dedicated Codex auth state mounted.
- The runtime image must normalize copied backend source to be readable and
  traversable by non-root services. Release preparation uses a restrictive
  `umask`, so build-context file modes must never be trusted for container
  runtime access.

## Latest production checkpoint

<!-- BEGIN AMIGO PRODUCTION CHECKPOINT -->
- Status: **deployed and verified**
- Production URL: `https://amigo.tolstik.ru/amigo/`
- Verified at: `2026-08-24T18:51:16Z` (`2026-08-24 21:51:16 MSK`)
- Git SHA: `9a39511985dee0309451537d2065029be650b279`
- Latest rollback snapshot: `/srv/amigo-rollbacks/20260824T184712Z`
- Installed config SHA-256: Compose `5d34fc668a976c46b6143930afe814dca05a2bd4059ef842346dff2210b92866`; nginx locations `fa20983b63b276d5440b9c9ca2c3aa7211bf441c9dda51656e9b948a55032475`; nginx rate limit `4b2d524cb9cedb0059671b601d983f6f2f73ec1f011054c443c6cd7dafab0104`.
- Pinned Codex: `0.148.0` (`sha256:ac2cfed85fb647d61e0150b8548102b330e4799d9d81ad5d354de701edf6b074`).
- Release access SHA-256: wrapper `721eabf3e79806d3b4ffecaaba7d2105632016ba1e4c90ae99f41af361818527`; sudoers policy `c02cd113d07deac89aaac689777fcdb89deafb3f011135a17d04428d25dee8ea`.
- Verification: all seven Compose services healthy; application services use the release image; PostgreSQL ready; the current worker completed a successful post-start Withings incremental job; web and ingest are bound only to `127.0.0.1:18181` and `127.0.0.1:18182`; database-owned originals, repaired laboratory dates, analyte guides, signed Android updater/APK, laboratory and study queues, assistant/queue SSE, authentication, exact Origin/CSRF, authenticated API/CSV/upload checks, root-only laboratory storage, parser/gateway isolation and unpublished ports, container secret boundaries, pinned Codex hash, fixed `gpt-5.6-sol`/`amigo-health-v4` gateway health, root-owned least-privilege release access, signed-ingest rejection, origin proxy, HTTPS login shell, hidden health routes, immutable frontend assets, cron isolation, previous-release auth-floor recovery assets, and the explicit legacy disaster-fallback guard passed.
- Installed image references and IDs:

- `web`: `amigo:9a39511985dee0309451537d2065029be650b279` (`sha256:a075df043ecfc967d785a38539996958481580ace025e76eb5307bc864164fb9`)
- `worker`: `amigo:9a39511985dee0309451537d2065029be650b279` (`sha256:a075df043ecfc967d785a38539996958481580ace025e76eb5307bc864164fb9`)
- `ingest`: `amigo:9a39511985dee0309451537d2065029be650b279` (`sha256:a075df043ecfc967d785a38539996958481580ace025e76eb5307bc864164fb9`)
- `ai-worker`: `amigo:9a39511985dee0309451537d2065029be650b279` (`sha256:a075df043ecfc967d785a38539996958481580ace025e76eb5307bc864164fb9`)
- `ai-gateway`: `amigo:9a39511985dee0309451537d2065029be650b279` (`sha256:a075df043ecfc967d785a38539996958481580ace025e76eb5307bc864164fb9`)
- `lab-parser`: `amigo:9a39511985dee0309451537d2065029be650b279` (`sha256:a075df043ecfc967d785a38539996958481580ace025e76eb5307bc864164fb9`)
- `db`: `postgres:17-alpine` (`sha256:1bea307dfb3ee30541a7acf7de14b58bcd6948da98e5d31a04c627c4d35ec64b`)
- Previous-release recovery command: `sudo /srv/amigo/deploy/restore-previous-release.sh /srv/amigo-rollbacks/20260824T184712Z`
- Legacy disaster fallback command: `sudo /srv/amigo/deploy/rollback.sh --to-legacy /srv/amigo-rollbacks/20260824T184712Z`
<!-- END AMIGO PRODUCTION CHECKPOINT -->
