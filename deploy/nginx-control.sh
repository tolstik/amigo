#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
# shellcheck source=lib/common.sh
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

usage() {
    printf 'Usage: %s enable|disable|restore /srv/amigo-rollbacks/YYYYMMDDTHHMMSSZ\n' "${0##*/}" >&2
    exit 2
}

[[ $# -eq 2 ]] || usage
readonly ACTION=$1
readonly SNAPSHOT=$2
[[ "${ACTION}" == "enable" || "${ACTION}" == "disable" || "${ACTION}" == "restore" ]] \
    || usage

amigo_require_root
amigo_require_commands \
    chmod chown cp flock install mktemp mv nginx python3 realpath rm sha256sum systemctl
amigo_assert_snapshot "${SNAPSHOT}"
[[ -f "${AMIGO_NGINX_CONFIG}" ]] \
    || amigo_die "origin nginx config is missing: ${AMIGO_NGINX_CONFIG}"
[[ -f "${SCRIPT_DIR}/nginx/amigo.locations.conf" ]] \
    || amigo_die "repository nginx snippet is missing"
[[ -f "${SCRIPT_DIR}/nginx/amigo.http.conf" ]] \
    || amigo_die "repository nginx HTTP configuration is missing"
[[ -f "${SCRIPT_DIR}/nginx/route_config.py" ]] \
    || amigo_die "nginx route transformer is missing"
if [[ "${ACTION}" == "restore" ]]; then
    for restore_file in \
        "${SNAPSHOT}/nginx/amigo-v2-locations.conf" \
        "${SNAPSHOT}/nginx/amigo-v2-http.conf"; do
        [[ -f "${restore_file}" && ! -L "${restore_file}" ]] \
            || amigo_die "snapshot managed nginx file is missing or is a symlink: ${restore_file}"
    done
fi
[[ ! -e "${AMIGO_NGINX_SNIPPET}" || ( -f "${AMIGO_NGINX_SNIPPET}" && ! -L "${AMIGO_NGINX_SNIPPET}" ) ]] \
    || amigo_die "installed nginx snippet is not a regular file"
[[ ! -e "${AMIGO_NGINX_HTTP_CONFIG}" || ( -f "${AMIGO_NGINX_HTTP_CONFIG}" && ! -L "${AMIGO_NGINX_HTTP_CONFIG}" ) ]] \
    || amigo_die "installed nginx HTTP config is not a regular file"

exec 7>"/run/lock/amigo-nginx.lock"
flock -n 7 || amigo_die "another Amigo nginx operation is running"

ORIGINAL_FILE="$(mktemp /etc/nginx/conf.d/my.conf.amigo-original.XXXXXX)"
ORIGINAL_SNIPPET="$(mktemp /etc/nginx/snippets/amigo-v2-locations-original.XXXXXX)"
ORIGINAL_HTTP_CONFIG="$(mktemp /etc/nginx/conf.d/amigo-v2-http-original.XXXXXX)"
CANDIDATE_FILE="$(mktemp /etc/nginx/conf.d/my.conf.amigo-candidate.XXXXXX)"
SNIPPET_CANDIDATE="$(mktemp /etc/nginx/snippets/amigo-v2-locations.XXXXXX)"
HTTP_CANDIDATE="$(mktemp /etc/nginx/conf.d/amigo-v2-http.XXXXXX)"
readonly ORIGINAL_FILE ORIGINAL_SNIPPET ORIGINAL_HTTP_CONFIG
readonly CANDIDATE_FILE SNIPPET_CANDIDATE HTTP_CANDIDATE
SNIPPET_WAS_PRESENT=0
HTTP_CONFIG_WAS_PRESENT=0
NGINX_TRANSACTION_STARTED=0
NGINX_TRANSACTION_COMMITTED=0

cleanup() {
    rm -f -- \
        "${ORIGINAL_FILE}" \
        "${ORIGINAL_SNIPPET}" \
        "${ORIGINAL_HTTP_CONFIG}" \
        "${CANDIDATE_FILE}" \
        "${SNIPPET_CANDIDATE}" \
        "${HTTP_CANDIDATE}"
}
trap cleanup EXIT

cp --preserve=all -- "${AMIGO_NGINX_CONFIG}" "${ORIGINAL_FILE}"
if [[ -f "${AMIGO_NGINX_SNIPPET}" ]]; then
    cp --preserve=all -- "${AMIGO_NGINX_SNIPPET}" "${ORIGINAL_SNIPPET}"
    SNIPPET_WAS_PRESENT=1
fi
if [[ -f "${AMIGO_NGINX_HTTP_CONFIG}" ]]; then
    cp --preserve=all -- "${AMIGO_NGINX_HTTP_CONFIG}" "${ORIGINAL_HTTP_CONFIG}"
    HTTP_CONFIG_WAS_PRESENT=1
fi

restore_original() {
    local reason=$1
    local restore_status=0

    amigo_log "${reason}; restoring the pre-operation nginx files"
    mv -- "${ORIGINAL_FILE}" "${AMIGO_NGINX_CONFIG}" || restore_status=1
    if [[ ${SNIPPET_WAS_PRESENT} -eq 1 ]]; then
        mv -- "${ORIGINAL_SNIPPET}" "${AMIGO_NGINX_SNIPPET}" || restore_status=1
    else
        rm -f -- "${AMIGO_NGINX_SNIPPET}" || restore_status=1
    fi
    if [[ ${HTTP_CONFIG_WAS_PRESENT} -eq 1 ]]; then
        mv -- "${ORIGINAL_HTTP_CONFIG}" "${AMIGO_NGINX_HTTP_CONFIG}" || restore_status=1
    else
        rm -f -- "${AMIGO_NGINX_HTTP_CONFIG}" || restore_status=1
    fi
    nginx -t >/dev/null || restore_status=1
    systemctl reload nginx || restore_status=1
    if [[ ${restore_status} -ne 0 ]]; then
        amigo_log "WARNING: pre-operation nginx files could not be fully restored or reloaded"
    fi
}

abort_transaction() {
    local status=$1
    local reason=$2

    trap - ERR
    trap '' HUP INT TERM
    set +e
    if [[ ${NGINX_TRANSACTION_STARTED} -eq 1 && ${NGINX_TRANSACTION_COMMITTED} -eq 0 ]]; then
        restore_original "${reason}"
    fi
    exit "${status}"
}

trap 'abort_transaction "$?" "nginx route operation failed at line ${LINENO}"' ERR
trap 'abort_transaction 129 "nginx route operation received SIGHUP"' HUP
trap 'abort_transaction 130 "nginx route operation received SIGINT"' INT
trap 'abort_transaction 143 "nginx route operation received SIGTERM"' TERM

if [[ "${ACTION}" == "restore" ]]; then
    # The origin file is shared with unrelated services. Rebuild only the
    # Amigo-owned marker region against the current file so a later manual
    # recovery cannot erase unrelated nginx changes made after the snapshot.
    python3 "${SCRIPT_DIR}/nginx/route_config.py" enable \
        <"${AMIGO_NGINX_CONFIG}" >"${CANDIDATE_FILE}"
    install -o root -g root -m 0644 \
        "${SNAPSHOT}/nginx/amigo-v2-locations.conf" "${SNIPPET_CANDIDATE}"
    install -o root -g root -m 0644 \
        "${SNAPSHOT}/nginx/amigo-v2-http.conf" "${HTTP_CANDIDATE}"
else
    python3 "${SCRIPT_DIR}/nginx/route_config.py" "${ACTION}" \
        <"${AMIGO_NGINX_CONFIG}" >"${CANDIDATE_FILE}"
    install -o root -g root -m 0644 \
        "${SCRIPT_DIR}/nginx/amigo.locations.conf" "${SNIPPET_CANDIDATE}"
    install -o root -g root -m 0644 \
        "${SCRIPT_DIR}/nginx/amigo.http.conf" "${HTTP_CANDIDATE}"
fi
NGINX_TRANSACTION_STARTED=1
mv -- "${SNIPPET_CANDIDATE}" "${AMIGO_NGINX_SNIPPET}"
mv -- "${HTTP_CANDIDATE}" "${AMIGO_NGINX_HTTP_CONFIG}"
chown root:root "${CANDIDATE_FILE}"
chmod 0644 "${CANDIDATE_FILE}"
mv -- "${CANDIDATE_FILE}" "${AMIGO_NGINX_CONFIG}"

nginx -t >/dev/null
systemctl reload nginx
NGINX_TRANSACTION_COMMITTED=1
trap - ERR HUP INT TERM

if [[ "${ACTION}" == "restore" ]]; then
    amigo_log "pre-cutover Amigo snippets restored without replacing shared nginx content"
else
    amigo_log "nginx Amigo route ${ACTION}d and configuration reload succeeded"
fi
