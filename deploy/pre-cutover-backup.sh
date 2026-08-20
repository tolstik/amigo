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
    awk chmod cmp cp crontab date docker find flock git gzip hostname install mv nginx realpath \
    sha256sum sort tar xargs
amigo_acquire_deploy_lock

[[ -d "${AMIGO_LEGACY_WEB_DIR}" ]] \
    || amigo_die "legacy web directory is missing: ${AMIGO_LEGACY_WEB_DIR}"
[[ -f "${AMIGO_NGINX_CONFIG}" ]] \
    || amigo_die "origin nginx config is missing: ${AMIGO_NGINX_CONFIG}"
nginx -t >/dev/null

PREVIOUS_RELEASE_SHA="$(amigo_recorded_release)"
readonly PREVIOUS_RELEASE_SHA
git -C "${AMIGO_APP_DIR}" cat-file -e "${PREVIOUS_RELEASE_SHA}^{commit}" \
    || amigo_die "recorded previous release is unavailable in the production checkout"
PREVIOUS_IMAGE_REFERENCE="amigo:${PREVIOUS_RELEASE_SHA}"
PREVIOUS_IMAGE_ID="$(docker image inspect --format '{{.Id}}' "${PREVIOUS_IMAGE_REFERENCE}")" \
    || amigo_die "previous application image is unavailable: ${PREVIOUS_IMAGE_REFERENCE}"
[[ "${PREVIOUS_IMAGE_ID}" =~ ^sha256:[0-9a-f]{64}$ ]] \
    || amigo_die "previous application image has an invalid immutable ID"
PREVIOUS_IMAGE_ROLLBACK_REFERENCE="amigo-rollback:${PREVIOUS_RELEASE_SHA}-${PREVIOUS_IMAGE_ID:7:12}"
readonly PREVIOUS_IMAGE_REFERENCE PREVIOUS_IMAGE_ID PREVIOUS_IMAGE_ROLLBACK_REFERENCE
if git -C "${AMIGO_APP_DIR}" cat-file -e "${PREVIOUS_RELEASE_SHA}:backend/app/auth.py" 2>/dev/null; then
    PREVIOUS_AUTH_FLOOR="enabled"
else
    PREVIOUS_AUTH_FLOOR="disabled"
fi
readonly PREVIOUS_AUTH_FLOOR
amigo_assert_image_revision "${PREVIOUS_IMAGE_REFERENCE}" "${PREVIOUS_RELEASE_SHA}"
docker image tag "${PREVIOUS_IMAGE_ID}" "${PREVIOUS_IMAGE_ROLLBACK_REFERENCE}"
[[ "$(docker image inspect --format '{{.Id}}' "${PREVIOUS_IMAGE_ROLLBACK_REFERENCE}")" \
    == "${PREVIOUS_IMAGE_ID}" ]] \
    || amigo_die "cannot preserve the previous application image under its rollback tag"

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
    "${STAGING_DIR}/nginx" \
    "${STAGING_DIR}/release" \
    "${STAGING_DIR}/release/nginx" \
    "${STAGING_DIR}/data"

backup_failed() {
    local status=$?
    if [[ ${status} -ne 0 ]]; then
        amigo_log "backup failed; incomplete artifacts were preserved at ${STAGING_DIR}"
    fi
}
trap backup_failed EXIT

amigo_log "capturing the immutable previous Amigo release envelope"
git -C "${AMIGO_APP_DIR}" show "${PREVIOUS_RELEASE_SHA}:compose.yaml" \
    >"${STAGING_DIR}/release/compose.yaml"
git -C "${AMIGO_APP_DIR}" show \
    "${PREVIOUS_RELEASE_SHA}:deploy/nginx/amigo.locations.conf" \
    >"${STAGING_DIR}/release/nginx/amigo.locations.conf"
git -C "${AMIGO_APP_DIR}" show \
    "${PREVIOUS_RELEASE_SHA}:deploy/nginx/amigo.http.conf" \
    >"${STAGING_DIR}/release/nginx/amigo.http.conf"
amigo_compose_file_release \
    "${STAGING_DIR}/release/compose.yaml" "${PREVIOUS_RELEASE_SHA}" config --quiet

mapfile -t PREVIOUS_SERVICES < <(
    amigo_compose_file_release \
        "${STAGING_DIR}/release/compose.yaml" "${PREVIOUS_RELEASE_SHA}" config --services
)
readonly -a PREVIOUS_SERVICES
previous_has_service() {
    local expected=$1
    local service
    for service in "${PREVIOUS_SERVICES[@]}"; do
        [[ "${service}" == "${expected}" ]] && return 0
    done
    return 1
}
for required_previous_service in db web worker ingest ai-worker ai-gateway; do
    previous_has_service "${required_previous_service}" \
        || amigo_die "previous release Compose is missing required service: ${required_previous_service}"
done

for previous_service in web worker ingest ai-worker ai-gateway lab-parser; do
    previous_has_service "${previous_service}" || continue
    previous_container=$(amigo_compose_file_release \
        "${STAGING_DIR}/release/compose.yaml" "${PREVIOUS_RELEASE_SHA}" \
        ps -q "${previous_service}")
    [[ -n "${previous_container}" ]] \
        || amigo_die "previous release container is missing: ${previous_service}"
    [[ "$(docker inspect --format '{{.Config.Image}}' "${previous_container}")" \
        == "${PREVIOUS_IMAGE_REFERENCE}" ]] \
        || amigo_die "${previous_service} does not use the recorded previous image reference"
    [[ "$(docker inspect --format '{{.Image}}' "${previous_container}")" \
        == "${PREVIOUS_IMAGE_ID}" ]] \
        || amigo_die "${previous_service} does not use the recorded previous image ID"
    [[ "$(docker inspect --format \
        '{{if .Config.Labels}}{{index .Config.Labels "org.opencontainers.image.revision"}}{{end}}' \
        "${previous_container}")" == "${PREVIOUS_RELEASE_SHA}" ]] \
        || amigo_die "${previous_service} OCI revision differs from the recorded release"
done

previous_database_container=$(amigo_compose_file_release \
    "${STAGING_DIR}/release/compose.yaml" "${PREVIOUS_RELEASE_SHA}" ps -q db)
[[ -n "${previous_database_container}" ]] \
    || amigo_die "previous release database container is missing"
PREVIOUS_DATABASE_IMAGE_REFERENCE="$(docker inspect --format '{{.Config.Image}}' \
    "${previous_database_container}")"
PREVIOUS_DATABASE_IMAGE_ID="$(docker inspect --format '{{.Image}}' \
    "${previous_database_container}")"
readonly PREVIOUS_DATABASE_IMAGE_REFERENCE PREVIOUS_DATABASE_IMAGE_ID
[[ "${PREVIOUS_DATABASE_IMAGE_REFERENCE}" == "postgres:17-alpine" ]] \
    || amigo_die "previous database container uses an unexpected image reference"
[[ "${PREVIOUS_DATABASE_IMAGE_ID}" =~ ^sha256:[0-9a-f]{64}$ ]] \
    || amigo_die "previous database container has an invalid immutable image ID"
[[ "$(docker inspect --format '{{.State.Health.Status}}' \
    "${previous_database_container}")" == "healthy" ]] \
    || amigo_die "previous database container is not healthy"
PREVIOUS_DATABASE_ROLLBACK_REFERENCE="amigo-postgres-rollback:${PREVIOUS_DATABASE_IMAGE_ID:7}"
readonly PREVIOUS_DATABASE_ROLLBACK_REFERENCE
docker image tag "${PREVIOUS_DATABASE_IMAGE_ID}" "${PREVIOUS_DATABASE_ROLLBACK_REFERENCE}"
[[ "$(docker image inspect --format '{{.Id}}' "${PREVIOUS_DATABASE_ROLLBACK_REFERENCE}")" \
    == "${PREVIOUS_DATABASE_IMAGE_ID}" ]] \
    || amigo_die "cannot preserve the previous PostgreSQL image under its rollback tag"

read -r PREVIOUS_AI_MODEL PREVIOUS_AI_PROMPT_VERSION < <(
    docker run --rm --network none --read-only --tmpfs /tmp:size=8m,mode=1777 \
        --entrypoint python "${PREVIOUS_IMAGE_REFERENCE}" -c \
        'from app.ai_contracts import AI_MODEL, AI_PROMPT_VERSION; print(AI_MODEL, AI_PROMPT_VERSION)'
)
readonly PREVIOUS_AI_MODEL PREVIOUS_AI_PROMPT_VERSION
[[ "${PREVIOUS_AI_MODEL}" =~ ^[A-Za-z0-9._-]{1,64}$ ]] \
    || amigo_die "previous AI model identifier is invalid"
[[ "${PREVIOUS_AI_PROMPT_VERSION}" =~ ^[A-Za-z0-9._-]{1,64}$ ]] \
    || amigo_die "previous AI prompt identifier is invalid"

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

POSTGRES_DUMP_CREATED=0
postgres_container="$(amigo_compose ps -q db 2>/dev/null || true)"
if [[ -n "${postgres_container}" ]] \
    && [[ "$(docker inspect --format '{{.State.Status}}' "${postgres_container}")" == "running" ]]; then
    amigo_log "dumping the current Amigo PostgreSQL database"
    amigo_compose exec -T db pg_dump \
        --username amigo \
        --dbname amigo \
        --format=custom \
        --no-owner \
        --no-privileges \
        >"${STAGING_DIR}/postgres-amigo.dump"
    [[ -s "${STAGING_DIR}/postgres-amigo.dump" ]] \
        || amigo_die "PostgreSQL dump is empty"
    amigo_compose exec -T db pg_restore --list \
        <"${STAGING_DIR}/postgres-amigo.dump" >/dev/null
    POSTGRES_DUMP_CREATED=1
fi

amigo_log "archiving protected laboratory originals"
tar \
    --acls \
    --xattrs \
    --numeric-owner \
    --one-file-system \
    -C "${AMIGO_APP_DIR}/data" \
    -czf "${STAGING_DIR}/data/lab-files.tar.gz" \
    "lab-files"

PREVIOUS_ANDROID_APK_PRESENT="false"
if [[ -e "${AMIGO_ANDROID_APK}" ]]; then
    [[ -f "${AMIGO_ANDROID_APK}" && ! -L "${AMIGO_ANDROID_APK}" ]] \
        || amigo_die "installed Android APK is not a regular file"
    cp --preserve=mode,timestamps -- "${AMIGO_ANDROID_APK}" \
        "${STAGING_DIR}/data/amigo-sync.apk"
    PREVIOUS_ANDROID_APK_PRESENT="true"
fi
readonly PREVIOUS_ANDROID_APK_PRESENT

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
[[ "${ACTIVE_WITHINGS_COUNT}" -eq 0 && "${DISABLED_WITHINGS_COUNT}" -eq 1 ]] \
    || amigo_die "previous Amigo release requires the legacy Withings cron to be disabled"
[[ "$(amigo_count_exact_line "${AMIGO_SHARED_TELEGRAM_CRON_LINE}" "${CRON_SNAPSHOT}")" -ge 1 ]] \
    || amigo_die "shared send_telergam cron line is missing from the snapshot"

amigo_log "capturing nginx configuration"
cp --archive -- "${AMIGO_NGINX_CONFIG}" "${STAGING_DIR}/nginx/my.conf"
[[ -f "${AMIGO_NGINX_SNIPPET}" && ! -L "${AMIGO_NGINX_SNIPPET}" ]] \
    || amigo_die "installed managed nginx locations file is missing or is a symlink"
[[ -f "${AMIGO_NGINX_HTTP_CONFIG}" && ! -L "${AMIGO_NGINX_HTTP_CONFIG}" ]] \
    || amigo_die "installed managed nginx HTTP file is missing or is a symlink"
cp --archive -- "${AMIGO_NGINX_SNIPPET}" \
    "${STAGING_DIR}/nginx/amigo-v2-locations.conf"
cp --archive -- "${AMIGO_NGINX_HTTP_CONFIG}" \
    "${STAGING_DIR}/nginx/amigo-v2-http.conf"
if [[ "${PREVIOUS_AUTH_FLOOR}" == "enabled" ]]; then
    cmp --silent "${STAGING_DIR}/release/nginx/amigo.locations.conf" \
        "${STAGING_DIR}/nginx/amigo-v2-locations.conf" \
        || amigo_die "installed nginx locations do not match the previous release"
    cmp --silent "${STAGING_DIR}/release/nginx/amigo.http.conf" \
        "${STAGING_DIR}/nginx/amigo-v2-http.conf" \
        || amigo_die "installed nginx HTTP config does not match the previous release"
else
    cmp --silent "${SCRIPT_DIR}/nginx/amigo.maintenance.locations.conf" \
        "${STAGING_DIR}/nginx/amigo-v2-locations.conf" \
        || amigo_die "installed auth-floor locations do not match the candidate maintenance release"
    cmp --silent "${SCRIPT_DIR}/nginx/amigo.http.conf" \
        "${STAGING_DIR}/nginx/amigo-v2-http.conf" \
        || amigo_die "installed auth-floor HTTP config does not match the candidate release"
fi
[[ "$(awk '/^[[:space:]]*# BEGIN AMIGO V2 ROUTE[[:space:]]*$/ { count += 1 } END { print count + 0 }' \
    "${STAGING_DIR}/nginx/my.conf")" -eq 2 ]] \
    || amigo_die "previous managed Amigo nginx route is not active"
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
    printf 'previous_release_sha=%s\n' "${PREVIOUS_RELEASE_SHA}"
    printf 'previous_application_image=%s\n' "${PREVIOUS_IMAGE_REFERENCE}"
    printf 'previous_application_image_id=%s\n' "${PREVIOUS_IMAGE_ID}"
    printf 'previous_application_rollback_image=%s\n' "${PREVIOUS_IMAGE_ROLLBACK_REFERENCE}"
    printf 'previous_database_image=%s\n' "${PREVIOUS_DATABASE_IMAGE_REFERENCE}"
    printf 'previous_database_image_id=%s\n' "${PREVIOUS_DATABASE_IMAGE_ID}"
    printf 'previous_database_rollback_image=%s\n' "${PREVIOUS_DATABASE_ROLLBACK_REFERENCE}"
    printf 'previous_ai_model=%s\n' "${PREVIOUS_AI_MODEL}"
    printf 'previous_ai_prompt_version=%s\n' "${PREVIOUS_AI_PROMPT_VERSION}"
    printf 'previous_auth_floor=%s\n' "${PREVIOUS_AUTH_FLOOR}"
    printf 'previous_managed_route_state=enabled\n'
    printf 'previous_compose_sha256=%s\n' \
        "$(sha256sum "${STAGING_DIR}/release/compose.yaml" | awk '{ print $1 }')"
    printf 'postgres_dump_created=%s\n' "${POSTGRES_DUMP_CREATED}"
    printf 'previous_android_apk_present=%s\n' "${PREVIOUS_ANDROID_APK_PRESENT}"
} >"${STAGING_DIR}/metadata.txt"

amigo_log "verifying archives and database dump"
tar -tzf "${STAGING_DIR}/legacy-www-amigo.tar.gz" >/dev/null
tar -tzf "${STAGING_DIR}/nginx-config.tar.gz" >/dev/null
tar -tzf "${STAGING_DIR}/data/lab-files.tar.gz" >/dev/null
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
