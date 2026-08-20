#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
# shellcheck source=lib/common.sh
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

usage() {
    printf 'Usage: %s --resume-recorded-release [--allow-unhealthy-legacy-origin] /srv/amigo-rollbacks/YYYYMMDDTHHMMSSZ\n' \
        "${0##*/}" >&2
    printf 'The unhealthy-origin override is only for an explicit takeover from a responding but degraded legacy route.\n' >&2
    exit 2
}

[[ $# -ge 2 && $1 == "--resume-recorded-release" ]] || usage
if [[ $# -eq 2 ]]; then
    readonly ALLOW_UNHEALTHY_LEGACY_ORIGIN=0
    readonly SNAPSHOT=$2
elif [[ $# -eq 3 && $2 == "--allow-unhealthy-legacy-origin" ]]; then
    readonly ALLOW_UNHEALTHY_LEGACY_ORIGIN=1
    readonly SNAPSHOT=$3
else
    usage
fi

amigo_require_root
amigo_require_commands \
    awk bash chmod cmp crontab curl date docker find flock git grep install mariadb mktemp pgrep php \
    realpath rm rmdir sha256sum sleep stat
amigo_require_production_layout
amigo_assert_snapshot "${SNAPSHOT}"
amigo_acquire_deploy_lock

PROJECT_ROOT="$(realpath -e -- "${SCRIPT_DIR}/..")"
readonly PROJECT_ROOT
[[ "${PROJECT_ROOT}" == "${AMIGO_APP_DIR}" ]] \
    || amigo_die "takeover must run from the production checkout at ${AMIGO_APP_DIR}"
[[ -z "$(git -C "${AMIGO_APP_DIR}" status --porcelain)" ]] \
    || amigo_die "production checkout is dirty; resume from an exact committed revision"

CANDIDATE_RELEASE_SHA="$(git -C "${AMIGO_APP_DIR}" rev-parse HEAD)"
SNAPSHOT_PREVIOUS_RELEASE="$(
    amigo_snapshot_metadata_optional "${SNAPSHOT}" previous_release_sha
)"
if [[ -n "${SNAPSHOT_PREVIOUS_RELEASE}" ]]; then
    SNAPSHOT_FORMAT="release-envelope-v1"
    PREVIOUS_COMPOSE="${SNAPSHOT}/release/compose.yaml"
    [[ -f "${PREVIOUS_COMPOSE}" && ! -L "${PREVIOUS_COMPOSE}" ]] \
        || amigo_die "snapshot previous-release Compose file is missing or is a symlink"
    PREVIOUS_RELEASE_SHA="${SNAPSHOT_PREVIOUS_RELEASE}"
    PREVIOUS_IMAGE_REFERENCE="$(
        amigo_snapshot_metadata_value "${SNAPSHOT}" previous_application_image
    )"
    PREVIOUS_IMAGE_ID="$(
        amigo_snapshot_metadata_value "${SNAPSHOT}" previous_application_image_id
    )"
    PREVIOUS_IMAGE_ROLLBACK_REFERENCE="$(
        amigo_snapshot_metadata_optional "${SNAPSHOT}" previous_application_rollback_image
    )"
    PREVIOUS_DATABASE_IMAGE_REFERENCE="$(
        amigo_snapshot_metadata_optional "${SNAPSHOT}" previous_database_image
    )"
    PREVIOUS_DATABASE_IMAGE_ID="$(
        amigo_snapshot_metadata_optional "${SNAPSHOT}" previous_database_image_id
    )"
    PREVIOUS_DATABASE_ROLLBACK_REFERENCE="$(
        amigo_snapshot_metadata_optional "${SNAPSHOT}" previous_database_rollback_image
    )"
    SNAPSHOT_AI_MODEL="$(
        amigo_snapshot_metadata_value "${SNAPSHOT}" previous_ai_model
    )"
    SNAPSHOT_AI_PROMPT_VERSION="$(
        amigo_snapshot_metadata_value "${SNAPSHOT}" previous_ai_prompt_version
    )"
    PREVIOUS_COMPOSE_SHA256="$(
        amigo_snapshot_metadata_value "${SNAPSHOT}" previous_compose_sha256
    )"
    [[ "$(sha256sum "${PREVIOUS_COMPOSE}" | awk '{ print $1 }')" \
        == "${PREVIOUS_COMPOSE_SHA256}" ]] \
        || amigo_die "snapshot previous Compose file does not match its metadata"
    [[ "$(amigo_snapshot_metadata_value \
        "${SNAPSHOT}" previous_managed_route_state)" == "enabled" ]] \
        || amigo_die "takeover snapshot was not captured from an active managed route"
else
    SNAPSHOT_FORMAT="legacy-v0"
    PREVIOUS_COMPOSE="${AMIGO_COMPOSE_FILE}"
    PREVIOUS_RELEASE_SHA="$(amigo_recorded_release)"
    PREVIOUS_IMAGE_REFERENCE="amigo:${PREVIOUS_RELEASE_SHA}"
    PREVIOUS_IMAGE_ID="$(docker image inspect --format '{{.Id}}' \
        "${PREVIOUS_IMAGE_REFERENCE}")" \
        || amigo_die "recorded application image is unavailable for legacy snapshot takeover"
    PREVIOUS_IMAGE_ROLLBACK_REFERENCE=""
    PREVIOUS_DATABASE_IMAGE_REFERENCE=""
    PREVIOUS_DATABASE_IMAGE_ID=""
    PREVIOUS_DATABASE_ROLLBACK_REFERENCE=""
    SNAPSHOT_AI_MODEL=""
    SNAPSHOT_AI_PROMPT_VERSION=""
    PREVIOUS_COMPOSE_SHA256="$(sha256sum "${PREVIOUS_COMPOSE}" | awk '{ print $1 }')"
    [[ -f "${SNAPSHOT}/nginx/my.conf" && ! -L "${SNAPSHOT}/nginx/my.conf" ]] \
        || amigo_die "legacy-format snapshot lacks its captured nginx origin file"
    [[ "$(awk '
        /^[[:space:]]*# BEGIN AMIGO V2 ROUTE[[:space:]]*$/ { begin += 1 }
        /^[[:space:]]*include \/etc\/nginx\/snippets\/amigo-v2-locations[.]conf;[[:space:]]*$/ { include += 1 }
        /^[[:space:]]*# END AMIGO V2 ROUTE[[:space:]]*$/ { end += 1 }
        END { printf "%d:%d:%d", begin + 0, include + 0, end + 0 }
    ' "${SNAPSHOT}/nginx/my.conf")" == "2:2:2" ]] \
        || amigo_die "legacy-format snapshot was not captured from an active managed route"
fi
readonly CANDIDATE_RELEASE_SHA SNAPSHOT_FORMAT PREVIOUS_COMPOSE PREVIOUS_RELEASE_SHA
readonly PREVIOUS_IMAGE_REFERENCE PREVIOUS_IMAGE_ID PREVIOUS_IMAGE_ROLLBACK_REFERENCE
readonly PREVIOUS_DATABASE_IMAGE_REFERENCE PREVIOUS_DATABASE_IMAGE_ID
readonly PREVIOUS_DATABASE_ROLLBACK_REFERENCE PREVIOUS_COMPOSE_SHA256
readonly SNAPSHOT_AI_MODEL SNAPSHOT_AI_PROMPT_VERSION
[[ "${PREVIOUS_RELEASE_SHA}" =~ ^[0-9a-f]{40,64}$ ]]
[[ "${PREVIOUS_IMAGE_REFERENCE}" == "amigo:${PREVIOUS_RELEASE_SHA}" ]]
[[ "${PREVIOUS_IMAGE_ID}" =~ ^sha256:[0-9a-f]{64}$ ]]
[[ -z "${PREVIOUS_IMAGE_ROLLBACK_REFERENCE}" \
    || "${PREVIOUS_IMAGE_ROLLBACK_REFERENCE}" \
        == "amigo-rollback:${PREVIOUS_RELEASE_SHA}-${PREVIOUS_IMAGE_ID:7:12}" ]] \
    || amigo_die "snapshot previous application rollback image is invalid"
DATABASE_SNAPSHOT_FIELDS=0
for database_metadata_value in \
    "${PREVIOUS_DATABASE_IMAGE_REFERENCE}" \
    "${PREVIOUS_DATABASE_IMAGE_ID}" \
    "${PREVIOUS_DATABASE_ROLLBACK_REFERENCE}"; do
    [[ -z "${database_metadata_value}" ]] || DATABASE_SNAPSHOT_FIELDS=$((DATABASE_SNAPSHOT_FIELDS + 1))
done
readonly DATABASE_SNAPSHOT_FIELDS
[[ ${DATABASE_SNAPSHOT_FIELDS} -eq 0 || ${DATABASE_SNAPSHOT_FIELDS} -eq 3 ]] \
    || amigo_die "snapshot contains an incomplete previous PostgreSQL image envelope"
if [[ ${DATABASE_SNAPSHOT_FIELDS} -eq 3 ]]; then
    [[ "${PREVIOUS_DATABASE_IMAGE_REFERENCE}" == "postgres:17-alpine" ]]
    [[ "${PREVIOUS_DATABASE_IMAGE_ID}" =~ ^sha256:[0-9a-f]{64}$ ]]
    [[ "${PREVIOUS_DATABASE_ROLLBACK_REFERENCE}" \
        == "amigo-postgres-rollback:${PREVIOUS_DATABASE_IMAGE_ID:7}" ]] \
        || amigo_die "snapshot previous PostgreSQL rollback image is invalid"
    [[ "$(docker image inspect --format '{{.Id}}' \
        "${PREVIOUS_DATABASE_ROLLBACK_REFERENCE}")" == "${PREVIOUS_DATABASE_IMAGE_ID}" ]] \
        || amigo_die "snapshot previous PostgreSQL image is unavailable or has changed"
fi
[[ "${PREVIOUS_COMPOSE_SHA256}" =~ ^[0-9a-f]{64}$ ]]
[[ "$(amigo_snapshot_metadata_value "${SNAPSHOT}" legacy_withings_cron_state)" \
    == "disabled" ]] \
    || amigo_die "takeover snapshot was not captured with the legacy collector disabled"
[[ "$(amigo_recorded_release)" == "${PREVIOUS_RELEASE_SHA}" ]] \
    || amigo_die "recorded release differs from the snapshot recovery release"
amigo_assert_release_rollback_compatible \
    "${AMIGO_APP_DIR}" "${PREVIOUS_RELEASE_SHA}" "${CANDIDATE_RELEASE_SHA}"

if [[ -n "${PREVIOUS_IMAGE_ROLLBACK_REFERENCE}" ]]; then
    PREVIOUS_IMAGE_SOURCE="${PREVIOUS_IMAGE_ROLLBACK_REFERENCE}"
else
    PREVIOUS_IMAGE_SOURCE="${PREVIOUS_IMAGE_REFERENCE}"
fi
readonly PREVIOUS_IMAGE_SOURCE
[[ "$(docker image inspect --format '{{.Id}}' "${PREVIOUS_IMAGE_SOURCE}")" \
    == "${PREVIOUS_IMAGE_ID}" ]] \
    || amigo_die "snapshot previous application image is unavailable or has changed"
amigo_assert_image_revision "${PREVIOUS_IMAGE_SOURCE}" "${PREVIOUS_RELEASE_SHA}"
amigo_compose_file_release \
    "${PREVIOUS_COMPOSE}" "${PREVIOUS_RELEASE_SHA}" config --quiet

amigo_assert_managed_route_inactive
LEGACY_CRONTAB_STATE="$(mktemp /run/amigo-legacy-takeover-crontab.XXXXXX)"
readonly LEGACY_CRONTAB_STATE
trap 'rm -f -- "${LEGACY_CRONTAB_STATE}"' EXIT
crontab -u "${AMIGO_LEGACY_CRON_USER}" -l >"${LEGACY_CRONTAB_STATE}"
[[ "$(amigo_count_exact_line "${AMIGO_LEGACY_WITHINGS_CRON_LINE}" \
    "${LEGACY_CRONTAB_STATE}")" -eq 1 ]] \
    || amigo_die "legacy takeover requires exactly one active Withings cron line"
[[ "$(amigo_count_exact_line "${AMIGO_LEGACY_WITHINGS_DISABLED_LINE}" \
    "${LEGACY_CRONTAB_STATE}")" -eq 0 ]] \
    || amigo_die "legacy takeover found an unexpected disabled Withings marker"
[[ "$(amigo_count_exact_line "${AMIGO_SHARED_TELEGRAM_CRON_LINE}" \
    "${LEGACY_CRONTAB_STATE}")" -ge 1 ]] \
    || amigo_die "shared Telegram cron is missing before takeover"
rm -f -- "${LEGACY_CRONTAB_STATE}"
trap - EXIT
LEGACY_ORIGIN_STATUS="$(
    curl --silent --show-error --max-time 15 \
        --header 'Host: amigo.tolstik.ru' \
        --output /dev/null \
        --write-out '%{http_code}' \
        http://127.0.0.1/amigo/
)"
readonly LEGACY_ORIGIN_STATUS
[[ "${LEGACY_ORIGIN_STATUS}" =~ ^[1-5][0-9]{2}$ ]] \
    || amigo_die "legacy origin did not return a concrete HTTP response before takeover"
LEGACY_ORIGIN_WAS_HEALTHY=0
if [[ "${LEGACY_ORIGIN_STATUS}" == "200" ]]; then
    LEGACY_ORIGIN_WAS_HEALTHY=1
elif [[ ${ALLOW_UNHEALTHY_LEGACY_ORIGIN} -eq 1 ]]; then
    amigo_log "DEGRADED SOURCE: legacy origin returned HTTP ${LEGACY_ORIGIN_STATUS}; explicit override accepted"
    amigo_log "legacy will not be treated as a healthy automatic failure target during this takeover"
else
    amigo_die "legacy origin route returned HTTP ${LEGACY_ORIGIN_STATUS}; explicit unhealthy-origin override is required"
fi
readonly LEGACY_ORIGIN_WAS_HEALTHY
[[ "$(mariadb --batch --skip-column-names "${AMIGO_LEGACY_DB}" \
    --execute 'SELECT COUNT(*) FROM seting')" == "1" ]] \
    || amigo_die "legacy token table must contain exactly one row before takeover"

read -r IMAGE_AI_MODEL IMAGE_AI_PROMPT_VERSION < <(
    docker run --rm --network none --read-only --tmpfs /tmp:size=8m,mode=1777 \
        --entrypoint python "${PREVIOUS_IMAGE_SOURCE}" -c \
        'from app.ai_contracts import AI_MODEL, AI_PROMPT_VERSION; print(AI_MODEL, AI_PROMPT_VERSION)'
)
readonly IMAGE_AI_MODEL IMAGE_AI_PROMPT_VERSION
if [[ "${SNAPSHOT_FORMAT}" == "release-envelope-v1" ]]; then
    [[ "${IMAGE_AI_MODEL}" == "${SNAPSHOT_AI_MODEL}" ]] \
        || amigo_die "snapshot previous AI model differs from its immutable image"
    [[ "${IMAGE_AI_PROMPT_VERSION}" == "${SNAPSHOT_AI_PROMPT_VERSION}" ]] \
        || amigo_die "snapshot previous AI prompt differs from its immutable image"
fi
PREVIOUS_AI_MODEL="${IMAGE_AI_MODEL}"
PREVIOUS_AI_PROMPT_VERSION="${IMAGE_AI_PROMPT_VERSION}"
readonly PREVIOUS_AI_MODEL PREVIOUS_AI_PROMPT_VERSION
[[ "${PREVIOUS_AI_MODEL}" =~ ^[A-Za-z0-9._-]{1,64}$ ]]
[[ "${PREVIOUS_AI_PROMPT_VERSION}" =~ ^[A-Za-z0-9._-]{1,64}$ ]]

HANDOFF_DIR="$(mktemp -d /run/amigo-legacy-takeover.XXXXXX)"
readonly HANDOFF_DIR
chmod 0700 "${HANDOFF_DIR}"
TAKEOVER_STARTED=0
TOKEN_IMPORTED=0
ROUTE_ENABLE_STARTED=0
TAKEOVER_COMMITTED=0
AI_WORKER_DEGRADED=0

cleanup_handoff() {
    find "${HANDOFF_DIR}" -mindepth 1 -maxdepth 1 -type f -delete 2>/dev/null || true
    rmdir -- "${HANDOFF_DIR}" 2>/dev/null || true
}

takeover_error() {
    local status=$1
    local line=$2
    trap - ERR
    trap '' HUP INT TERM
    set +e
    amigo_log "legacy-to-Amigo takeover failed at line ${line} (status ${status})"
    if [[ ${TAKEOVER_STARTED} -eq 1 && ${TAKEOVER_COMMITTED} -eq 0 ]]; then
        amigo_revert_legacy_takeover \
            "${PREVIOUS_COMPOSE}" \
            "${PREVIOUS_RELEASE_SHA}" \
            "${SNAPSHOT}" \
            "${TOKEN_IMPORTED}" \
            "${ROUTE_ENABLE_STARTED}" \
            "${LEGACY_ORIGIN_WAS_HEALTHY}"
    fi
    cleanup_handoff
    exit "${status}"
}
trap cleanup_handoff EXIT
trap 'takeover_error "$?" "${LINENO}"' ERR
trap 'takeover_error 129 "${LINENO}"' HUP
trap 'takeover_error 130 "${LINENO}"' INT
trap 'takeover_error 143 "${LINENO}"' TERM

TAKEOVER_STARTED=1
amigo_log "stopping any residual Amigo collectors before ownership transfer"
amigo_compose_file_release \
    "${PREVIOUS_COMPOSE}" "${PREVIOUS_RELEASE_SHA}" \
    stop worker ai-worker ingest ai-gateway web

amigo_log "restoring the exact recorded application image reference"
docker image tag "${PREVIOUS_IMAGE_SOURCE}" "${PREVIOUS_IMAGE_REFERENCE}"
[[ "$(docker image inspect --format '{{.Id}}' "${PREVIOUS_IMAGE_REFERENCE}")" \
    == "${PREVIOUS_IMAGE_ID}" ]]

if [[ ${DATABASE_SNAPSHOT_FIELDS} -eq 3 ]]; then
    amigo_log "starting PostgreSQL with the snapshot's exact image and preserved volume"
    docker image tag \
        "${PREVIOUS_DATABASE_ROLLBACK_REFERENCE}" "${PREVIOUS_DATABASE_IMAGE_REFERENCE}"
    amigo_compose_file_release \
        "${PREVIOUS_COMPOSE}" "${PREVIOUS_RELEASE_SHA}" \
        up -d --no-build --force-recreate db
else
    amigo_log "starting the existing preserved PostgreSQL container for the older snapshot format"
    existing_database_container=$(amigo_compose_file_release \
        "${PREVIOUS_COMPOSE}" "${PREVIOUS_RELEASE_SHA}" ps --all -q db)
    [[ -n "${existing_database_container}" ]] \
        || amigo_die "older takeover snapshot requires the preserved PostgreSQL container"
    [[ "$(docker inspect --format '{{.Config.Image}}' "${existing_database_container}")" \
        == "postgres:17-alpine" ]] \
        || amigo_die "preserved PostgreSQL container uses an unexpected image reference"
    EXISTING_DATABASE_IMAGE_ID="$(docker inspect --format '{{.Image}}' \
        "${existing_database_container}")"
    readonly EXISTING_DATABASE_IMAGE_ID
    [[ "${EXISTING_DATABASE_IMAGE_ID}" =~ ^sha256:[0-9a-f]{64}$ ]] \
        || amigo_die "preserved PostgreSQL container has an invalid image ID"
    amigo_compose_file_release \
        "${PREVIOUS_COMPOSE}" "${PREVIOUS_RELEASE_SHA}" start db
fi
for attempt in {1..60}; do
    if amigo_compose_file_release "${PREVIOUS_COMPOSE}" "${PREVIOUS_RELEASE_SHA}" \
        exec -T db pg_isready -U amigo -d amigo >/dev/null 2>&1; then
        break
    fi
    [[ ${attempt} -lt 60 ]] || amigo_die "PostgreSQL did not become ready"
    sleep 2
done
resumed_database_container=$(amigo_compose_file_release \
    "${PREVIOUS_COMPOSE}" "${PREVIOUS_RELEASE_SHA}" ps -q db)
[[ -n "${resumed_database_container}" ]] \
    || amigo_die "resumed PostgreSQL container is missing"
if [[ ${DATABASE_SNAPSHOT_FIELDS} -eq 3 ]]; then
    [[ "$(docker inspect --format '{{.Config.Image}}' "${resumed_database_container}")" \
        == "${PREVIOUS_DATABASE_IMAGE_REFERENCE}" ]]
    [[ "$(docker inspect --format '{{.Image}}' "${resumed_database_container}")" \
        == "${PREVIOUS_DATABASE_IMAGE_ID}" ]]
else
    [[ "$(docker inspect --format '{{.Image}}' "${resumed_database_container}")" \
        == "${EXISTING_DATABASE_IMAGE_ID}" ]]
fi

amigo_log "disabling only the exact legacy Withings cron before reading its current token pair"
bash "${SCRIPT_DIR}/cron-control.sh" disable

LEGACY_DISABLE_MINUTE="$(date -u +%Y%m%dT%H%M)"
readonly LEGACY_DISABLE_MINUTE
amigo_log "waiting through a cron boundary until the legacy Withings process is quiescent"
LEGACY_QUIET_OBSERVATIONS=0
for ((attempt = 1; attempt <= 60; attempt += 1)); do
    if pgrep -u "${AMIGO_LEGACY_CRON_USER}" -f -- \
        '/srv/cron/get_withings[.]php([[:space:]]|$)' >/dev/null; then
        LEGACY_QUIET_OBSERVATIONS=0
    else
        LEGACY_QUIET_OBSERVATIONS=$((LEGACY_QUIET_OBSERVATIONS + 1))
        if [[ ${LEGACY_QUIET_OBSERVATIONS} -ge 2 \
            && "$(date -u +%Y%m%dT%H%M)" != "${LEGACY_DISABLE_MINUTE}" ]]; then
            break
        fi
    fi
    [[ ${attempt} -lt 60 ]] && sleep 2
done
[[ ${LEGACY_QUIET_OBSERVATIONS} -ge 2 ]] \
    || amigo_die "legacy Withings process did not become quiescent after cron disable"
amigo_assert_legacy_cron_disabled

amigo_log "extracting the current legacy OAuth pair into a one-use root-only handoff"
php "${SCRIPT_DIR}/extract_legacy_secrets.php" "${HANDOFF_DIR}"
for extracted_name in \
    withings_access_token withings_refresh_token withings_client_id withings_client_secret \
    telegram_bot_token telegram_chat_id; do
    [[ -s "${HANDOFF_DIR}/${extracted_name}" && ! -L "${HANDOFF_DIR}/${extracted_name}" ]] \
        || amigo_die "legacy takeover extraction is incomplete"
    [[ "$(stat -c '%a' "${HANDOFF_DIR}/${extracted_name}")" == "400" ]] \
        || amigo_die "legacy takeover handoff file permissions are invalid"
done
cmp --silent "${HANDOFF_DIR}/withings_client_id" \
    "${AMIGO_SECRETS_DIR}/withings_client_id" \
    || amigo_die "legacy and Amigo Withings client IDs differ"
cmp --silent "${HANDOFF_DIR}/withings_client_secret" \
    "${AMIGO_SECRETS_DIR}/withings_client_secret" \
    || amigo_die "legacy and Amigo Withings client credentials differ"

amigo_log "atomically installing the current legacy OAuth pair in PostgreSQL"
amigo_compose_file_release "${PREVIOUS_COMPOSE}" "${PREVIOUS_RELEASE_SHA}" \
    run --rm --no-deps \
    --volume "${HANDOFF_DIR}:/handoff:ro" \
    worker python -c '
from pathlib import Path

from app.config import get_settings
from app.crypto import SecretCipher
from app.db import SessionLocal
from app.models import ProviderCredential
from app.withings import utcnow

directory = Path("/handoff")
values = {}
for name in ("withings_access_token", "withings_refresh_token"):
    value = (directory / name).read_text(encoding="utf-8").rstrip("\n")
    if not value or any(character in value for character in ("\0", "\r", "\n")):
        raise RuntimeError("invalid OAuth handoff value")
    values[name] = value
settings = get_settings()
cipher = SecretCipher(settings.token_encryption_key)
with SessionLocal() as db:
    credential = db.get(ProviderCredential, "withings")
    if credential is None:
        raise RuntimeError("PostgreSQL Withings credential row is missing")
    credential.access_token_encrypted = cipher.encrypt(values["withings_access_token"])
    credential.refresh_token_encrypted = cipher.encrypt(values["withings_refresh_token"])
    credential.expires_at = utcnow()
    db.commit()
'
TOKEN_IMPORTED=1

find "${HANDOFF_DIR}" -mindepth 1 -maxdepth 1 -type f -delete

amigo_log "validating takeover with one notification-suppressed incremental synchronization"
amigo_compose_file_release "${PREVIOUS_COMPOSE}" "${PREVIOUS_RELEASE_SHA}" \
    run --rm --no-deps worker \
    python -m app.cli sync --suppress-notifications
amigo_log "refreshing the disabled legacy fallback with the now-current token pair"
amigo_handback_withings_tokens "${PREVIOUS_COMPOSE}" "${PREVIOUS_RELEASE_SHA}"

AI_TABLE_STATE="$(
    amigo_compose_file_release "${PREVIOUS_COMPOSE}" "${PREVIOUS_RELEASE_SHA}" \
        exec -T db psql \
        --username amigo --dbname amigo --no-psqlrc --quiet --tuples-only --no-align \
        --set=ON_ERROR_STOP=1 \
        --command "SELECT CASE WHEN to_regclass('public.ai_analysis_jobs') IS NULL THEN 'absent' ELSE 'present' END;"
)" || AI_TABLE_STATE="error"
readonly AI_TABLE_STATE
if [[ "${AI_TABLE_STATE}" == "present" ]]; then
    if ! amigo_compose_file_release "${PREVIOUS_COMPOSE}" "${PREVIOUS_RELEASE_SHA}" \
        exec -T db psql \
        --username amigo --dbname amigo --no-psqlrc --quiet --set=ON_ERROR_STOP=1 \
        --command "
            UPDATE ai_analysis_jobs
            SET status = 'superseded', finished_at = CURRENT_TIMESTAMP,
                lease_until = NULL, last_error_code = NULL
            WHERE status IN ('pending', 'processing')
              AND (
                  model <> '${PREVIOUS_AI_MODEL}'
                  OR prompt_version <> '${PREVIOUS_AI_PROMPT_VERSION}'
              );
        " >/dev/null; then
        AI_WORKER_DEGRADED=1
    fi
elif [[ "${AI_TABLE_STATE}" == "absent" ]]; then
    AI_WORKER_DEGRADED=1
    amigo_log "WARNING: AI job table is absent; AI worker will remain stopped"
else
    AI_WORKER_DEGRADED=1
fi

amigo_log "starting the recorded Amigo release before moving the public route"
amigo_compose_file_release "${PREVIOUS_COMPOSE}" "${PREVIOUS_RELEASE_SHA}" \
    up -d --no-build --no-deps --force-recreate --wait --wait-timeout 180 \
    web ingest ai-gateway worker
if [[ ${AI_WORKER_DEGRADED} -eq 0 ]]; then
    amigo_compose_file_release "${PREVIOUS_COMPOSE}" "${PREVIOUS_RELEASE_SHA}" \
        up -d --no-build --no-deps --force-recreate --wait --wait-timeout 180 ai-worker
else
    amigo_compose_file_release "${PREVIOUS_COMPOSE}" "${PREVIOUS_RELEASE_SHA}" \
        create --no-build --force-recreate ai-worker >/dev/null
    amigo_compose_file_release \
        "${PREVIOUS_COMPOSE}" "${PREVIOUS_RELEASE_SHA}" stop ai-worker >/dev/null
fi
amigo_wait_for_http "${AMIGO_DIRECT_HEALTH_URL}" 60 \
    || amigo_die "recorded release web endpoint did not become ready"
amigo_wait_for_http "http://127.0.0.1:18182/healthz" 60 \
    || amigo_die "recorded release ingest endpoint did not become ready"

ROUTE_ENABLE_STARTED=1
bash "${SCRIPT_DIR}/nginx-control.sh" enable "${SNAPSHOT}"
curl --fail --silent --show-error --max-time 15 \
    --header 'Host: amigo.tolstik.ru' --output /dev/null \
    http://127.0.0.1/amigo/api/v1/overview

for resumed_service in web worker ingest ai-worker ai-gateway; do
    resumed_container=$(amigo_compose_file_release \
        "${PREVIOUS_COMPOSE}" "${PREVIOUS_RELEASE_SHA}" \
        ps --all -q "${resumed_service}")
    [[ -n "${resumed_container}" ]] || amigo_die "resumed service is missing: ${resumed_service}"
    [[ "$(docker inspect --format '{{.Config.Image}}' "${resumed_container}")" \
        == "${PREVIOUS_IMAGE_REFERENCE}" ]] \
        || amigo_die "resumed service uses the wrong image: ${resumed_service}"
    [[ "$(docker inspect --format '{{.Image}}' "${resumed_container}")" \
        == "${PREVIOUS_IMAGE_ID}" ]] \
        || amigo_die "resumed service uses the wrong immutable image ID: ${resumed_service}"
done

amigo_assert_managed_route_active
amigo_assert_legacy_cron_disabled
amigo_record_current_release "${PREVIOUS_RELEASE_SHA}"
[[ "$(amigo_recorded_release)" == "${PREVIOUS_RELEASE_SHA}" ]]
TAKEOVER_COMMITTED=1
trap - ERR HUP INT TERM
cleanup_handoff
trap - EXIT
amigo_log "RECORDED AMIGO RELEASE RESUMED: ${PREVIOUS_RELEASE_SHA}"
amigo_log "fresh legacy OAuth pair is in PostgreSQL; managed route is active; legacy cron is disabled"
if [[ ${AI_WORKER_DEGRADED} -eq 1 ]]; then
    amigo_log "AI worker remains intentionally stopped because job metadata could not be safely reconciled"
fi
