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

amigo_recorded_release() {
    local release_sha=""

    [[ -f "${AMIGO_CURRENT_RELEASE_FILE}" && ! -L "${AMIGO_CURRENT_RELEASE_FILE}" ]] \
        || amigo_die "recorded release file is missing or is a symlink: ${AMIGO_CURRENT_RELEASE_FILE}"
    IFS= read -r release_sha <"${AMIGO_CURRENT_RELEASE_FILE}"
    [[ "${release_sha}" =~ ^[0-9a-f]{40,64}$ ]] \
        || amigo_die "recorded release file does not contain a valid Git SHA"
    printf '%s\n' "${release_sha}"
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

amigo_compose_file_release() {
    local compose_file=$1
    local image_tag=$2
    shift 2

    [[ -f "${compose_file}" && ! -L "${compose_file}" ]] \
        || amigo_die "Compose file is missing or is a symlink: ${compose_file}"
    [[ "${image_tag}" =~ ^[0-9a-f]{40,64}$ ]] \
        || amigo_die "invalid application image release SHA"
    AMIGO_IMAGE_TAG="${image_tag}" docker compose \
        --project-directory "${AMIGO_APP_DIR}" \
        --file "${compose_file}" \
        --env-file "${AMIGO_ENV_FILE}" \
        "$@"
}

amigo_compose_release() {
    local image_tag=$1
    shift
    amigo_compose_file_release "${AMIGO_COMPOSE_FILE}" "${image_tag}" "$@"
}

amigo_compose() {
    local image_tag
    image_tag=$(amigo_current_release)
    amigo_compose_release "${image_tag}" "$@"
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

amigo_snapshot_metadata_value() {
    local snapshot_path=$1
    local key=$2
    local metadata_file="${snapshot_path}/metadata.txt"
    local value

    [[ "${key}" =~ ^[a-z][a-z0-9_]*$ ]] \
        || amigo_die "invalid snapshot metadata key"
    [[ -f "${metadata_file}" && ! -L "${metadata_file}" ]] \
        || amigo_die "snapshot metadata is missing or is a symlink"
    value=$(awk -F= -v expected="${key}" '
        $1 == expected { count += 1; value = substr($0, length($1) + 2) }
        END { if (count != 1) exit 1; print value }
    ' "${metadata_file}") \
        || amigo_die "snapshot metadata must contain exactly one ${key} value"
    [[ -n "${value}" ]] || amigo_die "snapshot metadata value ${key} is empty"
    printf '%s\n' "${value}"
}

amigo_snapshot_metadata_optional() {
    local snapshot_path=$1
    local key=$2
    local metadata_file="${snapshot_path}/metadata.txt"
    local count

    [[ "${key}" =~ ^[a-z][a-z0-9_]*$ ]] \
        || amigo_die "invalid snapshot metadata key"
    [[ -f "${metadata_file}" && ! -L "${metadata_file}" ]] \
        || amigo_die "snapshot metadata is missing or is a symlink"
    count=$(awk -F= -v expected="${key}" '$1 == expected { count += 1 } END { print count + 0 }' \
        "${metadata_file}")
    [[ "${count}" -le 1 ]] \
        || amigo_die "snapshot metadata contains duplicate ${key} values"
    if [[ "${count}" -eq 1 ]]; then
        amigo_snapshot_metadata_value "${snapshot_path}" "${key}"
    fi
}

amigo_assert_image_revision() {
    local image_reference=$1
    local expected_release=$2
    local actual_release

    actual_release=$(docker image inspect --format \
        '{{if .Config.Labels}}{{index .Config.Labels "org.opencontainers.image.revision"}}{{end}}' \
        "${image_reference}") \
        || amigo_die "cannot inspect image revision: ${image_reference}"
    [[ "${actual_release}" == "${expected_release}" ]] \
        || amigo_die "image OCI revision does not match release ${expected_release}"
}

amigo_assert_managed_route_active() {
    [[ -f "${AMIGO_NGINX_CONFIG}" && ! -L "${AMIGO_NGINX_CONFIG}" ]] \
        || amigo_die "origin nginx config is missing or is a symlink"
    [[ "$(awk '
        /^[[:space:]]*# BEGIN AMIGO V2 ROUTE[[:space:]]*$/ { begin += 1 }
        /^[[:space:]]*include \/etc\/nginx\/snippets\/amigo-v2-locations[.]conf;[[:space:]]*$/ { include += 1 }
        /^[[:space:]]*# END AMIGO V2 ROUTE[[:space:]]*$/ { end += 1 }
        END { printf "%d:%d:%d", begin + 0, include + 0, end + 0 }
    ' "${AMIGO_NGINX_CONFIG}")" == "2:2:2" ]] \
        || amigo_die "managed Amigo route is not active"
}

amigo_assert_managed_route_inactive() {
    [[ -f "${AMIGO_NGINX_CONFIG}" && ! -L "${AMIGO_NGINX_CONFIG}" ]] \
        || amigo_die "origin nginx config is missing or is a symlink"
    [[ "$(awk '
        /^[[:space:]]*# BEGIN AMIGO V2 ROUTE[[:space:]]*$/ { begin += 1 }
        /^[[:space:]]*include \/etc\/nginx\/snippets\/amigo-v2-locations[.]conf;[[:space:]]*$/ { include += 1 }
        /^[[:space:]]*# END AMIGO V2 ROUTE[[:space:]]*$/ { end += 1 }
        END { printf "%d:%d:%d", begin + 0, include + 0, end + 0 }
    ' "${AMIGO_NGINX_CONFIG}")" == "0:0:0" ]] \
        || amigo_die "managed Amigo route is active or its markers are malformed"
}

amigo_assert_legacy_cron_disabled() {
    local crontab_state
    local status=0

    amigo_require_commands crontab mktemp rm
    crontab_state=$(mktemp /run/amigo-cron-state.XXXXXX) \
        || amigo_die "cannot create crontab state file"
    if ! crontab -u "${AMIGO_LEGACY_CRON_USER}" -l >"${crontab_state}"; then
        status=1
    elif [[ "$(amigo_count_exact_line "${AMIGO_LEGACY_WITHINGS_CRON_LINE}" \
        "${crontab_state}")" -ne 0 ]]; then
        status=1
    elif [[ "$(amigo_count_exact_line "${AMIGO_LEGACY_WITHINGS_DISABLED_LINE}" \
        "${crontab_state}")" -ne 1 ]]; then
        status=1
    elif [[ "$(amigo_count_exact_line "${AMIGO_SHARED_TELEGRAM_CRON_LINE}" \
        "${crontab_state}")" -lt 1 ]]; then
        status=1
    fi
    rm -f -- "${crontab_state}"
    [[ ${status} -eq 0 ]] \
        || amigo_die "legacy Withings cron is not provably disabled"
}

amigo_assert_release_rollback_compatible() {
    local repository=$1
    local previous_release=$2
    local candidate_release=$3
    local changed_path
    local -a protected_paths=(
        compose.yaml
        backend/alembic
        backend/app/ai_models.py
        backend/app/health_models.py
        backend/app/models.py
        deploy/nginx
        deploy/prepare-ai-runtime.sh
    )

    [[ "${previous_release}" =~ ^[0-9a-f]{40,64}$ ]] \
        || amigo_die "previous release is not a valid Git SHA"
    [[ "${candidate_release}" =~ ^[0-9a-f]{40,64}$ ]] \
        || amigo_die "candidate release is not a valid Git SHA"
    git -C "${repository}" cat-file -e "${previous_release}^{commit}" \
        || amigo_die "previous release Git object is unavailable: ${previous_release}"
    git -C "${repository}" cat-file -e "${candidate_release}^{commit}" \
        || amigo_die "candidate release Git object is unavailable: ${candidate_release}"
    if ! git -C "${repository}" diff --quiet \
        "${previous_release}" "${candidate_release}" -- "${protected_paths[@]}"; then
        changed_path=$(git -C "${repository}" diff --name-only \
            "${previous_release}" "${candidate_release}" -- "${protected_paths[@]}")
        changed_path=${changed_path%%$'\n'*}
        amigo_die "candidate changes rollback-protected schema/runtime path: ${changed_path}"
    fi
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

amigo_wait_for_origin_http_200() {
    local path=$1
    local attempts=${2:-15}
    local attempt
    local status=""

    if [[ "${path}" != "/amigo/" \
        && "${path}" != "/amigo/api/v1/overview" ]]; then
        amigo_die "invalid Amigo origin verification path"
        return 1
    fi
    if [[ ! "${attempts}" =~ ^[1-9][0-9]*$ || ${attempts} -gt 60 ]]; then
        amigo_die "invalid Amigo origin verification attempt bound"
        return 1
    fi
    for ((attempt = 1; attempt <= attempts; attempt += 1)); do
        status="$(
            curl --silent --show-error --max-time 5 \
                --header 'Host: amigo.tolstik.ru' \
                --output /dev/null --write-out '%{http_code}' \
                "http://127.0.0.1${path}" 2>/dev/null
        )" || status=""
        if [[ "${status}" == "200" ]]; then
            return 0
        fi
        [[ ${attempt} -lt ${attempts} ]] && sleep 2
    done
    return 1
}

amigo_handback_withings_tokens() {
    local compose_file=${1:-${AMIGO_COMPOSE_FILE}}
    local release_sha=${2:-}
    local handoff_dir
    local status=0

    if [[ -z "${release_sha}" ]]; then
        release_sha=$(amigo_current_release)
    fi
    amigo_require_commands chmod mktemp php rm rmdir
    [[ -f "${AMIGO_DEPLOY_DIR}/legacy-token-handback.php" ]] \
        || amigo_die "legacy token handback helper is missing"

    handoff_dir=$(mktemp -d /run/amigo-token-handoff.XXXXXX) \
        || amigo_die "cannot create token handoff directory"
    chmod 0700 "${handoff_dir}"

    if ! amigo_compose_file_release "${compose_file}" "${release_sha}" run --rm --no-deps \
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

amigo_revert_legacy_takeover() {
    local compose_file=$1
    local release_sha=$2
    local snapshot=$3
    local token_imported=$4
    local route_enable_started=$5
    local legacy_origin_was_healthy=$6
    local handback_ok=1
    local route_safe_for_legacy=${legacy_origin_was_healthy}
    local collectors_stopped=1

    [[ "${token_imported}" =~ ^[01]$ \
        && "${route_enable_started}" =~ ^[01]$ \
        && "${legacy_origin_was_healthy}" =~ ^[01]$ ]] \
        || amigo_die "invalid legacy takeover reversal state"

    if ! amigo_compose_file_release \
        "${compose_file}" "${release_sha}" stop worker ai-worker; then
        collectors_stopped=0
        amigo_log "CRITICAL: Amigo collector stop was not confirmed; legacy cron will remain disabled"
    fi
    if [[ ${token_imported} -eq 1 ]]; then
        if [[ ${collectors_stopped} -eq 0 ]]; then
            handback_ok=0
            amigo_log "OAuth handback skipped while the Amigo collector state is unconfirmed"
        else
            amigo_log "returning the latest PostgreSQL OAuth pair before legacy collection resumes"
            if ! amigo_handback_withings_tokens "${compose_file}" "${release_sha}"; then
                handback_ok=0
                amigo_log "WARNING: OAuth handback failed; both collectors will remain stopped"
            fi
        fi
    fi
    if [[ ${route_enable_started} -eq 1 && ${legacy_origin_was_healthy} -eq 0 ]]; then
        route_safe_for_legacy=0
        amigo_log "known-unhealthy legacy origin will not replace a started Amigo route"
    elif [[ ${route_enable_started} -eq 1 ]]; then
        amigo_log "returning the public route to legacy before stopping the Amigo web service"
        if ! bash "${AMIGO_DEPLOY_DIR}/nginx-control.sh" disable "${snapshot}"; then
            route_safe_for_legacy=0
        elif ! amigo_assert_managed_route_inactive; then
            route_safe_for_legacy=0
        else
            if ! amigo_wait_for_origin_http_200 "/amigo/" 15; then
                route_safe_for_legacy=0
            fi
        fi
    elif [[ ${legacy_origin_was_healthy} -eq 1 ]]; then
        if ! amigo_wait_for_origin_http_200 "/amigo/" 15; then
            route_safe_for_legacy=0
        fi
    fi
    if [[ ${route_safe_for_legacy} -eq 1 && ${collectors_stopped} -eq 1 ]]; then
        amigo_compose_file_release \
            "${compose_file}" "${release_sha}" stop web ingest ai-gateway db
        if [[ ${handback_ok} -eq 1 ]]; then
            bash "${AMIGO_DEPLOY_DIR}/cron-control.sh" enable \
                || amigo_log "WARNING: could not resume the exact legacy Withings cron"
        else
            bash "${AMIGO_DEPLOY_DIR}/cron-control.sh" disable \
                || amigo_log "WARNING: legacy cron state is ambiguous"
        fi
    elif [[ ${route_safe_for_legacy} -eq 0 ]]; then
        amigo_log "CRITICAL: legacy route restoration was not verified; keeping Amigo web/db running"
        amigo_log "legacy Withings cron remains disabled to avoid duplicate or stale-token collection"
        bash "${AMIGO_DEPLOY_DIR}/cron-control.sh" disable \
            || amigo_log "WARNING: legacy cron state is ambiguous"
    else
        amigo_log "CRITICAL: collector stop was not confirmed; keeping Amigo runtime available"
        amigo_log "legacy route may be active, but its Withings cron remains disabled"
        bash "${AMIGO_DEPLOY_DIR}/cron-control.sh" disable \
            || amigo_log "WARNING: legacy cron state is ambiguous"
    fi
}
