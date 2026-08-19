#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
# shellcheck source=lib/common.sh
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

usage() {
    echo "Usage: prepare-ai-runtime.sh [--refresh-auth]" >&2
    exit 2
}

REFRESH_AUTH=0
if [[ $# -eq 1 && $1 == "--refresh-auth" ]]; then
    REFRESH_AUTH=1
elif [[ $# -ne 0 ]]; then
    usage
fi

amigo_require_root
amigo_require_commands awk install python3 sha256sum stat
amigo_require_production_layout

readonly CODEX_SOURCE_BINARY="${AMIGO_CODEX_SOURCE_BINARY:-/home/tolstik/.codex/packages/standalone/releases/0.148.0-x86_64-unknown-linux-musl/bin/codex}"
readonly CODEX_SOURCE_AUTH="${AMIGO_CODEX_SOURCE_AUTH:-/home/tolstik/.codex/auth.json}"
readonly CODEX_EXPECTED_SHA256="ac2cfed85fb647d61e0150b8548102b330e4799d9d81ad5d354de701edf6b074"
readonly CODEX_RUNTIME_DIR="${AMIGO_APP_DIR}/data/codex-bin"
readonly CODEX_STATE_DIR="${AMIGO_APP_DIR}/data/codex-state"
readonly CODEX_RUNTIME_BINARY="${CODEX_RUNTIME_DIR}/codex"
readonly CODEX_RUNTIME_AUTH="${CODEX_STATE_DIR}/auth.json"
readonly RUNTIME_UID=65532
readonly RUNTIME_GID=65532

[[ -f "${CODEX_SOURCE_BINARY}" && ! -L "${CODEX_SOURCE_BINARY}" ]] \
    || amigo_die "pinned Codex source binary is missing"
actual_source_hash="$(sha256sum "${CODEX_SOURCE_BINARY}" | awk '{print $1}')"
[[ "${actual_source_hash}" == "${CODEX_EXPECTED_SHA256}" ]] \
    || amigo_die "Codex source binary hash differs from the pinned release"

install -d -o "${RUNTIME_UID}" -g "${RUNTIME_GID}" -m 0700 \
    "${CODEX_RUNTIME_DIR}" "${CODEX_STATE_DIR}"
install -o "${RUNTIME_UID}" -g "${RUNTIME_GID}" -m 0555 \
    "${CODEX_SOURCE_BINARY}" "${CODEX_RUNTIME_BINARY}"

if [[ ${REFRESH_AUTH} -eq 1 || ! -s "${CODEX_RUNTIME_AUTH}" ]]; then
    [[ -f "${CODEX_SOURCE_AUTH}" && ! -L "${CODEX_SOURCE_AUTH}" ]] \
        || amigo_die "Codex auth source is missing; run codex login as the service owner first"
    python3 - "${CODEX_SOURCE_AUTH}" <<'PY'
from pathlib import Path
import json
import sys

value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if not isinstance(value, dict) or not value:
    raise SystemExit("Codex auth source is not a non-empty JSON object")
PY
    install -o "${RUNTIME_UID}" -g "${RUNTIME_GID}" -m 0600 \
        "${CODEX_SOURCE_AUTH}" "${CODEX_RUNTIME_AUTH}"
fi

runtime_hash="$(sha256sum "${CODEX_RUNTIME_BINARY}" | awk '{print $1}')"
[[ "${runtime_hash}" == "${CODEX_EXPECTED_SHA256}" ]] \
    || amigo_die "installed Codex binary hash validation failed"
[[ "$(stat -c '%a' "${CODEX_RUNTIME_AUTH}")" == "600" ]] \
    || amigo_die "runtime Codex auth has unsafe permissions"

amigo_log "Codex runtime prepared: version 0.148.0, binary hash verified, auth kept private"
