#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
# shellcheck source=lib/common.sh
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

usage() {
    cat >&2 <<'USAGE'
Usage: deploy.sh --send-telegram-test

The mandatory flag is explicit authorization to send one clearly labelled
pre-cutover Telegram smoke message. No notification is sent without it.
USAGE
    exit 2
}

[[ $# -eq 1 && $1 == "--send-telegram-test" ]] || usage

amigo_require_root
amigo_require_commands \
    bash chmod curl date docker flock git install mariadb mktemp mv nginx realpath stat
amigo_require_production_layout
amigo_acquire_deploy_lock

PROJECT_ROOT="$(realpath -e -- "${SCRIPT_DIR}/..")"
readonly PROJECT_ROOT
[[ "${PROJECT_ROOT}" == "${AMIGO_APP_DIR}" ]] \
    || amigo_die "deploy must run from the production checkout at ${AMIGO_APP_DIR}"
[[ -d "${AMIGO_SECRETS_DIR}" ]] \
    || amigo_die "secret directory is missing: ${AMIGO_SECRETS_DIR}"

assert_private_file() {
    local file=$1
    local mode
    local numeric_mode

    [[ -f "${file}" && ! -L "${file}" ]] || amigo_die "required private file is missing: ${file}"
    [[ -s "${file}" ]] || amigo_die "required private file is empty: ${file}"
    mode=$(stat -c '%a' "${file}")
    [[ "${mode}" =~ ^[0-7]{3,4}$ ]] || amigo_die "cannot validate mode for ${file}"
    numeric_mode=$((8#${mode}))
    (( (numeric_mode & 077) == 0 )) \
        || amigo_die "private file has group/world permissions: ${file} (${mode})"
}

assert_private_file "${AMIGO_ENV_FILE}"
for secret_name in \
    postgres_password \
    app_encryption_key \
    withings_client_id \
    withings_client_secret \
    withings_access_token \
    withings_refresh_token \
    telegram_bot_token \
    telegram_chat_id; do
    assert_private_file "${AMIGO_SECRETS_DIR}/${secret_name}"
done

[[ -z "$(git -C "${AMIGO_APP_DIR}" status --porcelain)" ]] \
    || amigo_die "production checkout is dirty; deploy an exact committed revision"
RELEASE_SHA="$(git -C "${AMIGO_APP_DIR}" rev-parse HEAD)"
readonly RELEASE_SHA
export AMIGO_IMAGE_TAG="${RELEASE_SHA}"
amigo_log "candidate Git SHA: ${RELEASE_SHA}"

[[ ! -L "${AMIGO_APP_DIR}/data" && ! -L "${AMIGO_IMPORT_DIR}" ]] \
    || amigo_die "application data/import paths must not be symlinks"
install -d -o root -g root -m 0700 \
    "${AMIGO_APP_DIR}/data" "${AMIGO_IMPORT_DIR}"

amigo_compose config --quiet
nginx -t >/dev/null

SNAPSHOT=""
CUTOVER_STARTED=0
CUTOVER_COMMITTED=0
REHEARSAL_ROUTE_DISABLED=0

deploy_error() {
    local status=$1
    local line=$2
    trap - ERR
    trap '' HUP INT TERM
    set +e
    amigo_log "deployment failed at line ${line} (status ${status})"
    if [[ ${REHEARSAL_ROUTE_DISABLED} -eq 1 && -n "${SNAPSHOT}" ]]; then
        amigo_log "restoring the verified v2 route after an interrupted rollback rehearsal"
        if bash "${SCRIPT_DIR}/nginx-control.sh" enable "${SNAPSHOT}"; then
            REHEARSAL_ROUTE_DISABLED=0
        else
            amigo_log "WARNING: could not restore the v2 route before automatic rollback"
        fi
    fi
    if [[ ${CUTOVER_COMMITTED} -eq 1 ]]; then
        amigo_log "runtime cutover remains healthy; finish the release-state/checkpoint step manually"
    elif [[ ${CUTOVER_STARTED} -eq 1 && -n "${SNAPSHOT}" ]]; then
        amigo_log "starting automatic rollback to the verified legacy route"
        AMIGO_DEPLOY_LOCK_HELD=1 bash "${SCRIPT_DIR}/rollback.sh" "${SNAPSHOT}"
        rollback_status=$?
        if [[ ${rollback_status} -ne 0 ]]; then
            amigo_log "AUTOMATIC ROLLBACK FAILED; use: sudo ${SCRIPT_DIR}/rollback.sh ${SNAPSHOT}"
        fi
    elif [[ -n "${SNAPSHOT}" ]]; then
        amigo_log "legacy production route was not changed; snapshot is ${SNAPSHOT}"
    fi
    exit "${status}"
}
trap 'deploy_error "$?" "${LINENO}"' ERR
trap 'deploy_error 129 "${LINENO}"' HUP
trap 'deploy_error 130 "${LINENO}"' INT
trap 'deploy_error 143 "${LINENO}"' TERM

SNAPSHOT="$(AMIGO_DEPLOY_LOCK_HELD=1 bash "${SCRIPT_DIR}/pre-cutover-backup.sh")"
amigo_assert_snapshot "${SNAPSHOT}"

amigo_log "pulling PostgreSQL and building immutable application images"
amigo_compose pull db
amigo_compose build --pull web

amigo_log "starting PostgreSQL"
amigo_compose up -d db
for attempt in {1..60}; do
    if amigo_compose exec -T db pg_isready -U amigo -d amigo >/dev/null 2>&1; then
        break
    fi
    [[ ${attempt} -lt 60 ]] || amigo_die "PostgreSQL did not become ready"
    sleep 2
done

amigo_log "running schema migration and idempotent bootstrap"
amigo_compose run --rm --no-deps worker python -m app.cli migrate
amigo_compose run --rm --no-deps worker python -m app.cli bootstrap

amigo_log "sending the explicitly authorized, labelled Telegram smoke message"
amigo_compose run --rm --no-deps worker python -m app.cli telegram-test

amigo_log "transferring Withings collection ownership away from the legacy cron"
CUTOVER_STARTED=1
bash "${SCRIPT_DIR}/cron-control.sh" disable

amigo_log "performing full import with historical notifications suppressed"
amigo_compose run --rm --no-deps worker \
    python -m app.cli sync --full --suppress-notifications

amigo_log "refreshing the rollback collector with the current OAuth token pair"
amigo_handback_withings_tokens

amigo_log "exporting the preserved legacy weight rows to a root-only TSV"
LEGACY_IMPORT_CANDIDATE="$(mktemp "${AMIGO_IMPORT_DIR}/.legacy-weight.tsv.XXXXXX")"
readonly LEGACY_IMPORT_CANDIDATE
chmod 0600 "${LEGACY_IMPORT_CANDIDATE}"
mariadb --batch --skip-column-names "${AMIGO_LEGACY_DB}" \
    --execute 'SELECT date_creat, weight FROM weight ORDER BY date_creat' \
    >"${LEGACY_IMPORT_CANDIDATE}"
[[ -s "${LEGACY_IMPORT_CANDIDATE}" ]] \
    || amigo_die "legacy MariaDB weight export is empty"
if [[ -e "${AMIGO_LEGACY_WEIGHT_IMPORT}" ]]; then
    PRESERVED_IMPORT="${AMIGO_LEGACY_WEIGHT_IMPORT}.previous.$(date -u +%Y%m%dT%H%M%SZ)"
    readonly PRESERVED_IMPORT
    [[ ! -e "${PRESERVED_IMPORT}" ]] \
        || amigo_die "refusing to overwrite preserved legacy TSV: ${PRESERVED_IMPORT}"
    mv -- "${AMIGO_LEGACY_WEIGHT_IMPORT}" "${PRESERVED_IMPORT}"
fi
mv -- "${LEGACY_IMPORT_CANDIDATE}" "${AMIGO_LEGACY_WEIGHT_IMPORT}"
chmod 0600 "${AMIGO_LEGACY_WEIGHT_IMPORT}"

amigo_log "merging legacy-only weight rows without creating notifications"
amigo_compose run --rm --no-deps --user 0 worker \
    python -m app.cli legacy-weight-import \
        --file /imports/legacy-weight.tsv \
        --timezone UTC \
        --scale 0.001

amigo_log "starting web without worker and checking the loopback-only endpoint"
amigo_compose up -d web
amigo_wait_for_http "${AMIGO_DIRECT_HEALTH_URL}" 60 \
    || amigo_die "web health endpoint did not become ready"

bash "${SCRIPT_DIR}/nginx-control.sh" enable "${SNAPSHOT}"
curl --fail --silent --show-error --max-time 15 \
    --header 'Host: amigo.tolstik.ru' \
    --output /dev/null \
    http://127.0.0.1/amigo/

amigo_log "starting the v2 worker after legacy collection is disabled"
amigo_compose up -d --wait --wait-timeout 180 worker
bash "${SCRIPT_DIR}/verify-production.sh"

amigo_log "rehearsing the route-only rollback while keeping all data intact"
REHEARSAL_ROUTE_DISABLED=1
bash "${SCRIPT_DIR}/nginx-control.sh" disable "${SNAPSHOT}"
REHEARSAL_LEGACY_STATUS="$(
    curl --silent --show-error --max-time 15 \
        --header 'Host: amigo.tolstik.ru' \
        --output /dev/null \
        --write-out '%{http_code}' \
        http://127.0.0.1/amigo/
)"
readonly REHEARSAL_LEGACY_STATUS
[[ "${REHEARSAL_LEGACY_STATUS}" == "200" ]] \
    || amigo_die "route rollback rehearsal did not expose the preserved legacy dashboard"
bash "${SCRIPT_DIR}/nginx-control.sh" enable "${SNAPSHOT}"
REHEARSAL_ROUTE_DISABLED=0
bash "${SCRIPT_DIR}/verify-production.sh"
CUTOVER_COMMITTED=1
amigo_record_current_release "${RELEASE_SHA}"

amigo_log "runtime cutover passed; writing mandatory documentation and memory checkpoint"
bash "${SCRIPT_DIR}/checkpoint.sh" "${SNAPSHOT}"

trap - ERR HUP INT TERM
amigo_log "DEPLOYMENT COMPLETE: ${AMIGO_PUBLIC_URL}"
amigo_log "Git SHA: ${RELEASE_SHA}"
amigo_log "rollback snapshot: ${SNAPSHOT}"
amigo_log "checkpoint files must be committed back to the canonical repository"
