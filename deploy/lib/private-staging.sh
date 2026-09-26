#!/usr/bin/env bash
# Identity checks for explicitly staged private files. Call with fixed deploy paths.

amigo_private_staging_identity() {
    local path=$1
    [[ -f "${path}" && ! -L "${path}" ]] || return 1
    stat -c '%d:%i' -- "${path}"
}

amigo_remove_private_staging_if_same() {
    local path=$1
    local expected_identity=$2
    local current_identity

    current_identity="$(amigo_private_staging_identity "${path}")" || return 1
    [[ "${current_identity}" == "${expected_identity}" ]] || return 1
    rm -- "${path}" || return 2
}
