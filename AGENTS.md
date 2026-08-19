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
  existing v2 worker before migrations and one-shot synchronization and restarts
  it automatically after an early pre-cutover failure.
- Before re-enabling the legacy Withings cron, stop the v2 worker and complete the secret-free OAuth token handback from PostgreSQL to the single legacy MariaDB token row.
- The deployed application SHA is recorded in `/var/lib/amigo/current-release`; verification must use it rather than assuming the documentation checkout HEAD equals the running image.
- After every production deployment, update this file and the runbook checkpoint with the deployed Git SHA/image IDs, verification results, production URL, and latest rollback location before reporting completion.

## Product invariants

- Program start: 2026-08-15 in `Europe/Moscow`; baseline weight: 127.03 kg.
- Plan: lose 4 kg per calendar month, interpolated between matching month-days, capped at 76.5 kg.
- Pre-program weight data is visible in the all-history view but excluded from program KPIs and forecasts.
- Blood pressure is descriptive statistics only: do not add diagnostic categories, severity colors, medical thresholds, or treatment advice.
- Advice is deterministic and rule based; do not send health data to an external language model.
- The dashboard is intentionally public and read-only. Configuration and integration mutations remain server-side only.
- The public `web` container receives only the PostgreSQL secret. Withings, Fernet, and Telegram credentials belong to `worker` and transient worker CLI jobs.

## Latest production checkpoint

<!-- BEGIN AMIGO PRODUCTION CHECKPOINT -->
- Status: **deployed and verified**
- Production URL: `https://amigo.tolstik.ru/amigo/`
- Verified at: `2026-08-19T15:51:29Z` (`2026-08-19 18:51:29 MSK`)
- Git SHA: `f447960ab5d0d798b6ea84aa6afa0fe4865bd115`
- Latest rollback snapshot: `/srv/amigo-rollbacks/20260819T154852Z`
- Installed config SHA-256: Compose `6727c935a9960a6a83c332c83b304d8814c26a328dd04d5a82ee63b2009847d7`; nginx locations `37ca8718449885e28e44fb04ded159913169d43b839024960c861063186d574e`; nginx rate limit `a887ddf70734dda6821fcd4db984d99dcb70993eff64a5ae4a2108e517a93362`.
- Verification: Compose `web`, `worker`, and `db` running; PostgreSQL ready; web bound to `127.0.0.1:18181`; direct health, hidden public health routes, origin proxy, public HTTPS dashboard/API, relative `308`, security headers, route rollback rehearsal, cron isolation, and rollback assets passed.
- Installed image references and IDs:

- `web`: `amigo:f447960ab5d0d798b6ea84aa6afa0fe4865bd115` (`sha256:f7ff42f496def8ff86c1c8f20ed959a6d18ef6cc88176849eed214038f6b3027`)
- `worker`: `amigo:f447960ab5d0d798b6ea84aa6afa0fe4865bd115` (`sha256:f7ff42f496def8ff86c1c8f20ed959a6d18ef6cc88176849eed214038f6b3027`)
- `db`: `postgres:17-alpine` (`sha256:1bea307dfb3ee30541a7acf7de14b58bcd6948da98e5d31a04c627c4d35ec64b`)
- Rollback command: `sudo /srv/amigo/deploy/rollback.sh /srv/amigo-rollbacks/20260819T154852Z`
<!-- END AMIGO PRODUCTION CHECKPOINT -->
