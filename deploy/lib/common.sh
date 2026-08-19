#!/usr/bin/env bash
# shellcheck disable=SC2034

# Shared, deliberately immutable production paths. This file is sourced by the
# executable scripts in deploy/; it must never read secrets or print .env.

readonly AMIGO_APP_DIR="/srv/amigo"
readonly AMIGO_COMPOSE_FILE="/srv/amigo/compose.yaml"
readonly AMIGO_ENV_FILE="/srv/amigo/.env"
readonly AMIGO_SECRETS_DIR="/srv/amigo/secrets"
readonly AMIGO_IMPORT_DIR="/srv/amigo/data/import"
readonly AMIGO_LEGACY_WEIGHT_IMPORT="/srv/amigo/data/import/legacy-weight.tsv"
readonly AMIGO_ROLLBACK_ROOT="/srv/amigo-rollbacks"
readonly AMIGO_STATE_DIR="/var/lib/amigo"
readonly AMIGO_CURRENT_RELEASE_FILE="/var/lib/amigo/current-release"

readonly AMIGO_LEGACY_WEB_DIR="/srv/www/amigo"
readonly AMIGO_LEGACY_DB="amigo"
readonly AMIGO_LEGACY_CRON_USER="tolstik"
readonly AMIGO_LEGACY_WITHINGS_CRON_LINE='*/1 07-08 * * *  php /srv/cron/get_withings.php'
readonly AMIGO_LEGACY_WITHINGS_DISABLED_LINE='# AMIGO_V2_DISABLED */1 07-08 * * *  php /srv/cron/get_withings.php'
readonly AMIGO_SHARED_TELEGRAM_CRON_LINE='*/1 * * * *  php /srv/cron/send_telergam.php all'

readonly AMIGO_NGINX_CONFIG="/etc/nginx/conf.d/my.conf"
readonly AMIGO_NGINX_SNIPPET="/etc/nginx/snippets/amigo-v2-locations.conf"
readonly AMIGO_NGINX_HTTP_CONFIG="/etc/nginx/conf.d/amigo-v2-http.conf"
readonly AMIGO_PUBLIC_URL="https://amigo.tolstik.ru/amigo/"
readonly AMIGO_DIRECT_HEALTH_URL="http://127.0.0.1:18181/healthz"

AMIGO_COMMON_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
AMIGO_DEPLOY_DIR="$(cd -- "${AMIGO_COMMON_DIR}/.." && pwd -P)"
readonly AMIGO_COMMON_DIR AMIGO_DEPLOY_DIR

amigo_log() {
    printf '[amigo] %s\n' "$*" >&2
}

amigo_die() {
    amigo_log "ERROR: $*"
    # Returning (instead of calling exit) lets an enclosing ERR trap perform a
    # transactional deploy/rollback recovery before the shell terminates.
    return 1
}

amigo_require_root() {
    [[ ${EUID} -eq 0 ]] || amigo_die "run this command as root"
}

amigo_require_commands() {
    local command_name
    for command_name in "$@"; do
        command -v -- "${command_name}" >/dev/null 2>&1 \
            || amigo_die "required command is missing: ${command_name}"
    done
}

amigo_require_production_layout() {
    [[ -d "${AMIGO_APP_DIR}" ]] \
        || amigo_die "application directory is missing: ${AMIGO_APP_DIR}"
    [[ -f "${AMIGO_COMPOSE_FILE}" ]] \
        || amigo_die "Compose file is missing: ${AMIGO_COMPOSE_FILE}"
    [[ -f "${AMIGO_ENV_FILE}" ]] \
        || amigo_die "environment file is missing: ${AMIGO_ENV_FILE}"
}

amigo_current_release() {
    local release_sha=${AMIGO_IMAGE_TAG:-}
    if [[ -z "${release_sha}" && -f "${AMIGO_CURRENT_RELEASE_FILE}" ]]; then
        IFS= read -r release_sha <"${AMIGO_CURRENT_RELEASE_FILE}"
    fi
    if [[ -z "${release_sha}" ]] && command -v git >/dev/null 2>&1; then
        release_sha=$(git -C "${AMIGO_APP_DIR}" rev-parse HEAD 2>/dev/null || true)
    fi
    [[ "${release_sha}" =~ ^[0-9a-f]{40,64}$ ]] \
        || amigo_die "no valid deployed Git SHA is available"
    printf '%s\n' "${release_sha}"
}

amigo_record_current_release() {
    local release_sha=$1
    local candidate

    [[ "${release_sha}" =~ ^[0-9a-f]{40,64}$ ]] \
        || amigo_die "refusing to record an invalid release SHA"
    amigo_require_commands chmod chown install mktemp mv
    install -d -o root -g root -m 0700 "${AMIGO_STATE_DIR}"
    candidate=$(mktemp "${AMIGO_STATE_DIR}/.current-release.XXXXXX") \
        || amigo_die "cannot create release state candidate"
    printf '%s\n' "${release_sha}" >"${candidate}"
    chmod 0600 "${candidate}"
    chown root:root "${candidate}"
    mv -- "${candidate}" "${AMIGO_CURRENT_RELEASE_FILE}"
}

amigo_compose() {
    local image_tag
    image_tag=$(amigo_current_release)

    AMIGO_IMAGE_TAG="${image_tag}" docker compose \
        --project-directory "${AMIGO_APP_DIR}" \
        --file "${AMIGO_COMPOSE_FILE}" \
        --env-file "${AMIGO_ENV_FILE}" \
        "$@"
}

amigo_acquire_deploy_lock() {
    if [[ ${AMIGO_DEPLOY_LOCK_HELD:-0} == 1 ]]; then
        return
    fi

    exec 9>"/run/lock/amigo-deploy.lock"
    flock -n 9 || amigo_die "another Amigo deploy or rollback is running"
    export AMIGO_DEPLOY_LOCK_HELD=1
}

amigo_assert_snapshot() {
    local snapshot_path=${1:-}
    local resolved_path

    [[ "${snapshot_path}" =~ ^/srv/amigo-rollbacks/[0-9]{8}T[0-9]{6}Z$ ]] \
        || amigo_die "snapshot must be an explicit timestamped path under ${AMIGO_ROLLBACK_ROOT}"
    [[ -d "${snapshot_path}" ]] || amigo_die "snapshot does not exist: ${snapshot_path}"
    resolved_path=$(realpath -e -- "${snapshot_path}")
    [[ "${resolved_path}" == "${snapshot_path}" ]] \
        || amigo_die "snapshot path must not contain symlinks: ${snapshot_path}"
    [[ -f "${snapshot_path}/SHA256SUMS" ]] \
        || amigo_die "snapshot manifest is missing: ${snapshot_path}/SHA256SUMS"

    (
        cd -- "${snapshot_path}"
        sha256sum --check --strict SHA256SUMS >/dev/null
    ) || amigo_die "snapshot checksum verification failed: ${snapshot_path}"
}

amigo_count_exact_line() {
    local line=$1
    local file=$2
    awk -v expected="${line}" '$0 == expected { count += 1 } END { print count + 0 }' "${file}"
}

amigo_wait_for_http() {
    local url=$1
    local attempts=${2:-60}
    local attempt

    for ((attempt = 1; attempt <= attempts; attempt += 1)); do
        if curl --fail --silent --show-error --max-time 5 --output /dev/null "${url}"; then
            return 0
        fi
        sleep 2
    done
    return 1
}

amigo_handback_withings_tokens() {
    local handoff_dir
    local status=0

    amigo_require_commands chmod mktemp php rm rmdir
    [[ -f "${AMIGO_DEPLOY_DIR}/legacy-token-handback.php" ]] \
        || amigo_die "legacy token handback helper is missing"

    handoff_dir=$(mktemp -d /run/amigo-token-handoff.XXXXXX) \
        || amigo_die "cannot create token handoff directory"
    chmod 0700 "${handoff_dir}"

    if ! amigo_compose run --rm --no-deps \
        --volume "${handoff_dir}:/handoff" \
        worker python -m app.cli withings-token-handoff --directory /handoff; then
        status=1
    elif ! php "${AMIGO_DEPLOY_DIR}/legacy-token-handback.php" \
        "${handoff_dir}/access_token" "${handoff_dir}/refresh_token"; then
        status=1
    fi

    rm -f -- "${handoff_dir}/access_token" "${handoff_dir}/refresh_token"
    rmdir -- "${handoff_dir}" 2>/dev/null || status=1
    [[ ${status} -eq 0 ]] || amigo_die "Withings token handback failed"
}
