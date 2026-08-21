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
Usage: deploy.sh --send-telegram-test|--skip-telegram-test [--no-auto-recovery]

Choose exactly one notification mode. --send-telegram-test explicitly
authorizes one clearly labelled pre-cutover Telegram smoke message;
--skip-telegram-test performs the deployment without sending that message.
--no-auto-recovery is an explicit operator-session exception that leaves a
started candidate in place for fix-forward instead of restoring its snapshot.
USAGE
    exit 2
}

[[ $# -ge 1 && $# -le 2 ]] || usage
case $1 in
    --send-telegram-test)
        SEND_TELEGRAM_TEST=1
        ;;
    --skip-telegram-test)
        SEND_TELEGRAM_TEST=0
        ;;
    *)
        usage
        ;;
esac
readonly SEND_TELEGRAM_TEST

AUTO_RECOVERY=1
if [[ -f "${SCRIPT_DIR}/.fix-forward-session" ]]; then
    AUTO_RECOVERY=0
fi
if [[ $# -eq 2 ]]; then
    [[ $2 == "--no-auto-recovery" ]] || usage
    AUTO_RECOVERY=0
fi
readonly AUTO_RECOVERY

amigo_require_root
amigo_require_commands \
    awk bash chmod cmp curl date docker flock git install mariadb mktemp mv nginx realpath rm \
    sha256sum sleep stat
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
PREVIOUS_RELEASE_SHA="$(amigo_recorded_release)"
readonly PREVIOUS_RELEASE_SHA
[[ "${RELEASE_SHA}" != "${PREVIOUS_RELEASE_SHA}" ]] \
    || amigo_die "candidate SHA is already the recorded release; refusing a mutable same-SHA rebuild"
amigo_assert_release_rollback_compatible \
    "${AMIGO_APP_DIR}" "${PREVIOUS_RELEASE_SHA}" "${RELEASE_SHA}"
bash "${SCRIPT_DIR}/test-release-recovery.sh" "${PREVIOUS_RELEASE_SHA}"
[[ -n "$(docker image inspect --format '{{.Id}}' "amigo:${PREVIOUS_RELEASE_SHA}")" ]] \
    || amigo_die "recorded previous release image is unavailable"
bash "${SCRIPT_DIR}/install-release-wrapper.sh"
export AMIGO_IMAGE_TAG="${RELEASE_SHA}"
CANDIDATE_IMAGE_SOURCE="ghcr.io/tolstik/amigo:${RELEASE_SHA}"
readonly CANDIDATE_IMAGE_SOURCE
readonly ANDROID_APK_URL="https://github.com/tolstik/amigo/releases/download/v5.0.3/Amigo-1.2.2.apk"
readonly ANDROID_APK_SHA256="4c8168013d49439072c0a084ea3284d88916d0164b5fba47201c60861ee9454a"
amigo_log "candidate Git SHA: ${RELEASE_SHA}"
amigo_log "automatic recovery target: ${PREVIOUS_RELEASE_SHA}"

[[ ! -L "${AMIGO_APP_DIR}/data" && ! -L "${AMIGO_IMPORT_DIR}" ]] \
    || amigo_die "application data/import paths must not be symlinks"
install -d -o root -g root -m 0700 \
    "${AMIGO_APP_DIR}/data" "${AMIGO_IMPORT_DIR}" "${AMIGO_LAB_FILES_DIR}" \
    "${AMIGO_APP_DIR}/data/android"

amigo_compose config --quiet
nginx -t >/dev/null

amigo_log "preparing the pinned AI runtime and downloading immutable release images"
bash "${SCRIPT_DIR}/prepare-ai-runtime.sh"
docker pull "${CANDIDATE_IMAGE_SOURCE}"
amigo_assert_image_revision "${CANDIDATE_IMAGE_SOURCE}" "${RELEASE_SHA}"
docker image tag "${CANDIDATE_IMAGE_SOURCE}" "amigo:${RELEASE_SHA}"
amigo_assert_image_revision "amigo:${RELEASE_SHA}" "${RELEASE_SHA}"
amigo_compose pull db

SNAPSHOT=""
CUTOVER_STARTED=0
CUTOVER_COMMITTED=0
CANDIDATE_RUNTIME_ACTIVE=0
ANDROID_APK_DOWNLOAD=""
ANDROID_APK_INSTALL_CANDIDATE=""
EXISTING_WORKER_STOPPED=0
EXISTING_WORKER_WAS_RUNNING=0
EXISTING_AI_WORKER_STOPPED=0
EXISTING_AI_WORKER_WAS_RUNNING=0
EXISTING_INGEST_STOPPED=0
EXISTING_INGEST_WAS_RUNNING=0
EXISTING_LAB_PARSER_STOPPED=0
EXISTING_LAB_PARSER_WAS_RUNNING=0

deploy_error() {
    local status=$1
    local line=$2
    trap - ERR
    trap '' HUP INT TERM
    set +e
    [[ -z "${ANDROID_APK_DOWNLOAD}" ]] || rm -f -- "${ANDROID_APK_DOWNLOAD}"
    [[ -z "${ANDROID_APK_INSTALL_CANDIDATE}" ]] \
        || rm -f -- "${ANDROID_APK_INSTALL_CANDIDATE}"
    amigo_log "deployment failed at line ${line} (status ${status})"
    if [[ ${CUTOVER_COMMITTED} -eq 1 ]]; then
        amigo_log "runtime cutover remains healthy; finish the release-state/checkpoint step manually"
    elif [[ ${CUTOVER_STARTED} -eq 1 && -n "${SNAPSHOT}" && ${AUTO_RECOVERY} -eq 1 ]]; then
        amigo_log "starting automatic recovery of the immutable previous Amigo release"
        AMIGO_DEPLOY_LOCK_HELD=1 bash \
            "${SCRIPT_DIR}/restore-previous-release.sh" "${SNAPSHOT}"
        recovery_status=$?
        if [[ ${recovery_status} -ne 0 ]]; then
            amigo_log "AUTOMATIC PREVIOUS-RELEASE RECOVERY FAILED"
            amigo_log "legacy was not activated; explicit disaster fallback requires:"
            amigo_log "sudo ${SCRIPT_DIR}/rollback.sh --to-legacy ${SNAPSHOT}"
        fi
    elif [[ ${CUTOVER_STARTED} -eq 1 && -n "${SNAPSHOT}" ]]; then
        amigo_log "automatic recovery was explicitly disabled for this operator session"
        if [[ ${CANDIDATE_RUNTIME_ACTIVE} -eq 1 ]]; then
            amigo_record_current_release "${RELEASE_SHA}"
            amigo_log "candidate runtime remains active and recorded for the next fix-forward release"
        else
            amigo_log "candidate did not reach the active-runtime boundary and was not recorded"
        fi
        amigo_log "verified recovery snapshot remains available at ${SNAPSHOT}"
    else
        if [[ ${EXISTING_WORKER_STOPPED} -eq 1 && ${EXISTING_WORKER_WAS_RUNNING} -eq 1 ]]; then
            amigo_log "restarting the previous data worker after the failed pre-cutover deployment"
            amigo_compose start worker \
                || amigo_log "WARNING: could not restart the previous data worker"
        fi
        if [[ ${EXISTING_AI_WORKER_STOPPED} -eq 1 && ${EXISTING_AI_WORKER_WAS_RUNNING} -eq 1 ]]; then
            amigo_log "restarting the previous AI worker after the failed pre-cutover deployment"
            amigo_compose start ai-worker \
                || amigo_log "WARNING: could not restart the previous AI worker"
        fi
        if [[ ${EXISTING_INGEST_STOPPED} -eq 1 && ${EXISTING_INGEST_WAS_RUNNING} -eq 1 ]]; then
            amigo_log "restarting the previous ingest service after the failed pre-cutover deployment"
            amigo_compose start ingest \
                || amigo_log "WARNING: could not restart the previous ingest service"
        fi
        if [[ ${EXISTING_LAB_PARSER_STOPPED} -eq 1 && ${EXISTING_LAB_PARSER_WAS_RUNNING} -eq 1 ]]; then
            amigo_log "restarting the previous lab parser after the failed pre-cutover deployment"
            amigo_compose start lab-parser \
                || amigo_log "WARNING: could not restart the previous lab parser"
        fi
        if [[ -n "${SNAPSHOT}" ]]; then
            amigo_log "production route was not changed; snapshot is ${SNAPSHOT}"
        fi
    fi
    exit "${status}"
}
trap 'deploy_error "$?" "${LINENO}"' ERR
trap 'deploy_error 129 "${LINENO}"' HUP
trap 'deploy_error 130 "${LINENO}"' INT
trap 'deploy_error 143 "${LINENO}"' TERM

ANDROID_APK_DOWNLOAD="$(mktemp /run/amigo-android-apk.XXXXXX)"
chmod 0600 "${ANDROID_APK_DOWNLOAD}"
curl --fail --location --silent --show-error --max-time 180 \
    --output "${ANDROID_APK_DOWNLOAD}" "${ANDROID_APK_URL}"
[[ "$(sha256sum "${ANDROID_APK_DOWNLOAD}" | awk '{ print $1 }')" \
    == "${ANDROID_APK_SHA256}" ]] \
    || amigo_die "downloaded Android APK hash differs from the signed release"

SNAPSHOT="$(AMIGO_DEPLOY_LOCK_HELD=1 bash "${SCRIPT_DIR}/pre-cutover-backup.sh")"
amigo_assert_snapshot "${SNAPSHOT}"
[[ "$(amigo_snapshot_metadata_value "${SNAPSHOT}" previous_release_sha)" \
    == "${PREVIOUS_RELEASE_SHA}" ]] \
    || amigo_die "snapshot previous release differs from the deploy recovery target"
[[ -n "$(amigo_snapshot_metadata_value "${SNAPSHOT}" previous_application_rollback_image)" ]]
[[ -n "$(amigo_snapshot_metadata_value "${SNAPSHOT}" previous_database_image_id)" ]]
[[ -n "$(amigo_snapshot_metadata_value "${SNAPSHOT}" previous_database_rollback_image)" ]]
CUTOVER_STARTED=1

existing_worker_container=$(amigo_compose ps -q worker)
if [[ -n "${existing_worker_container}" ]] \
    && [[ "$(docker inspect --format '{{.State.Status}}' "${existing_worker_container}")" == "running" ]]; then
    EXISTING_WORKER_WAS_RUNNING=1
    amigo_log "stopping the existing v2 worker before migration and one-shot synchronization"
    EXISTING_WORKER_STOPPED=1
    amigo_compose stop worker
fi
existing_ai_worker_container=$(amigo_compose ps -q ai-worker)
if [[ -n "${existing_ai_worker_container}" ]] \
    && [[ "$(docker inspect --format '{{.State.Status}}' "${existing_ai_worker_container}")" == "running" ]]; then
    EXISTING_AI_WORKER_WAS_RUNNING=1
    EXISTING_AI_WORKER_STOPPED=1
    amigo_log "stopping the existing AI worker before migration"
    amigo_compose stop --timeout 120 ai-worker
fi
existing_ingest_container=$(amigo_compose ps -q ingest)
if [[ -n "${existing_ingest_container}" ]] \
    && [[ "$(docker inspect --format '{{.State.Status}}' "${existing_ingest_container}")" == "running" ]]; then
    EXISTING_INGEST_WAS_RUNNING=1
    EXISTING_INGEST_STOPPED=1
    amigo_log "stopping the existing ingest service before migration"
    amigo_compose stop ingest
fi
existing_lab_parser_container=$(amigo_compose ps -q lab-parser)
if [[ -n "${existing_lab_parser_container}" ]] \
    && [[ "$(docker inspect --format '{{.State.Status}}' "${existing_lab_parser_container}")" == "running" ]]; then
    EXISTING_LAB_PARSER_WAS_RUNNING=1
    EXISTING_LAB_PARSER_STOPPED=1
    amigo_log "stopping the existing isolated laboratory parser before migration"
    amigo_compose stop lab-parser
fi

amigo_log "starting PostgreSQL"
amigo_compose up -d db
for attempt in {1..60}; do
    if amigo_compose exec -T db pg_isready -U amigo -d amigo >/dev/null 2>&1; then
        break
    fi
    [[ ${attempt} -lt 60 ]] || amigo_die "PostgreSQL did not become ready"
    sleep 2
done

amigo_log "running the idempotent bootstrap, including schema migrations"
amigo_compose run --rm --no-deps worker python -m app.cli bootstrap

amigo_log "copying and verifying legacy laboratory originals in PostgreSQL"
amigo_compose run --rm --no-deps \
    --volume "${AMIGO_LAB_FILES_DIR}:/lab-files:ro" \
    worker python -m app.cli backfill-files

if amigo_compose run --rm --no-deps --user 0 worker python -m app.cli auth-status; then
    amigo_log "local authentication is already configured"
else
    auth_status=$?
    [[ ${auth_status} -eq 75 ]] \
        || amigo_die "authentication status returned unexpected code ${auth_status}"
    [[ -r /dev/tty && -w /dev/tty ]] \
        || amigo_die "first authentication cutover requires an interactive root TTY"
    amigo_log "first authentication cutover requires the local account password"
    IFS= read -r -s -p 'New Amigo password (minimum 14 characters): ' AMIGO_NEW_PASSWORD </dev/tty
    printf '\n' >/dev/tty
    IFS= read -r -s -p 'Repeat Amigo password: ' AMIGO_NEW_PASSWORD_CONFIRM </dev/tty
    printf '\n' >/dev/tty
    [[ "${AMIGO_NEW_PASSWORD}" == "${AMIGO_NEW_PASSWORD_CONFIRM}" ]] \
        || amigo_die "password confirmation does not match"
    (( ${#AMIGO_NEW_PASSWORD} >= 14 )) \
        || amigo_die "password must contain at least 14 characters"
    printf '%s\n' "${AMIGO_NEW_PASSWORD}" \
        | amigo_compose run --rm --no-deps --user 0 -T worker \
            python -m app.cli auth-set-password --password-stdin
    unset AMIGO_NEW_PASSWORD AMIGO_NEW_PASSWORD_CONFIRM
fi

if [[ ${SEND_TELEGRAM_TEST} -eq 1 ]]; then
    amigo_log "sending the explicitly authorized, labelled Telegram smoke message"
    amigo_compose run --rm --no-deps worker python -m app.cli telegram-test
else
    amigo_log "skipping the Telegram smoke message as explicitly selected"
fi

amigo_log "confirming Withings collection ownership remains with Amigo"
bash "${SCRIPT_DIR}/cron-control.sh" disable

amigo_log "performing incremental import with notifications suppressed"
amigo_compose run --rm --no-deps worker \
    python -m app.cli sync --suppress-notifications

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
if [[ -e "${AMIGO_LEGACY_WEIGHT_IMPORT}" ]] \
    && cmp --silent "${LEGACY_IMPORT_CANDIDATE}" "${AMIGO_LEGACY_WEIGHT_IMPORT}"; then
    amigo_log "legacy weight export is unchanged; keeping the existing rollback import"
    rm -- "${LEGACY_IMPORT_CANDIDATE}"
elif [[ -e "${AMIGO_LEGACY_WEIGHT_IMPORT}" ]]; then
    PRESERVED_IMPORT="${AMIGO_LEGACY_WEIGHT_IMPORT}.previous.$(date -u +%Y%m%dT%H%M%SZ)"
    readonly PRESERVED_IMPORT
    [[ ! -e "${PRESERVED_IMPORT}" ]] \
        || amigo_die "refusing to overwrite preserved legacy TSV: ${PRESERVED_IMPORT}"
    mv -- "${AMIGO_LEGACY_WEIGHT_IMPORT}" "${PRESERVED_IMPORT}"
    mv -- "${LEGACY_IMPORT_CANDIDATE}" "${AMIGO_LEGACY_WEIGHT_IMPORT}"
    chmod 0600 "${AMIGO_LEGACY_WEIGHT_IMPORT}"
else
    mv -- "${LEGACY_IMPORT_CANDIDATE}" "${AMIGO_LEGACY_WEIGHT_IMPORT}"
    chmod 0600 "${AMIGO_LEGACY_WEIGHT_IMPORT}"
fi

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

amigo_log "installing the verified signed Android 1.2.2 update"
ANDROID_APK_INSTALL_CANDIDATE="${AMIGO_ANDROID_APK}.candidate.$$"
install -o root -g root -m 0600 \
    "${ANDROID_APK_DOWNLOAD}" "${ANDROID_APK_INSTALL_CANDIDATE}"
[[ "$(sha256sum "${ANDROID_APK_INSTALL_CANDIDATE}" | awk '{ print $1 }')" \
    == "${ANDROID_APK_SHA256}" ]]
mv -- "${ANDROID_APK_INSTALL_CANDIDATE}" "${AMIGO_ANDROID_APK}"
ANDROID_APK_INSTALL_CANDIDATE=""
rm -f -- "${ANDROID_APK_DOWNLOAD}"
ANDROID_APK_DOWNLOAD=""

amigo_log "starting isolated Codex gateway and laboratory parser"
amigo_compose up -d --wait --wait-timeout 180 ai-gateway lab-parser
EXISTING_LAB_PARSER_STOPPED=0
amigo_compose run --rm --no-deps ai-worker python -m app.ai_smoke

amigo_log "preparing one exact current AI retry while the persistent AI worker is stopped"
amigo_compose run --rm --no-deps ai-worker \
    python -m app.cli ai-retry-current --worker-stopped
AI_ANALYSIS_READY=0
for ai_attempt in {1..4}; do
    if amigo_compose run --rm --no-deps ai-worker python -m app.cli ai-ready; then
        ai_ready_status=0
    else
        ai_ready_status=$?
    fi
    case "${ai_ready_status}" in
        0)
            AI_ANALYSIS_READY=1
            break
            ;;
        75)
            ;;
        *)
            amigo_die "AI readiness check returned unexpected status ${ai_ready_status}"
            ;;
    esac

    amigo_log "running bounded foreground AI analysis attempt ${ai_attempt}/4"
    amigo_compose run --rm --no-deps \
        --env AMIGO_WORKER_ONCE=true \
        ai-worker python -m app.ai_worker

    if amigo_compose run --rm --no-deps ai-worker python -m app.cli ai-ready; then
        ai_ready_status=0
    else
        ai_ready_status=$?
    fi
    case "${ai_ready_status}" in
        0)
            AI_ANALYSIS_READY=1
            break
            ;;
        75)
            ;;
        *)
            amigo_die "AI readiness check returned unexpected status ${ai_ready_status}"
            ;;
    esac
    if [[ ${ai_attempt} -lt 4 ]]; then
        amigo_log "current AI result is not ready; removing retry backoff before the next attempt"
        amigo_compose run --rm --no-deps ai-worker python -m app.cli ai-enqueue
    fi
done
[[ ${AI_ANALYSIS_READY} -eq 1 ]] \
    || amigo_die "current validated AI analysis was not ready after four foreground attempts"

amigo_log "starting the signed Health Connect ingestion endpoint"
amigo_compose up -d --wait --wait-timeout 180 ingest
EXISTING_INGEST_STOPPED=0

bash "${SCRIPT_DIR}/nginx-control.sh" enable "${SNAPSHOT}"
amigo_wait_for_origin_http_200 "/amigo/" 15 \
    || amigo_die "candidate Amigo origin did not stabilize at HTTP 200 after route enable"

amigo_log "starting the data and AI workers after legacy collection is disabled"
amigo_compose up -d --wait --wait-timeout 180 worker ai-worker
EXISTING_WORKER_STOPPED=0
EXISTING_AI_WORKER_STOPPED=0
CANDIDATE_RUNTIME_ACTIVE=1
bash "${SCRIPT_DIR}/verify-production.sh"
amigo_record_current_release "${RELEASE_SHA}"
CUTOVER_COMMITTED=1

amigo_log "runtime cutover passed; writing mandatory documentation and memory checkpoint"
bash "${SCRIPT_DIR}/checkpoint.sh" --verification-passed "${SNAPSHOT}"

trap - ERR HUP INT TERM
amigo_log "DEPLOYMENT COMPLETE: ${AMIGO_PUBLIC_URL}"
amigo_log "Git SHA: ${RELEASE_SHA}"
amigo_log "rollback snapshot: ${SNAPSHOT}"
amigo_log "checkpoint files must be committed back to the canonical repository"
