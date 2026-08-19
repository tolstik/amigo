# Amigo project instructions

## Safety and production

- The production dashboard URL is `https://amigo.tolstik.ru/amigo/`.
- Production runs on `192.168.31.3`; application files belong under `/srv/amigo`.
- `/srv/cron` is shared with unrelated systems. Never edit it recursively and never remove the shared `send_telergam.php all` job.
- The legacy application in `/srv/www/amigo` and the legacy MariaDB database `amigo` must remain intact until a documented rollback no longer requires them.
- Never commit or document passwords, OAuth credentials, Telegram tokens, chat IDs, cookies, or authorization headers.
- Before every production cutover, create and verify the backup described in `docs/runbook.md`.
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
