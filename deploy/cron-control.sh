#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
# shellcheck source=lib/common.sh
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

usage() {
    printf 'Usage: %s disable|enable\n' "${0##*/}" >&2
    exit 2
}

[[ $# -eq 1 ]] || usage
readonly ACTION=$1
[[ "${ACTION}" == "disable" || "${ACTION}" == "enable" ]] || usage

amigo_require_root
amigo_require_commands awk cmp crontab flock mktemp rm

exec 8>"/run/lock/amigo-cron.lock"
flock -n 8 || amigo_die "another Amigo cron operation is running"

CURRENT_FILE="$(mktemp /run/amigo-crontab-current.XXXXXX)"
UPDATED_FILE="$(mktemp /run/amigo-crontab-updated.XXXXXX)"
INSTALLED_FILE="$(mktemp /run/amigo-crontab-installed.XXXXXX)"
readonly CURRENT_FILE UPDATED_FILE INSTALLED_FILE
CRONTAB_INSTALLED=0

cleanup() {
    rm -f -- "${CURRENT_FILE}" "${UPDATED_FILE}" "${INSTALLED_FILE}"
}
trap cleanup EXIT

restore_crontab_on_error() {
    local status=$1
    trap - ERR
    trap '' HUP INT TERM
    set +e
    if [[ ${CRONTAB_INSTALLED} -eq 1 ]]; then
        amigo_log "cron validation failed after install; restoring the captured crontab"
        crontab -u "${AMIGO_LEGACY_CRON_USER}" "${CURRENT_FILE}" \
            || amigo_log "WARNING: failed to restore the captured crontab"
    fi
    exit "${status}"
}
trap 'restore_crontab_on_error "$?"' ERR
trap 'restore_crontab_on_error 129' HUP
trap 'restore_crontab_on_error 130' INT
trap 'restore_crontab_on_error 143' TERM

crontab -u "${AMIGO_LEGACY_CRON_USER}" -l >"${CURRENT_FILE}" \
    || amigo_die "cannot read crontab for ${AMIGO_LEGACY_CRON_USER}"

SHARED_COUNT_BEFORE="$(amigo_count_exact_line "${AMIGO_SHARED_TELEGRAM_CRON_LINE}" "${CURRENT_FILE}")"
ACTIVE_COUNT="$(amigo_count_exact_line "${AMIGO_LEGACY_WITHINGS_CRON_LINE}" "${CURRENT_FILE}")"
DISABLED_COUNT="$(amigo_count_exact_line "${AMIGO_LEGACY_WITHINGS_DISABLED_LINE}" "${CURRENT_FILE}")"
readonly SHARED_COUNT_BEFORE ACTIVE_COUNT DISABLED_COUNT

[[ "${SHARED_COUNT_BEFORE}" -ge 1 ]] \
    || amigo_die "shared send_telergam cron line is missing; refusing to alter this crontab"

if [[ "${ACTION}" == "disable" ]]; then
    if [[ "${ACTIVE_COUNT}" -eq 0 && "${DISABLED_COUNT}" -eq 1 ]]; then
        amigo_log "legacy Withings cron line is already disabled"
        exit 0
    fi
    [[ "${ACTIVE_COUNT}" -eq 1 && "${DISABLED_COUNT}" -eq 0 ]] \
        || amigo_die "expected exactly one active legacy Withings line and no disabled marker"
    awk \
        -v expected="${AMIGO_LEGACY_WITHINGS_CRON_LINE}" \
        -v replacement="${AMIGO_LEGACY_WITHINGS_DISABLED_LINE}" \
        '{ print ($0 == expected) ? replacement : $0 }' \
        "${CURRENT_FILE}" >"${UPDATED_FILE}"
else
    if [[ "${ACTIVE_COUNT}" -eq 1 && "${DISABLED_COUNT}" -eq 0 ]]; then
        amigo_log "legacy Withings cron line is already enabled"
        exit 0
    fi
    [[ "${ACTIVE_COUNT}" -eq 0 && "${DISABLED_COUNT}" -eq 1 ]] \
        || amigo_die "expected exactly one disabled legacy Withings marker and no active line"
    awk \
        -v expected="${AMIGO_LEGACY_WITHINGS_DISABLED_LINE}" \
        -v replacement="${AMIGO_LEGACY_WITHINGS_CRON_LINE}" \
        '{ print ($0 == expected) ? replacement : $0 }' \
        "${CURRENT_FILE}" >"${UPDATED_FILE}"
fi

[[ "$(amigo_count_exact_line "${AMIGO_SHARED_TELEGRAM_CRON_LINE}" "${UPDATED_FILE}")" -eq "${SHARED_COUNT_BEFORE}" ]] \
    || amigo_die "shared send_telergam cron line would change; refusing to install"

CRONTAB_INSTALLED=1
crontab -u "${AMIGO_LEGACY_CRON_USER}" "${UPDATED_FILE}"
crontab -u "${AMIGO_LEGACY_CRON_USER}" -l >"${INSTALLED_FILE}"
cmp --silent "${UPDATED_FILE}" "${INSTALLED_FILE}" \
    || amigo_die "installed crontab differs from the validated candidate"

[[ "$(amigo_count_exact_line "${AMIGO_SHARED_TELEGRAM_CRON_LINE}" "${INSTALLED_FILE}")" -eq "${SHARED_COUNT_BEFORE}" ]] \
    || amigo_die "shared send_telergam cron line changed unexpectedly"

if [[ "${ACTION}" == "disable" ]]; then
    [[ "$(amigo_count_exact_line "${AMIGO_LEGACY_WITHINGS_CRON_LINE}" "${INSTALLED_FILE}")" -eq 0 ]]
    [[ "$(amigo_count_exact_line "${AMIGO_LEGACY_WITHINGS_DISABLED_LINE}" "${INSTALLED_FILE}")" -eq 1 ]]
else
    [[ "$(amigo_count_exact_line "${AMIGO_LEGACY_WITHINGS_CRON_LINE}" "${INSTALLED_FILE}")" -eq 1 ]]
    [[ "$(amigo_count_exact_line "${AMIGO_LEGACY_WITHINGS_DISABLED_LINE}" "${INSTALLED_FILE}")" -eq 0 ]]
fi

CRONTAB_INSTALLED=0
trap - ERR HUP INT TERM
amigo_log "legacy Withings cron line ${ACTION}d; shared send_telergam job is unchanged"
