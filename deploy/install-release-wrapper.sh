#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

readonly APP_DIR="/srv/amigo"
readonly SOURCE_WRAPPER="${APP_DIR}/deploy/amigo-release"
readonly SOURCE_SUDOERS="${APP_DIR}/deploy/sudoers/amigo-release"
readonly INSTALLED_WRAPPER="/usr/local/sbin/amigo-release"
readonly INSTALLED_SUDOERS="/etc/sudoers.d/amigo-release"

[[ ${EUID} -eq 0 ]] || {
    printf 'install-release-wrapper.sh must run as root\n' >&2
    exit 1
}
for source_file in "${SOURCE_WRAPPER}" "${SOURCE_SUDOERS}"; do
    [[ -f "${source_file}" && ! -L "${source_file}" ]] || {
        printf 'release access source is missing or is a symlink: %s\n' \
            "${source_file}" >&2
        exit 1
    }
    [[ "$(stat -c '%U:%G' "${source_file}")" == "root:root" ]] || {
        printf 'release access source must be owned by root:root: %s\n' \
            "${source_file}" >&2
        exit 1
    }
done

command -v install >/dev/null 2>&1
command -v visudo >/dev/null 2>&1
install -d -o root -g root -m 0755 /usr/local/sbin
install -d -o root -g root -m 0750 /etc/sudoers.d

SUDOERS_CANDIDATE="$(mktemp /etc/sudoers.d/.amigo-release.XXXXXX)"
readonly SUDOERS_CANDIDATE
cleanup() {
    rm -f -- "${SUDOERS_CANDIDATE}"
}
trap cleanup EXIT
install -o root -g root -m 0440 "${SOURCE_SUDOERS}" "${SUDOERS_CANDIDATE}"
visudo -cf "${SUDOERS_CANDIDATE}" >/dev/null

install -o root -g root -m 0755 "${SOURCE_WRAPPER}" "${INSTALLED_WRAPPER}"
mv -- "${SUDOERS_CANDIDATE}" "${INSTALLED_SUDOERS}"
trap - EXIT
visudo -cf /etc/sudoers >/dev/null

cmp --silent "${SOURCE_WRAPPER}" "${INSTALLED_WRAPPER}"
cmp --silent "${SOURCE_SUDOERS}" "${INSTALLED_SUDOERS}"
printf 'installed least-privilege Amigo release access for tolstik\n'
