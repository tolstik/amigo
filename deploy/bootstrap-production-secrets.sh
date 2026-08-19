#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
# shellcheck source=lib/common.sh
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

amigo_require_root
amigo_require_commands find flock install mv php python3 realpath rm rmdir stat
amigo_acquire_deploy_lock

[[ -d "${AMIGO_APP_DIR}" ]] || amigo_die "application directory is missing: ${AMIGO_APP_DIR}"
[[ -f "${AMIGO_COMPOSE_FILE}" ]] || amigo_die "Compose file is missing: ${AMIGO_COMPOSE_FILE}"
[[ -f "${AMIGO_APP_DIR}/.env.example" ]] || amigo_die "environment template is missing"
[[ "$(realpath -e -- "${SCRIPT_DIR}/..")" == "${AMIGO_APP_DIR}" ]] \
    || amigo_die "bootstrap must run from the production checkout at ${AMIGO_APP_DIR}"

[[ ! -e "${AMIGO_SECRETS_DIR}" ]] \
    || amigo_die "refusing to replace existing production secrets: ${AMIGO_SECRETS_DIR}"
[[ ! -e "${AMIGO_ENV_FILE}" ]] \
    || amigo_die "refusing to replace existing production environment: ${AMIGO_ENV_FILE}"
[[ -f "${SCRIPT_DIR}/extract_legacy_secrets.php" ]] \
    || amigo_die "legacy credential migration helper is missing"

readonly STAGING_DIR="${AMIGO_APP_DIR}/.secrets-staging-$$"
readonly ENV_STAGING="${AMIGO_APP_DIR}/.env-staging-$$"
[[ ! -e "${STAGING_DIR}" ]] || amigo_die "secret staging directory already exists"
[[ ! -e "${ENV_STAGING}" ]] || amigo_die "environment staging file already exists"
install -d -o root -g root -m 0700 "${STAGING_DIR}"
install -o root -g root -m 0600 "${AMIGO_APP_DIR}/.env.example" "${ENV_STAGING}"

cleanup() {
    if [[ -d "${STAGING_DIR}" ]]; then
        find "${STAGING_DIR}" -mindepth 1 -maxdepth 1 -type f -delete
        rmdir "${STAGING_DIR}" 2>/dev/null || true
    fi
    if [[ -f "${ENV_STAGING}" && ! -L "${ENV_STAGING}" ]]; then
        rm -f -- "${ENV_STAGING}"
    fi
}
trap cleanup EXIT

php "${SCRIPT_DIR}/extract_legacy_secrets.php" "${STAGING_DIR}"

python3 - "${STAGING_DIR}/postgres_password" "${STAGING_DIR}/app_encryption_key" <<'PY'
from __future__ import annotations

import base64
import os
from pathlib import Path
import sys

password_path = Path(sys.argv[1])
fernet_path = Path(sys.argv[2])
password_path.write_text(base64.urlsafe_b64encode(os.urandom(48)).decode() + "\n", encoding="utf-8")
fernet_path.write_text(base64.urlsafe_b64encode(os.urandom(32)).decode() + "\n", encoding="utf-8")
os.chmod(password_path, 0o400)
os.chmod(fernet_path, 0o400)
PY

for secret_name in \
    postgres_password \
    app_encryption_key \
    withings_client_id \
    withings_client_secret \
    withings_access_token \
    withings_refresh_token \
    telegram_bot_token \
    telegram_chat_id; do
    [[ -s "${STAGING_DIR}/${secret_name}" && ! -L "${STAGING_DIR}/${secret_name}" ]] \
        || amigo_die "secret migration did not produce ${secret_name}"
    [[ "$(stat -c '%a' "${STAGING_DIR}/${secret_name}")" == "400" ]] \
        || amigo_die "migrated secret does not have mode 0400: ${secret_name}"
done

mv -- "${STAGING_DIR}" "${AMIGO_SECRETS_DIR}"
mv -- "${ENV_STAGING}" "${AMIGO_ENV_FILE}"
trap - EXIT
amigo_log "production environment and eight root-only secret files are ready"
