#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
# shellcheck source=lib/common.sh
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

usage() {
    printf 'Usage: %s /srv/amigo-rollbacks/YYYYMMDDTHHMMSSZ\n' "${0##*/}" >&2
    exit 2
}

[[ $# -eq 1 ]] || usage
readonly SNAPSHOT=$1

amigo_require_root
amigo_require_commands \
    awk bash chmod crontab curl date docker flock git grep install nginx realpath sha256sum
amigo_require_production_layout
amigo_assert_snapshot "${SNAPSHOT}"
amigo_acquire_deploy_lock

WORKER_STOPPED=0
WORKER_WAS_RUNNING=0
ROUTE_DISABLED=0
ROUTE_WAS_ACTIVE=0
ROLLBACK_COMMITTED=0
ROLLBACK_CRON_ENABLE_STARTED=0

worker_container=$(amigo_compose ps -q worker)
if [[ -n "${worker_container}" ]] \
    && [[ "$(docker inspect --format '{{.State.Status}}' "${worker_container}")" == "running" ]]; then
    WORKER_WAS_RUNNING=1
fi
if [[ "$(grep -Ec '^[[:space:]]*# BEGIN AMIGO V2 ROUTE[[:space:]]*$' "${AMIGO_NGINX_CONFIG}")" -eq 2 ]]; then
    ROUTE_WAS_ACTIVE=1
fi

rollback_error() {
    local status=$1
    local line=$2
    local cron_state=""
    trap - ERR
    trap '' HUP INT TERM
    set +e
    amigo_log "rollback failed at line ${line} (status ${status})"
    if [[ ${ROLLBACK_CRON_ENABLE_STARTED} -eq 1 && ${ROLLBACK_COMMITTED} -eq 0 ]]; then
        cron_state="$(
            crontab -u "${AMIGO_LEGACY_CRON_USER}" -l 2>/dev/null \
                | awk \
                    -v active="${AMIGO_LEGACY_WITHINGS_CRON_LINE}" \
                    -v disabled="${AMIGO_LEGACY_WITHINGS_DISABLED_LINE}" \
                    '$0 == active { a += 1 } $0 == disabled { d += 1 } END { printf "%d:%d", a + 0, d + 0 }'
        )"
        if [[ "${cron_state}" == "1:0" ]]; then
            ROLLBACK_COMMITTED=1
            amigo_log "legacy cron activation completed before interruption; keeping the v2 worker stopped"
        elif [[ "${cron_state}" != "0:1" ]]; then
            ROLLBACK_COMMITTED=1
            amigo_log "WARNING: legacy cron state is ambiguous; keeping the v2 worker stopped to prevent duplicate collection"
        fi
    fi
    if [[ ${ROLLBACK_COMMITTED} -eq 0 ]]; then
        if [[ ${ROUTE_DISABLED} -eq 1 && ${ROUTE_WAS_ACTIVE} -eq 1 ]]; then
            amigo_log "restoring the v2 nginx route after the failed rollback"
            bash "${SCRIPT_DIR}/nginx-control.sh" enable "${SNAPSHOT}"
        fi
        if [[ ${WORKER_STOPPED} -eq 1 && ${WORKER_WAS_RUNNING} -eq 1 ]]; then
            amigo_log "restarting the v2 worker after the failed rollback"
            amigo_compose start worker
        fi
    else
        amigo_log "legacy route and collector are committed; keep the v2 worker stopped"
    fi
    exit "${status}"
}
trap 'rollback_error "$?" "${LINENO}"' ERR
trap 'rollback_error 129 "${LINENO}"' HUP
trap 'rollback_error 130 "${LINENO}"' INT
trap 'rollback_error 143 "${LINENO}"' TERM

amigo_log "stopping the v2 worker before changing collection ownership"
WORKER_STOPPED=1
amigo_compose stop worker

amigo_log "switching nginx back to the preserved legacy application"
ROUTE_DISABLED=1
bash "${SCRIPT_DIR}/nginx-control.sh" disable "${SNAPSHOT}"

LEGACY_ORIGIN_STATUS="$(
    curl --silent --show-error --max-time 15 \
        --header 'Host: amigo.tolstik.ru' \
        --output /dev/null \
        --write-out '%{http_code}' \
        http://127.0.0.1/amigo/
)"
readonly LEGACY_ORIGIN_STATUS
[[ "${LEGACY_ORIGIN_STATUS}" == "200" ]] \
    || amigo_die "legacy origin route returned ${LEGACY_ORIGIN_STATUS}"

amigo_log "returning the current Withings OAuth token pair to the legacy collector"
amigo_handback_withings_tokens

amigo_log "re-enabling only the exact legacy Withings cron line"
ROLLBACK_CRON_ENABLE_STARTED=1
bash "${SCRIPT_DIR}/cron-control.sh" enable
ROLLBACK_COMMITTED=1

amigo_log "stopping the remaining v2 services without removing containers, images, or volumes"
amigo_compose stop web db

install -d -o root -g root -m 0700 \
    "${AMIGO_STATE_DIR}" "${AMIGO_STATE_DIR}/rollbacks"
ROLLED_BACK_AT="$(date -u +%Y%m%dT%H%M%SZ)"
readonly ROLLED_BACK_AT
{
    printf 'rolled_back_at_utc=%s\n' "${ROLLED_BACK_AT}"
    printf 'snapshot=%s\n' "${SNAPSHOT}"
    printf 'legacy_origin_status=%s\n' "${LEGACY_ORIGIN_STATUS}"
    printf 'legacy_withings_cron=enabled\n'
    printf 'shared_send_telergam_cron=unchanged\n'
    printf 'new_postgres_data=preserved\n'
} >"${AMIGO_STATE_DIR}/rollbacks/${ROLLED_BACK_AT}.txt"
chmod 0600 "${AMIGO_STATE_DIR}/rollbacks/${ROLLED_BACK_AT}.txt"

trap - ERR HUP INT TERM
amigo_log "ROLLBACK COMPLETE: legacy route and exact Withings cron are active"
amigo_log "new Compose data was preserved; recovery snapshot: ${SNAPSHOT}"
