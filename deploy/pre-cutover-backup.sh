#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
# shellcheck source=lib/common.sh
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

amigo_require_root
amigo_require_commands \
    awk chmod cp crontab date find flock gzip hostname install mv nginx realpath \
    sha256sum sort tar xargs
amigo_acquire_deploy_lock

[[ -d "${AMIGO_LEGACY_WEB_DIR}" ]] \
    || amigo_die "legacy web directory is missing: ${AMIGO_LEGACY_WEB_DIR}"
[[ -f "${AMIGO_NGINX_CONFIG}" ]] \
    || amigo_die "origin nginx config is missing: ${AMIGO_NGINX_CONFIG}"
nginx -t >/dev/null

if command -v mariadb-dump >/dev/null 2>&1; then
    DB_DUMP_COMMAND="$(command -v mariadb-dump)"
elif command -v mysqldump >/dev/null 2>&1; then
    DB_DUMP_COMMAND="$(command -v mysqldump)"
else
    amigo_die "neither mariadb-dump nor mysqldump is installed"
fi
readonly DB_DUMP_COMMAND

CREATED_AT="$(date -u +%Y%m%dT%H%M%SZ)"
readonly CREATED_AT
readonly FINAL_DIR="${AMIGO_ROLLBACK_ROOT}/${CREATED_AT}"
readonly STAGING_DIR="${AMIGO_ROLLBACK_ROOT}/.partial-${CREATED_AT}-$$"

install -d -o root -g root -m 0700 "${AMIGO_ROLLBACK_ROOT}"
[[ ! -e "${FINAL_DIR}" ]] || amigo_die "snapshot already exists: ${FINAL_DIR}"
[[ ! -e "${STAGING_DIR}" ]] || amigo_die "staging path already exists: ${STAGING_DIR}"
install -d -o root -g root -m 0700 \
    "${STAGING_DIR}" \
    "${STAGING_DIR}/crontab" \
    "${STAGING_DIR}/nginx"

backup_failed() {
    local status=$?
    if [[ ${status} -ne 0 ]]; then
        amigo_log "backup failed; incomplete artifacts were preserved at ${STAGING_DIR}"
    fi
}
trap backup_failed EXIT

amigo_log "archiving legacy application"
tar \
    --acls \
    --xattrs \
    --numeric-owner \
    --one-file-system \
    -C "/srv/www" \
    -czf "${STAGING_DIR}/legacy-www-amigo.tar.gz" \
    "amigo"

amigo_log "dumping legacy MariaDB database"
"${DB_DUMP_COMMAND}" \
    --single-transaction \
    --quick \
    --routines \
    --events \
    --triggers \
    --hex-blob \
    --databases "${AMIGO_LEGACY_DB}" \
    | gzip -9 >"${STAGING_DIR}/legacy-mariadb-amigo.sql.gz"

amigo_log "capturing crontabs"
crontab -u "${AMIGO_LEGACY_CRON_USER}" -l \
    >"${STAGING_DIR}/crontab/${AMIGO_LEGACY_CRON_USER}.crontab" \
    || amigo_die "cannot snapshot crontab for ${AMIGO_LEGACY_CRON_USER}"
if ! crontab -u root -l >"${STAGING_DIR}/crontab/root.crontab"; then
    printf '# root had no crontab at %s\n' "${CREATED_AT}" \
        >"${STAGING_DIR}/crontab/root.crontab"
fi

readonly CRON_SNAPSHOT="${STAGING_DIR}/crontab/${AMIGO_LEGACY_CRON_USER}.crontab"
ACTIVE_WITHINGS_COUNT="$(amigo_count_exact_line "${AMIGO_LEGACY_WITHINGS_CRON_LINE}" "${CRON_SNAPSHOT}")"
DISABLED_WITHINGS_COUNT="$(amigo_count_exact_line "${AMIGO_LEGACY_WITHINGS_DISABLED_LINE}" "${CRON_SNAPSHOT}")"
readonly ACTIVE_WITHINGS_COUNT DISABLED_WITHINGS_COUNT
[[ $((ACTIVE_WITHINGS_COUNT + DISABLED_WITHINGS_COUNT)) -eq 1 ]] \
    || amigo_die "snapshot must contain exactly one active or disabled legacy Withings cron marker"
[[ "$(amigo_count_exact_line "${AMIGO_SHARED_TELEGRAM_CRON_LINE}" "${CRON_SNAPSHOT}")" -ge 1 ]] \
    || amigo_die "shared send_telergam cron line is missing from the snapshot"

amigo_log "capturing nginx configuration"
cp --archive -- "${AMIGO_NGINX_CONFIG}" "${STAGING_DIR}/nginx/my.conf"
tar \
    --acls \
    --xattrs \
    --numeric-owner \
    --one-file-system \
    -C "/etc" \
    -czf "${STAGING_DIR}/nginx-config.tar.gz" \
    "nginx"
nginx -T >"${STAGING_DIR}/nginx/nginx-T.txt" 2>&1

{
    printf 'created_at_utc=%s\n' "${CREATED_AT}"
    printf 'host=%s\n' "$(hostname --fqdn 2>/dev/null || hostname)"
    printf 'legacy_web_dir=%s\n' "${AMIGO_LEGACY_WEB_DIR}"
    printf 'legacy_database=%s\n' "${AMIGO_LEGACY_DB}"
    printf 'nginx_config=%s\n' "${AMIGO_NGINX_CONFIG}"
    printf 'cron_user=%s\n' "${AMIGO_LEGACY_CRON_USER}"
    if [[ "${ACTIVE_WITHINGS_COUNT}" -eq 1 ]]; then
        printf 'legacy_withings_cron_state=active\n'
    else
        printf 'legacy_withings_cron_state=disabled\n'
    fi
    if git -C "${AMIGO_APP_DIR}" rev-parse HEAD >/dev/null 2>&1; then
        printf 'candidate_git_sha=%s\n' "$(git -C "${AMIGO_APP_DIR}" rev-parse HEAD)"
    fi
} >"${STAGING_DIR}/metadata.txt"

amigo_log "verifying archives and database dump"
tar -tzf "${STAGING_DIR}/legacy-www-amigo.tar.gz" >/dev/null
tar -tzf "${STAGING_DIR}/nginx-config.tar.gz" >/dev/null
gzip -t "${STAGING_DIR}/legacy-mariadb-amigo.sql.gz"
gzip -dc "${STAGING_DIR}/legacy-mariadb-amigo.sql.gz" \
    | awk '/(Current Database: `amigo`|CREATE DATABASE.*`amigo`)/ { found = 1 } END { exit(found ? 0 : 1) }' \
    || amigo_die "database dump does not identify the amigo database"

find "${STAGING_DIR}" -type d -exec chmod 0700 {} +
find "${STAGING_DIR}" -type f -exec chmod 0600 {} +
(
    cd -- "${STAGING_DIR}"
    # SHA256SUMS is explicitly excluded from the read side of this pipeline.
    # shellcheck disable=SC2094
    find . -type f ! -name SHA256SUMS -print0 \
        | sort -z \
        | xargs -0 sha256sum >SHA256SUMS
    chmod 0600 SHA256SUMS
    sha256sum --check --strict SHA256SUMS >/dev/null
)

mv -- "${STAGING_DIR}" "${FINAL_DIR}"
trap - EXIT
amigo_log "verified rollback snapshot created: ${FINAL_DIR}"
printf '%s\n' "${FINAL_DIR}"
