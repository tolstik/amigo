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
    awk bash chmod crontab curl date docker flock grep install mktemp nginx realpath rm sha256sum sleep stat
amigo_require_production_layout
amigo_assert_snapshot "${SNAPSHOT}"
amigo_acquire_deploy_lock

readonly PREVIOUS_COMPOSE="${SNAPSHOT}/release/compose.yaml"
[[ -f "${PREVIOUS_COMPOSE}" && ! -L "${PREVIOUS_COMPOSE}" ]] \
    || amigo_die "snapshot previous-release Compose file is missing or is a symlink"

PREVIOUS_RELEASE_SHA="$(amigo_snapshot_metadata_value "${SNAPSHOT}" previous_release_sha)"
PREVIOUS_IMAGE_REFERENCE="$(
    amigo_snapshot_metadata_value "${SNAPSHOT}" previous_application_image
)"
PREVIOUS_IMAGE_ID="$(
    amigo_snapshot_metadata_value "${SNAPSHOT}" previous_application_image_id
)"
PREVIOUS_IMAGE_ROLLBACK_REFERENCE="$(
    amigo_snapshot_metadata_value "${SNAPSHOT}" previous_application_rollback_image
)"
PREVIOUS_DATABASE_IMAGE_REFERENCE="$(
    amigo_snapshot_metadata_value "${SNAPSHOT}" previous_database_image
)"
PREVIOUS_DATABASE_IMAGE_ID="$(
    amigo_snapshot_metadata_value "${SNAPSHOT}" previous_database_image_id
)"
PREVIOUS_DATABASE_ROLLBACK_REFERENCE="$(
    amigo_snapshot_metadata_value "${SNAPSHOT}" previous_database_rollback_image
)"
PREVIOUS_AI_MODEL="$(amigo_snapshot_metadata_value "${SNAPSHOT}" previous_ai_model)"
PREVIOUS_AI_PROMPT_VERSION="$(
    amigo_snapshot_metadata_value "${SNAPSHOT}" previous_ai_prompt_version
)"
PREVIOUS_ROUTE_STATE="$(
    amigo_snapshot_metadata_value "${SNAPSHOT}" previous_managed_route_state
)"
PREVIOUS_CRON_STATE="$(
    amigo_snapshot_metadata_value "${SNAPSHOT}" legacy_withings_cron_state
)"
PREVIOUS_COMPOSE_SHA256="$(
    amigo_snapshot_metadata_value "${SNAPSHOT}" previous_compose_sha256
)"
CANDIDATE_RELEASE_SHA="$(amigo_snapshot_metadata_value "${SNAPSHOT}" candidate_git_sha)"
readonly PREVIOUS_RELEASE_SHA PREVIOUS_IMAGE_REFERENCE PREVIOUS_IMAGE_ID
readonly PREVIOUS_IMAGE_ROLLBACK_REFERENCE PREVIOUS_DATABASE_IMAGE_REFERENCE
readonly PREVIOUS_DATABASE_IMAGE_ID PREVIOUS_DATABASE_ROLLBACK_REFERENCE
readonly PREVIOUS_AI_MODEL PREVIOUS_AI_PROMPT_VERSION
readonly PREVIOUS_ROUTE_STATE PREVIOUS_CRON_STATE PREVIOUS_COMPOSE_SHA256 CANDIDATE_RELEASE_SHA

[[ "${PREVIOUS_RELEASE_SHA}" =~ ^[0-9a-f]{40,64}$ ]] \
    || amigo_die "snapshot previous release SHA is invalid"
[[ "${PREVIOUS_IMAGE_REFERENCE}" == "amigo:${PREVIOUS_RELEASE_SHA}" ]] \
    || amigo_die "snapshot previous image reference does not match its release SHA"
[[ "${PREVIOUS_IMAGE_ID}" =~ ^sha256:[0-9a-f]{64}$ ]] \
    || amigo_die "snapshot previous image ID is invalid"
[[ "${PREVIOUS_IMAGE_ROLLBACK_REFERENCE}" \
    == "amigo-rollback:${PREVIOUS_RELEASE_SHA}-${PREVIOUS_IMAGE_ID:7:12}" ]] \
    || amigo_die "snapshot previous application rollback image is invalid"
[[ "${PREVIOUS_DATABASE_IMAGE_REFERENCE}" == "postgres:17-alpine" ]] \
    || amigo_die "snapshot previous database image reference is invalid"
[[ "${PREVIOUS_DATABASE_IMAGE_ID}" =~ ^sha256:[0-9a-f]{64}$ ]] \
    || amigo_die "snapshot previous database image ID is invalid"
[[ "${PREVIOUS_DATABASE_ROLLBACK_REFERENCE}" \
    == "amigo-postgres-rollback:${PREVIOUS_DATABASE_IMAGE_ID:7}" ]] \
    || amigo_die "snapshot previous database rollback image is invalid"
[[ "${PREVIOUS_AI_MODEL}" =~ ^[A-Za-z0-9._-]{1,64}$ ]] \
    || amigo_die "snapshot previous AI model is invalid"
[[ "${PREVIOUS_AI_PROMPT_VERSION}" =~ ^[A-Za-z0-9._-]{1,64}$ ]] \
    || amigo_die "snapshot previous AI prompt version is invalid"
[[ "${PREVIOUS_ROUTE_STATE}" == "enabled" ]] \
    || amigo_die "snapshot was not taken from an active managed Amigo route"
[[ "${PREVIOUS_CRON_STATE}" == "disabled" ]] \
    || amigo_die "snapshot was not taken with legacy Withings collection disabled"
[[ "${PREVIOUS_COMPOSE_SHA256}" =~ ^[0-9a-f]{64}$ ]] \
    || amigo_die "snapshot previous Compose hash is invalid"
[[ "${CANDIDATE_RELEASE_SHA}" =~ ^[0-9a-f]{40,64}$ ]] \
    || amigo_die "snapshot candidate release SHA is invalid"
[[ "$(sha256sum "${PREVIOUS_COMPOSE}" | awk '{ print $1 }')" \
    == "${PREVIOUS_COMPOSE_SHA256}" ]] \
    || amigo_die "snapshot previous Compose file does not match its metadata"
RECORDED_RELEASE_SHA="$(amigo_recorded_release)"
readonly RECORDED_RELEASE_SHA
[[ "${RECORDED_RELEASE_SHA}" == "${PREVIOUS_RELEASE_SHA}" \
    || "${RECORDED_RELEASE_SHA}" == "${CANDIDATE_RELEASE_SHA}" ]] \
    || amigo_die "recorded deployed release no longer matches this recovery snapshot"

CURRENT_PREVIOUS_IMAGE_ID="$(docker image inspect --format '{{.Id}}' \
    "${PREVIOUS_IMAGE_ROLLBACK_REFERENCE}")" \
    || amigo_die "preserved previous application image is no longer installed"
readonly CURRENT_PREVIOUS_IMAGE_ID
[[ "${CURRENT_PREVIOUS_IMAGE_ID}" == "${PREVIOUS_IMAGE_ID}" ]] \
    || amigo_die "preserved previous application image ID differs from the snapshot"
amigo_assert_image_revision "${PREVIOUS_IMAGE_ROLLBACK_REFERENCE}" "${PREVIOUS_RELEASE_SHA}"
CURRENT_PREVIOUS_DATABASE_IMAGE_ID="$(docker image inspect --format '{{.Id}}' \
    "${PREVIOUS_DATABASE_ROLLBACK_REFERENCE}")" \
    || amigo_die "preserved previous PostgreSQL image is no longer installed"
readonly CURRENT_PREVIOUS_DATABASE_IMAGE_ID
[[ "${CURRENT_PREVIOUS_DATABASE_IMAGE_ID}" == "${PREVIOUS_DATABASE_IMAGE_ID}" ]] \
    || amigo_die "preserved previous PostgreSQL image ID differs from the snapshot"
amigo_compose_file_release \
    "${PREVIOUS_COMPOSE}" "${PREVIOUS_RELEASE_SHA}" config --quiet

if ! amigo_assert_managed_route_active || ! amigo_assert_legacy_cron_disabled; then
    amigo_log "previous-release recovery is valid only while the managed Amigo route owns production"
    amigo_log "from legacy state use: sudo ${SCRIPT_DIR}/takeover-from-legacy.sh --resume-recorded-release ${SNAPSHOT}"
    exit 1
fi

RECOVERY_STARTED=0
RECOVERY_COMMITTED=0
AI_WORKER_DEGRADED=0

recovery_error() {
    local status=$1
    local line=$2
    trap - ERR
    trap '' HUP INT TERM
    set +e
    amigo_log "previous-release recovery failed at line ${line} (status ${status})"
    if [[ ${RECOVERY_STARTED} -eq 1 && ${RECOVERY_COMMITTED} -eq 0 ]]; then
        amigo_log "stopping Amigo collectors fail-closed; legacy collection remains disabled"
        amigo_compose_file_release \
            "${PREVIOUS_COMPOSE}" "${PREVIOUS_RELEASE_SHA}" stop worker ai-worker
        bash "${SCRIPT_DIR}/cron-control.sh" disable \
            || amigo_log "WARNING: could not confirm the disabled legacy Withings cron"
    fi
    amigo_log "legacy disaster fallback was NOT activated automatically"
    amigo_log "manual disaster fallback requires: sudo ${SCRIPT_DIR}/rollback.sh --to-legacy ${SNAPSHOT}"
    exit "${status}"
}
trap 'recovery_error "$?" "${LINENO}"' ERR
trap 'recovery_error 129 "${LINENO}"' HUP
trap 'recovery_error 130 "${LINENO}"' INT
trap 'recovery_error 143 "${LINENO}"' TERM

RECOVERY_STARTED=1
amigo_log "stopping candidate application services before immutable release recovery"
amigo_compose_file_release \
    "${PREVIOUS_COMPOSE}" "${PREVIOUS_RELEASE_SHA}" \
    stop worker ai-worker ingest ai-gateway web

amigo_log "restoring exact application and PostgreSQL image references from protected rollback tags"
docker image tag "${PREVIOUS_IMAGE_ROLLBACK_REFERENCE}" "${PREVIOUS_IMAGE_REFERENCE}"
docker image tag "${PREVIOUS_DATABASE_ROLLBACK_REFERENCE}" "${PREVIOUS_DATABASE_IMAGE_REFERENCE}"
[[ "$(docker image inspect --format '{{.Id}}' "${PREVIOUS_IMAGE_REFERENCE}")" \
    == "${PREVIOUS_IMAGE_ID}" ]]
[[ "$(docker image inspect --format '{{.Id}}' "${PREVIOUS_DATABASE_IMAGE_REFERENCE}")" \
    == "${PREVIOUS_DATABASE_IMAGE_ID}" ]]

amigo_log "recreating PostgreSQL with its exact previous image and preserved data volume"
amigo_compose_file_release \
    "${PREVIOUS_COMPOSE}" "${PREVIOUS_RELEASE_SHA}" \
    up -d --no-build --force-recreate db
for attempt in {1..60}; do
    if amigo_compose_file_release \
        "${PREVIOUS_COMPOSE}" "${PREVIOUS_RELEASE_SHA}" \
        exec -T db pg_isready -U amigo -d amigo >/dev/null 2>&1; then
        break
    fi
    [[ ${attempt} -lt 60 ]] || amigo_die "PostgreSQL did not become ready during recovery"
    sleep 2
done
recovered_database_container=$(amigo_compose_file_release \
    "${PREVIOUS_COMPOSE}" "${PREVIOUS_RELEASE_SHA}" ps -q db)
[[ -n "${recovered_database_container}" ]] \
    || amigo_die "recovered PostgreSQL container is missing"
[[ "$(docker inspect --format '{{.Config.Image}}' "${recovered_database_container}")" \
    == "${PREVIOUS_DATABASE_IMAGE_REFERENCE}" ]] \
    || amigo_die "recovered PostgreSQL uses the wrong image reference"
[[ "$(docker inspect --format '{{.Image}}' "${recovered_database_container}")" \
    == "${PREVIOUS_DATABASE_IMAGE_ID}" ]] \
    || amigo_die "recovered PostgreSQL uses the wrong immutable image ID"

amigo_log "confirming exclusive Withings ownership for the previous Amigo worker"
bash "${SCRIPT_DIR}/cron-control.sh" disable

amigo_log "starting previous web, ingest, and isolated AI gateway images"
amigo_compose_file_release \
    "${PREVIOUS_COMPOSE}" "${PREVIOUS_RELEASE_SHA}" \
    up -d --no-build --no-deps --force-recreate --wait --wait-timeout 180 \
    web ingest ai-gateway
amigo_wait_for_http "${AMIGO_DIRECT_HEALTH_URL}" 60 \
    || amigo_die "previous web health endpoint did not become ready"
amigo_wait_for_http "http://127.0.0.1:18182/healthz" 60 \
    || amigo_die "previous ingest health endpoint did not become ready"

amigo_log "restoring the exact pre-cutover managed nginx files"
bash "${SCRIPT_DIR}/nginx-control.sh" restore "${SNAPSHOT}"
amigo_wait_for_origin_http_200 "/amigo/api/v1/overview" 15 \
    || amigo_die "previous Amigo origin did not stabilize at HTTP 200 after route restore"

AI_TABLE_STATE="$(
    amigo_compose_file_release \
        "${PREVIOUS_COMPOSE}" "${PREVIOUS_RELEASE_SHA}" \
        exec -T db psql \
            --username amigo \
            --dbname amigo \
            --no-psqlrc \
            --quiet \
            --tuples-only \
            --no-align \
            --set=ON_ERROR_STOP=1 \
            --command "SELECT CASE WHEN to_regclass('public.ai_analysis_jobs') IS NULL THEN 'absent' ELSE 'present' END;"
)" || AI_TABLE_STATE="error"
readonly AI_TABLE_STATE

if [[ "${AI_TABLE_STATE}" == "present" ]]; then
    amigo_log "superseding only active AI jobs incompatible with the previous model contract"
    if ! amigo_compose_file_release \
        "${PREVIOUS_COMPOSE}" "${PREVIOUS_RELEASE_SHA}" \
        exec -T db psql \
            --username amigo \
            --dbname amigo \
            --no-psqlrc \
            --quiet \
            --set=ON_ERROR_STOP=1 \
            --command "
                UPDATE ai_analysis_jobs
                SET status = 'superseded',
                    finished_at = CURRENT_TIMESTAMP,
                    lease_until = NULL,
                    last_error_code = NULL
                WHERE status IN ('pending', 'processing')
                  AND (
                      model <> '${PREVIOUS_AI_MODEL}'
                      OR prompt_version <> '${PREVIOUS_AI_PROMPT_VERSION}'
                  );
            " >/dev/null; then
        AI_WORKER_DEGRADED=1
        amigo_log "WARNING: AI job metadata cleanup failed; previous AI worker will remain stopped"
    fi
elif [[ "${AI_TABLE_STATE}" == "absent" ]]; then
    AI_WORKER_DEGRADED=1
    amigo_log "WARNING: AI job table is absent; previous AI worker will remain stopped"
else
    AI_WORKER_DEGRADED=1
    amigo_log "WARNING: AI job metadata could not be inspected; previous AI worker will remain stopped"
fi

RECOVERY_MINUTE_BUCKET="$(date -u +%Y%m%dT%H%M)"
readonly RECOVERY_MINUTE_BUCKET
amigo_log "waiting for a fresh worker run-key minute before starting the previous data worker"
while [[ "$(date -u +%Y%m%dT%H%M)" == "${RECOVERY_MINUTE_BUCKET}" ]]; do
    sleep 1
done

amigo_log "starting the previous data worker"
amigo_compose_file_release \
    "${PREVIOUS_COMPOSE}" "${PREVIOUS_RELEASE_SHA}" \
    up -d --no-build --no-deps --force-recreate --wait --wait-timeout 180 worker
if [[ ${AI_WORKER_DEGRADED} -eq 0 ]]; then
    amigo_log "starting the previous AI worker after incompatible jobs were contained"
    amigo_compose_file_release \
        "${PREVIOUS_COMPOSE}" "${PREVIOUS_RELEASE_SHA}" \
        up -d --no-build --no-deps --force-recreate --wait --wait-timeout 180 ai-worker
else
    amigo_compose_file_release \
        "${PREVIOUS_COMPOSE}" "${PREVIOUS_RELEASE_SHA}" \
        create --no-build --force-recreate ai-worker >/dev/null
    amigo_compose_file_release \
        "${PREVIOUS_COMPOSE}" "${PREVIOUS_RELEASE_SHA}" stop ai-worker >/dev/null
fi

for previous_service in web worker ingest ai-worker ai-gateway; do
    previous_container=$(amigo_compose_file_release \
        "${PREVIOUS_COMPOSE}" "${PREVIOUS_RELEASE_SHA}" \
        ps --all -q "${previous_service}")
    [[ -n "${previous_container}" ]] \
        || amigo_die "recovered service container is missing: ${previous_service}"
    [[ "$(docker inspect --format '{{.Config.Image}}' "${previous_container}")" \
        == "${PREVIOUS_IMAGE_REFERENCE}" ]] \
        || amigo_die "recovered ${previous_service} does not use the previous image reference"
    [[ "$(docker inspect --format '{{.Image}}' "${previous_container}")" \
        == "${PREVIOUS_IMAGE_ID}" ]] \
        || amigo_die "recovered ${previous_service} does not use the previous image ID"
done

for healthy_service in db web worker ingest ai-gateway; do
    healthy_container=$(amigo_compose_file_release \
        "${PREVIOUS_COMPOSE}" "${PREVIOUS_RELEASE_SHA}" ps -q "${healthy_service}")
    [[ -n "${healthy_container}" ]] || amigo_die "recovered service is not running: ${healthy_service}"
    [[ "$(docker inspect --format '{{.State.Health.Status}}' "${healthy_container}")" == "healthy" ]] \
        || amigo_die "recovered service is not healthy: ${healthy_service}"
done
if [[ ${AI_WORKER_DEGRADED} -eq 0 ]]; then
    ai_worker_container=$(amigo_compose_file_release \
        "${PREVIOUS_COMPOSE}" "${PREVIOUS_RELEASE_SHA}" ps -q ai-worker)
    [[ -n "${ai_worker_container}" ]] || amigo_die "recovered AI worker is not running"
    [[ "$(docker inspect --format '{{.State.Health.Status}}' "${ai_worker_container}")" == "healthy" ]] \
        || amigo_die "recovered AI worker is not healthy"
fi

worker_container=$(amigo_compose_file_release \
    "${PREVIOUS_COMPOSE}" "${PREVIOUS_RELEASE_SHA}" ps -q worker)
WORKER_STARTED_AT="$(docker inspect --format '{{.State.StartedAt}}' "${worker_container}")"
readonly WORKER_STARTED_AT
[[ "${WORKER_STARTED_AT}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?Z$ ]] \
    || amigo_die "recovered worker has an invalid Docker StartedAt timestamp"
WORKER_INCREMENTAL_VERIFIED=0
for ((attempt = 1; attempt <= 60; attempt += 1)); do
    worker_incremental_state="$(
        amigo_compose_file_release \
            "${PREVIOUS_COMPOSE}" "${PREVIOUS_RELEASE_SHA}" \
            exec -T db psql \
                --username amigo \
                --dbname amigo \
                --no-psqlrc \
                --quiet \
                --tuples-only \
                --no-align \
                --set=ON_ERROR_STOP=1 \
                --command "
                    SELECT COALESCE(
                        (
                            SELECT CASE
                                WHEN status = 'success' AND finished_at IS NOT NULL THEN 'success'
                                WHEN status = 'failed' AND finished_at IS NOT NULL THEN 'failed'
                                ELSE 'waiting'
                            END
                            FROM job_runs
                            WHERE job_name = 'withings-incremental'
                              AND started_at >= '${WORKER_STARTED_AT}'::timestamptz
                            ORDER BY started_at DESC, id DESC
                            LIMIT 1
                        ),
                        'waiting'
                    );
                "
    )"
    case "${worker_incremental_state}" in
        success)
            WORKER_INCREMENTAL_VERIFIED=1
            break
            ;;
        failed)
            amigo_die "recovered worker's post-start Withings incremental job failed"
            ;;
        waiting)
            ;;
        *)
            amigo_die "unexpected recovered worker verification state"
            ;;
    esac
    [[ ${attempt} -lt 60 ]] && sleep 3
done
[[ ${WORKER_INCREMENTAL_VERIFIED} -eq 1 ]] \
    || amigo_die "recovered worker did not complete a post-start Withings incremental job"

CRONTAB_FILE="$(mktemp /run/amigo-recovery-crontab.XXXXXX)"
readonly CRONTAB_FILE
crontab -u "${AMIGO_LEGACY_CRON_USER}" -l >"${CRONTAB_FILE}"
[[ "$(amigo_count_exact_line "${AMIGO_LEGACY_WITHINGS_CRON_LINE}" "${CRONTAB_FILE}")" -eq 0 ]]
[[ "$(amigo_count_exact_line "${AMIGO_LEGACY_WITHINGS_DISABLED_LINE}" "${CRONTAB_FILE}")" -eq 1 ]]
[[ "$(amigo_count_exact_line "${AMIGO_SHARED_TELEGRAM_CRON_LINE}" "${CRONTAB_FILE}")" -ge 1 ]]
rm -f -- "${CRONTAB_FILE}"

amigo_record_current_release "${PREVIOUS_RELEASE_SHA}"
[[ "$(amigo_recorded_release)" == "${PREVIOUS_RELEASE_SHA}" ]] \
    || amigo_die "recovered release marker could not be verified"
RECOVERY_COMMITTED=1
trap - ERR HUP INT TERM

RECOVERED_AT="$(date -u +%Y%m%dT%H%M%SZ)"
readonly RECOVERED_AT
if ! (
    install -d -o root -g root -m 0700 "${AMIGO_STATE_DIR}" "${AMIGO_STATE_DIR}/recoveries"
    {
        printf 'recovered_at_utc=%s\n' "${RECOVERED_AT}"
        printf 'snapshot=%s\n' "${SNAPSHOT}"
        printf 'release_sha=%s\n' "${PREVIOUS_RELEASE_SHA}"
        printf 'application_image_id=%s\n' "${PREVIOUS_IMAGE_ID}"
        printf 'database_image_id=%s\n' "${PREVIOUS_DATABASE_IMAGE_ID}"
        if [[ ${AI_WORKER_DEGRADED} -eq 0 ]]; then
            printf 'ai_worker=running\n'
        else
            printf 'ai_worker=stopped_degraded\n'
        fi
        printf 'legacy_withings_cron=disabled\n'
        printf 'database_restore=not_performed\n'
    } >"${AMIGO_STATE_DIR}/recoveries/${RECOVERED_AT}.txt"
    chmod 0600 "${AMIGO_STATE_DIR}/recoveries/${RECOVERED_AT}.txt"
); then
    amigo_log "WARNING: recovered runtime is healthy but its local audit record could not be written"
fi

amigo_log "PREVIOUS RELEASE RECOVERED: ${PREVIOUS_RELEASE_SHA}"
amigo_log "managed Amigo route is active; legacy collector remains disabled"
if [[ ${AI_WORKER_DEGRADED} -eq 1 ]]; then
    amigo_log "AI worker is intentionally stopped; deterministic dashboard and data collection are healthy"
fi
