#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
# shellcheck source=lib/common.sh
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

amigo_require_root
amigo_require_commands \
    awk cmp crontab curl dirname docker git grep install mariadb mktemp nginx python3 rm rmdir \
    sha256sum sleep ss stat visudo
amigo_require_production_layout

TMP_DIR="$(mktemp -d /run/amigo-verify.XXXXXX)"
readonly TMP_DIR
readonly VERIFICATION_DIR="${TMP_DIR}/verification"
readonly SESSION_DESCRIPTOR="${VERIFICATION_DIR}/session.json"
readonly AUTH_CURL_CONFIG="${TMP_DIR}/auth.curl"
readonly ORIGIN_NO_CSRF_CURL_CONFIG="${TMP_DIR}/origin-no-csrf.curl"
readonly DASHBOARD_HEADERS="${TMP_DIR}/dashboard.headers"
readonly DASHBOARD_BODY="${TMP_DIR}/dashboard.body"
readonly API_HEADERS="${TMP_DIR}/api.headers"
readonly API_BODY="${TMP_DIR}/api.body"
readonly REPORT_HEADERS="${TMP_DIR}/doctor-report.headers"
readonly REPORT_BODY="${VERIFICATION_DIR}/doctor-report.json"
readonly REPORT_PDF_HEADERS="${TMP_DIR}/doctor-report-pdf.headers"
readonly REPORT_PDF_BODY="${VERIFICATION_DIR}/doctor-report.pdf"
readonly REPORT_HTML_HEADERS="${TMP_DIR}/doctor-report-html.headers"
readonly REPORT_HTML_BODY="${VERIFICATION_DIR}/doctor-report.html"
readonly CSV_HEADERS="${TMP_DIR}/csv.headers"
readonly CSV_BODY="${TMP_DIR}/csv.body"
readonly INGEST_HEADERS="${TMP_DIR}/ingest.headers"
readonly INGEST_BODY="${TMP_DIR}/ingest.body"
readonly UPLOAD_HEADERS="${TMP_DIR}/upload.headers"
readonly UPLOAD_BODY="${TMP_DIR}/upload.body"
readonly UNSUPPORTED_FILE="${TMP_DIR}/unsupported.txt"
readonly SSE_ORIGIN_HEADERS="${TMP_DIR}/sse-origin.headers"
readonly SSE_HEADERS="${TMP_DIR}/sse.headers"
readonly SSE_BODY="${TMP_DIR}/sse.body"
readonly APK_HEADERS="${TMP_DIR}/apk.headers"
readonly APK_BODY="${TMP_DIR}/amigo-sync.apk"
readonly ASSET_HEADERS="${TMP_DIR}/asset.headers"
readonly ASSETLINKS_HEADERS="${TMP_DIR}/assetlinks.headers"
readonly ASSETLINKS_BODY="${TMP_DIR}/assetlinks.json"
readonly REDIRECT_HEADERS="${TMP_DIR}/redirect.headers"
readonly CRONTAB_FILE="${TMP_DIR}/tolstik.crontab"
DOCTOR_REPORT_ID=""

cleanup() {
    if [[ -n "${DOCTOR_REPORT_ID}" && -f "${AUTH_CURL_CONFIG}" ]]; then
        curl --config "${AUTH_CURL_CONFIG}" \
            --request DELETE \
            --output /dev/null \
            "${AMIGO_PUBLIC_URL}api/v1/reports/doctor/${DOCTOR_REPORT_ID}" \
            >/dev/null 2>&1 || true
    fi
    rm -f -- \
        "${SESSION_DESCRIPTOR}" \
        "${AUTH_CURL_CONFIG}" \
        "${ORIGIN_NO_CSRF_CURL_CONFIG}" \
        "${DASHBOARD_HEADERS}" \
        "${DASHBOARD_BODY}" \
        "${API_HEADERS}" \
        "${API_BODY}" \
        "${REPORT_HEADERS}" \
        "${REPORT_BODY}" \
        "${REPORT_PDF_HEADERS}" \
        "${REPORT_PDF_BODY}" \
        "${REPORT_HTML_HEADERS}" \
        "${REPORT_HTML_BODY}" \
        "${CSV_HEADERS}" \
        "${CSV_BODY}" \
        "${INGEST_HEADERS}" \
        "${INGEST_BODY}" \
        "${UPLOAD_HEADERS}" \
        "${UPLOAD_BODY}" \
        "${UNSUPPORTED_FILE}" \
        "${SSE_ORIGIN_HEADERS}" \
        "${SSE_HEADERS}" \
        "${SSE_BODY}" \
        "${APK_HEADERS}" \
        "${APK_BODY}" \
        "${ASSET_HEADERS}" \
        "${ASSETLINKS_HEADERS}" \
        "${ASSETLINKS_BODY}" \
        "${REDIRECT_HEADERS}" \
        "${CRONTAB_FILE}"
    rmdir -- "${VERIFICATION_DIR}" 2>/dev/null || true
    rmdir -- "${TMP_DIR}" 2>/dev/null || true
}
trap cleanup EXIT
install -d -o root -g root -m 0700 "${VERIFICATION_DIR}"

readonly INSTALLED_RELEASE_WRAPPER="/usr/local/sbin/amigo-release"
readonly INSTALLED_RELEASE_SUDOERS="/etc/sudoers.d/amigo-release"
for release_access_file in \
    "${INSTALLED_RELEASE_WRAPPER}" "${INSTALLED_RELEASE_SUDOERS}"; do
    [[ -f "${release_access_file}" && ! -L "${release_access_file}" ]] \
        || amigo_die "release access file is missing or is a symlink: ${release_access_file}"
    [[ "$(stat -c '%U:%G' "${release_access_file}")" == "root:root" ]] \
        || amigo_die "release access file is not owned by root:root: ${release_access_file}"
done
[[ "$(stat -c '%a' "${INSTALLED_RELEASE_WRAPPER}")" == "755" ]] \
    || amigo_die "installed release wrapper mode is not 0755"
[[ "$(stat -c '%a' "${INSTALLED_RELEASE_SUDOERS}")" == "440" ]] \
    || amigo_die "installed release sudoers mode is not 0440"
cmp --silent "${SCRIPT_DIR}/amigo-release" "${INSTALLED_RELEASE_WRAPPER}" \
    || amigo_die "installed release wrapper differs from the deployed release"
cmp --silent "${SCRIPT_DIR}/sudoers/amigo-release" "${INSTALLED_RELEASE_SUDOERS}" \
    || amigo_die "installed release sudoers policy differs from the deployed release"
visudo -cf /etc/sudoers >/dev/null
amigo_log "PASS unattended release access is root-owned and limited to the validated wrapper"

check_service() {
    local service=$1
    local container_id
    local state
    local health

    container_id=$(amigo_compose ps -q "${service}")
    [[ -n "${container_id}" ]] || amigo_die "Compose service has no container: ${service}"
    state=$(docker inspect --format '{{.State.Status}}' "${container_id}")
    [[ "${state}" == "running" ]] \
        || amigo_die "Compose service is not running: ${service} (${state})"
    health=$(docker inspect \
        --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
        "${container_id}")
    [[ "${health}" == "healthy" ]] \
        || amigo_die "Compose service is not healthy: ${service} (${health})"
    amigo_log "PASS service ${service}: running, health=${health}"
}

secret_destinations() {
    local container_id=$1
    docker inspect "${container_id}" | python3 -c '
import json, sys
mounts = json.load(sys.stdin)[0]["Mounts"]
print(" ".join(sorted(
    mount["Destination"]
    for mount in mounts
    if mount["Destination"].startswith("/run/secrets/")
)))
'
}

container_networks() {
    local container_id=$1
    docker inspect "${container_id}" | python3 -c '
import json, sys
networks = json.load(sys.stdin)[0]["NetworkSettings"].get("Networks") or {}
print(" ".join(sorted(networks)))
'
}

require_service_networks() {
    local service=$1
    local expected=$2
    local container_id
    local actual

    container_id=$(amigo_compose ps -q "${service}")
    actual=$(container_networks "${container_id}")
    [[ "${actual}" == "${expected}" ]] \
        || amigo_die "${service} network membership is '${actual}', expected exactly '${expected}'"
}

require_header() {
    local header_pattern=$1
    local file=$2
    grep --ignore-case --quiet --extended-regexp "${header_pattern}" "${file}" \
        || amigo_die "required response header is missing: ${header_pattern}"
}

public_status() {
    local path=$1
    local method=${2:-GET}
    curl --silent --show-error --max-time 20 \
        --proto '=https' \
        --tlsv1.2 \
        --request "${method}" \
        --output /dev/null \
        --write-out '%{http_code}' \
        "${AMIGO_PUBLIC_URL}${path}"
}

published_port_count() {
    local container_id=$1
    docker inspect "${container_id}" | python3 -c '
import json, sys
ports = json.load(sys.stdin)[0]["NetworkSettings"].get("Ports") or {}
print(sum(bool(bindings) for bindings in ports.values()))
'
}

amigo_log "validating the exact seven-service Compose topology"
amigo_compose config --quiet
mapfile -t COMPOSE_SERVICES < <(amigo_compose config --services)
[[ ${#COMPOSE_SERVICES[@]} -eq 7 ]] \
    || amigo_die "Compose must contain exactly seven services"
for expected_service in db web worker ingest ai-worker ai-gateway lab-parser; do
    service_found=0
    for configured_service in "${COMPOSE_SERVICES[@]}"; do
        [[ "${configured_service}" == "${expected_service}" ]] && service_found=1
    done
    [[ ${service_found} -eq 1 ]] \
        || amigo_die "Compose is missing required service: ${expected_service}"
    check_service "${expected_service}"
done
amigo_compose exec -T db pg_isready -U amigo -d amigo >/dev/null

require_service_networks db "amigo_backend"
require_service_networks web "amigo_backend"
require_service_networks worker "amigo_backend"
require_service_networks ingest "amigo_backend"
require_service_networks ai-worker "amigo_ai_private amigo_backend amigo_lab_private"
require_service_networks ai-gateway "amigo_ai_private"
require_service_networks lab-parser "amigo_lab_private"
amigo_log "PASS every service has exactly the expected Docker network membership"

CURRENT_RELEASE="$(amigo_current_release)"
EXPECTED_IMAGE="amigo:${CURRENT_RELEASE}"
readonly CURRENT_RELEASE EXPECTED_IMAGE
for application_service in web worker ingest ai-worker ai-gateway lab-parser; do
    application_container=$(amigo_compose ps -q "${application_service}")
    actual_image=$(docker inspect --format '{{.Config.Image}}' "${application_container}")
    [[ "${actual_image}" == "${EXPECTED_IMAGE}" ]] \
        || amigo_die "${application_service} runs ${actual_image}, expected immutable ${EXPECTED_IMAGE}"
    actual_revision=$(docker inspect \
        --format '{{if .Config.Labels}}{{index .Config.Labels "org.opencontainers.image.revision"}}{{end}}' \
        "${application_container}")
    [[ "${actual_revision}" == "${CURRENT_RELEASE}" ]] \
        || amigo_die "${application_service} OCI revision differs from ${CURRENT_RELEASE}"
done
db_container=$(amigo_compose ps -q db)
[[ "$(docker inspect --format '{{.Config.Image}}' "${db_container}")" == "postgres:17-alpine" ]] \
    || amigo_die "db does not use postgres:17-alpine"
amigo_log "PASS all six application services use the release image and PostgreSQL uses its pinned tag"

worker_container=$(amigo_compose ps -q worker)
web_container=$(amigo_compose ps -q web)
ai_worker_container=$(amigo_compose ps -q ai-worker)
gateway_container=$(amigo_compose ps -q ai-gateway)
parser_container=$(amigo_compose ps -q lab-parser)
readonly POSTGRES_SECRET_DESTINATION="/run/secrets/postgres_password"
readonly WORKER_SECRET_DESTINATIONS="/run/secrets/app_encryption_key /run/secrets/postgres_password /run/secrets/telegram_bot_token /run/secrets/telegram_chat_id /run/secrets/withings_access_token /run/secrets/withings_client_id /run/secrets/withings_client_secret /run/secrets/withings_refresh_token"
for postgres_only_service in web ingest ai-worker; do
    postgres_only_container=$(amigo_compose ps -q "${postgres_only_service}")
    [[ "$(secret_destinations "${postgres_only_container}")" == "${POSTGRES_SECRET_DESTINATION}" ]] \
        || amigo_die "${postgres_only_service} has unexpected secret mounts"
done
[[ "$(secret_destinations "${worker_container}")" == "${WORKER_SECRET_DESTINATIONS}" ]] \
    || amigo_die "worker does not have exactly the expected eight secret mounts"
[[ -z "$(secret_destinations "${gateway_container}")" ]] \
    || amigo_die "AI gateway unexpectedly receives Docker secrets"
[[ -z "$(secret_destinations "${parser_container}")" ]] \
    || amigo_die "laboratory parser unexpectedly receives Docker secrets"
amigo_log "PASS PostgreSQL-only, integration, and zero-secret container boundaries"

ai_worker_environment="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "${ai_worker_container}")"
grep --fixed-strings --line-regexp --quiet 'AMIGO_ENV=production' <<<"${ai_worker_environment}" \
    || amigo_die "AI worker is not running with production settings"
grep --fixed-strings --line-regexp --quiet 'AMIGO_AI_ENABLED=true' <<<"${ai_worker_environment}" \
    || amigo_die "AI worker does not have generated analysis enabled"
grep --fixed-strings --line-regexp --quiet \
    'AMIGO_AI_GATEWAY_URL=http://ai-gateway:8090' <<<"${ai_worker_environment}" \
    || amigo_die "AI worker can send snapshots outside the isolated gateway"
grep --fixed-strings --line-regexp --quiet \
    'AMIGO_AI_GATEWAY_TIMEOUT_SECONDS=180' <<<"${ai_worker_environment}" \
    || amigo_die "AI worker does not use the bounded routine-analysis timeout"
grep --fixed-strings --line-regexp --quiet \
    'AMIGO_LAB_PARSER_URL=http://lab-parser:8085' <<<"${ai_worker_environment}" \
    || amigo_die "AI worker does not use the isolated laboratory parser"
grep --fixed-strings --line-regexp --quiet \
    'AMIGO_USER_HEIGHT_CM=176' <<<"${ai_worker_environment}" \
    || amigo_die "AI worker does not use the configured 176 cm profile height"
gateway_environment="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "${gateway_container}")"
grep --fixed-strings --line-regexp --quiet \
    'AMIGO_AI_CODEX_TIMEOUT_SECONDS=75' <<<"${gateway_environment}" \
    || amigo_die "non-analysis Codex contracts do not retain their fixed deadline"
grep --fixed-strings --line-regexp --quiet \
    'AMIGO_AI_ANALYSIS_TIMEOUT_SECONDS=150' <<<"${gateway_environment}" \
    || amigo_die "routine analysis does not use its separate bounded deadline"
for isolated_container in "${gateway_container}" "${parser_container}"; do
    isolated_environment="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "${isolated_container}")"
    if grep --quiet --extended-regexp '^(DATABASE_URL|POSTGRES_PASSWORD_FILE|AMIGO_ENCRYPTION_KEY_FILE)=' \
        <<<"${isolated_environment}"; then
        amigo_die "isolated inference/parser container unexpectedly receives database configuration"
    fi
done
amigo_log "PASS local AI and parser boundaries are pinned and database-free"

worker_environment="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "${worker_container}")"
grep --fixed-strings --line-regexp --quiet 'AMIGO_WEEKLY_DIGEST_DAY=mon' <<<"${worker_environment}" \
    || amigo_die "worker weekly digest day differs from Monday"
grep --fixed-strings --line-regexp --quiet 'AMIGO_WEEKLY_DIGEST_TIME=09:00' <<<"${worker_environment}" \
    || amigo_die "worker weekly digest time differs from 09:00"
grep --fixed-strings --line-regexp --quiet 'AMIGO_DAILY_DIGEST_TIME=09:00' <<<"${worker_environment}" \
    || amigo_die "worker daily digest time differs from 09:00"
amigo_log "PASS Telegram daily/weekly schedule is pinned to 09:00 Europe/Moscow"

WORKER_STARTED_AT="$(docker inspect --format '{{.State.StartedAt}}' "${worker_container}")"
readonly WORKER_STARTED_AT
[[ "${WORKER_STARTED_AT}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?Z$ ]] \
    || amigo_die "current worker has an invalid Docker StartedAt timestamp"
WORKER_INCREMENTAL_VERIFIED=0
for ((attempt = 1; attempt <= 30; attempt += 1)); do
    WORKER_INCREMENTAL_STATE="$(
        amigo_compose exec -T db psql \
            --username amigo \
            --dbname amigo \
            --no-psqlrc \
            --quiet \
            --tuples-only \
            --no-align \
            --set=ON_ERROR_STOP=1 \
            --command "
                WITH post_start_runs AS (
                    SELECT status, finished_at
                    FROM job_runs
                    WHERE job_name = 'withings-incremental'
                      AND started_at >= '${WORKER_STARTED_AT}'::timestamptz
                    ORDER BY started_at DESC, id DESC
                    LIMIT 1
                )
                SELECT COALESCE(
                    (
                        SELECT CASE
                            WHEN status = 'success' AND finished_at IS NOT NULL THEN 'success'
                            WHEN status = 'failed' AND finished_at IS NOT NULL THEN 'failed'
                            ELSE 'waiting'
                        END
                        FROM post_start_runs
                    ),
                    'waiting'
                );
            "
    )"
    case "${WORKER_INCREMENTAL_STATE}" in
        success)
            WORKER_INCREMENTAL_VERIFIED=1
            break
            ;;
        failed)
            amigo_die "current worker's post-start Withings incremental job failed"
            ;;
        waiting)
            ;;
        *)
            amigo_die "unexpected privacy-safe worker verification result"
            ;;
    esac
    [[ ${attempt} -lt 30 ]] && sleep 3
done
[[ ${WORKER_INCREMENTAL_VERIFIED} -eq 1 ]] \
    || amigo_die "current worker did not finish a successful post-start Withings incremental job"
amigo_log "PASS current worker completed a successful post-start Withings incremental job"

readonly CODEX_RUNTIME_BINARY="/srv/amigo/data/codex-bin/codex"
readonly CODEX_CONTAINER_BINARY="/opt/amigo/codex"
readonly CODEX_EXPECTED_SHA256="ac2cfed85fb647d61e0150b8548102b330e4799d9d81ad5d354de701edf6b074"
[[ -f "${CODEX_RUNTIME_BINARY}" && ! -L "${CODEX_RUNTIME_BINARY}" ]] \
    || amigo_die "pinned Codex runtime binary is missing or is a symlink"
[[ "$(sha256sum "${CODEX_RUNTIME_BINARY}" | awk '{print $1}')" == "${CODEX_EXPECTED_SHA256}" ]] \
    || amigo_die "host Codex runtime binary hash differs from pinned 0.148.0"
codex_mount="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/opt/amigo/codex"}}{{.Source}}|{{.RW}}{{end}}{{end}}' "${gateway_container}")"
[[ "${codex_mount}" == "${CODEX_RUNTIME_BINARY}|false" ]] \
    || amigo_die "AI gateway Codex mount is missing, writable, or sourced unexpectedly"
container_codex_hash="$(docker exec "${gateway_container}" sha256sum "${CODEX_CONTAINER_BINARY}" | awk '{print $1}')"
[[ "${container_codex_hash}" == "${CODEX_EXPECTED_SHA256}" ]] \
    || amigo_die "running AI gateway sees an unpinned Codex binary"
amigo_log "PASS Codex 0.148.0 binary and read-only mount match the pinned SHA-256"

docker exec "${gateway_container}" python -c '
import json
import urllib.request
with urllib.request.urlopen("http://127.0.0.1:8090/healthz", timeout=3) as response:
    payload = json.load(response)
if payload != {"status": "ok", "model": "gpt-5.6-sol", "prompt_version": "amigo-health-v4"}:
    raise SystemExit(1)
' || amigo_die "AI gateway health does not report the fixed model and v4 contract"
docker exec "${parser_container}" python -c '
import json
import urllib.request
with urllib.request.urlopen("http://127.0.0.1:8085/healthz", timeout=3) as response:
    payload = json.load(response)
if payload != {"status": "ok"}:
    raise SystemExit(1)
' || amigo_die "laboratory parser health contract failed"
docker exec "${ai_worker_container}" python -c '
import json
import urllib.request
with urllib.request.urlopen("http://lab-parser:8085/healthz", timeout=3) as response:
    payload = json.load(response)
if payload != {"status": "ok"}:
    raise SystemExit(1)
' || amigo_die "AI worker cannot reach the isolated laboratory parser"
amigo_log "PASS fixed gpt-5.6-sol/v4 gateway and isolated parser health contracts"

[[ -s "${AMIGO_LEGACY_WEIGHT_IMPORT}" && ! -L "${AMIGO_LEGACY_WEIGHT_IMPORT}" ]] \
    || amigo_die "root-only legacy weight import is missing"
IMPORT_MODE="$(stat -c '%a' "${AMIGO_LEGACY_WEIGHT_IMPORT}")"
readonly IMPORT_MODE
readonly IMPORT_MODE_NUMERIC=$((8#${IMPORT_MODE}))
(( (IMPORT_MODE_NUMERIC & 077) == 0 )) \
    || amigo_die "legacy weight import is readable by group/world"
[[ "$(stat -c '%U' "${AMIGO_LEGACY_WEIGHT_IMPORT}")" == "root" ]] \
    || amigo_die "legacy weight import is not owned by root"
IMPORT_MOUNT_RW="$(docker inspect \
    --format '{{range .Mounts}}{{if eq .Destination "/imports"}}{{.RW}}{{end}}{{end}}' \
    "${web_container}")"
[[ "${IMPORT_MOUNT_RW}" == "false" ]] \
    || amigo_die "web /imports mount is missing or is not read-only"

[[ -d "${AMIGO_LAB_FILES_DIR}" && ! -L "${AMIGO_LAB_FILES_DIR}" ]] \
    || amigo_die "protected laboratory storage is missing or is a symlink"
[[ "$(stat -c '%a' "${AMIGO_LAB_FILES_DIR}")" == "700" ]] \
    || amigo_die "laboratory storage mode is not exactly 0700"
[[ "$(stat -c '%U:%G' "${AMIGO_LAB_FILES_DIR}")" == "root:root" ]] \
    || amigo_die "laboratory storage is not owned by root:root"
web_lab_mount="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/lab-files"}}{{.Source}}|{{.RW}}{{end}}{{end}}' "${web_container}")"
ai_worker_lab_mount="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/lab-files"}}{{.Source}}|{{.RW}}{{end}}{{end}}' "${ai_worker_container}")"
parser_lab_mount="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/lab-files"}}{{.Source}}|{{.RW}}{{end}}{{end}}' "${parser_container}")"
[[ "${web_lab_mount}" == "${AMIGO_LAB_FILES_DIR}|true" ]] \
    || amigo_die "web laboratory storage mount is missing or not writable"
[[ "${ai_worker_lab_mount}" == "${AMIGO_LAB_FILES_DIR}|false" ]] \
    || amigo_die "AI worker laboratory storage mount is missing or writable"
[[ -z "${parser_lab_mount}" ]] \
    || amigo_die "isolated parser unexpectedly mounts laboratory originals"
amigo_log "PASS root-only laboratory originals and least-privilege mounts"

readonly EXPECTED_ANDROID_APK_SHA256="fd5a13cf89440a80d8ee44444607077bce9f5466f3653372c26cd153add965e5"
readonly EXPECTED_ANDROID_APK_SIZE_BYTES=3520750
[[ -f "${AMIGO_ANDROID_APK}" && ! -L "${AMIGO_ANDROID_APK}" ]] \
    || amigo_die "signed Android update is missing or is a symlink"
[[ "$(stat -c '%a' "${AMIGO_ANDROID_APK}")" == "600" ]] \
    || amigo_die "signed Android update mode is not exactly 0600"
[[ "$(stat -c '%U:%G' "${AMIGO_ANDROID_APK}")" == "root:root" ]] \
    || amigo_die "signed Android update is not owned by root:root"
[[ "$(sha256sum "${AMIGO_ANDROID_APK}" | awk '{ print $1 }')" \
    == "${EXPECTED_ANDROID_APK_SHA256}" ]] \
    || amigo_die "installed Android update hash differs from signed 1.4.1"
[[ "$(stat -c '%s' "${AMIGO_ANDROID_APK}")" -eq "${EXPECTED_ANDROID_APK_SIZE_BYTES}" ]] \
    || amigo_die "installed Android update size differs from signed 1.4.1"
web_android_mount="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/android"}}{{.Source}}|{{.RW}}{{end}}{{end}}' "${web_container}")"
[[ "${web_android_mount}" == "$(dirname -- "${AMIGO_ANDROID_APK}")|false" ]] \
    || amigo_die "web Android update mount is missing, writable, or sourced unexpectedly"

amigo_compose run --rm --no-deps worker python -m app.cli backfill-files >/dev/null
FILE_STORAGE_STATE="$(
    amigo_compose exec -T db psql \
        --username amigo \
        --dbname amigo \
        --no-psqlrc \
        --quiet \
        --tuples-only \
        --no-align \
        --set=ON_ERROR_STOP=1 \
        --command "
            SELECT
                (SELECT count(*) FROM lab_documents WHERE stored_file_id IS NULL)
                || '|' ||
                (SELECT count(*) FROM study_documents WHERE stored_file_id IS NULL)
                || '|' ||
                (SELECT count(*) FROM stored_files);
        "
)"
[[ "${FILE_STORAGE_STATE}" =~ ^0\|0\|[0-9]+$ ]] \
    || amigo_die "uploaded-original database ownership verification failed"
LAB_DATE_STATE="$(
    amigo_compose exec -T db psql \
        --username amigo \
        --dbname amigo \
        --no-psqlrc \
        --quiet \
        --tuples-only \
        --no-align \
        --set=ON_ERROR_STOP=1 \
        --command "
            SELECT
                (SELECT count(*) FROM lab_reports
                 WHERE observed_on < DATE '1900-01-01'
                    OR observed_on > CURRENT_DATE + INTERVAL '1 year')
                || '|' ||
                (SELECT count(*) FROM lab_results
                 WHERE observed_on < DATE '1900-01-01'
                    OR observed_on > CURRENT_DATE + INTERVAL '1 year');
        "
)"
[[ "${LAB_DATE_STATE}" == "0|0" ]] \
    || amigo_die "implausible laboratory dates remain after deterministic repair"

ANALYTE_GUIDES_READY=0
for _guide_wait in {1..36}; do
    ANALYTE_GUIDE_STATE="$(
        amigo_compose exec -T ai-worker python -c '
from sqlalchemy import func, select
from app.db import SessionLocal
from app.lab_contracts import LAB_ANALYTE_GUIDE_PROMPT_VERSION
from app.lab_models import LabAnalyteGuide, LabAnalyteGuideJob
from app.labs import missing_analyte_guides
with SessionLocal() as db:
    missing = len(missing_analyte_guides(db))
    current_jobs = LabAnalyteGuideJob.contract_version == LAB_ANALYTE_GUIDE_PROMPT_VERSION
    active = db.scalar(select(func.count()).select_from(LabAnalyteGuideJob).where(current_jobs, LabAnalyteGuideJob.status.in_(["pending", "processing"]))) or 0
    failed = db.scalar(select(func.count()).select_from(LabAnalyteGuideJob).where(current_jobs, LabAnalyteGuideJob.status == "failed")) or 0
    generated = db.scalar(select(func.count()).select_from(LabAnalyteGuide).where(LabAnalyteGuide.contract_version == LAB_ANALYTE_GUIDE_PROMPT_VERSION)) or 0
    print(f"{missing}|{active}|{failed}|{generated}")
'
    )"
    if [[ "${ANALYTE_GUIDE_STATE}" =~ ^0\|0\|0\|[0-9]+$ ]] \
        || [[ "${ANALYTE_GUIDE_STATE}" =~ ^[0-9]+\|[0-9]+\|0\|[1-9][0-9]*$ ]]; then
        ANALYTE_GUIDES_READY=1
        break
    fi
    if [[ "${ANALYTE_GUIDE_STATE}" =~ ^[0-9]+\|[0-9]+\|[1-9][0-9]*\|[0-9]+$ ]]; then
        amigo_die "analyte guide backfill reached a terminal failure"
    fi
    sleep 5
done
[[ ${ANALYTE_GUIDES_READY} -eq 1 ]] \
    || amigo_die "analyte guide backfill made no verified progress within three minutes"
amigo_log "PASS database-owned originals, repaired laboratory dates, bounded analyte-guide backfill progress, and signed Android 1.4.1 artifact"

check_loopback_listener() {
    local port=$1
    local service=$2
    local listeners
    listeners="$(ss -H -ltn "sport = :${port}")"
    [[ -n "${listeners}" ]] || amigo_die "nothing is listening for ${service} on TCP port ${port}"
    awk -v expected="127.0.0.1:${port}" '$4 != expected { exit 1 }' <<<"${listeners}" \
        || amigo_die "${service} port ${port} is not restricted to 127.0.0.1"
}

check_loopback_listener 18181 web
check_loopback_listener 18182 ingest
[[ "$(published_port_count "${gateway_container}")" -eq 0 ]] \
    || amigo_die "AI gateway unexpectedly publishes a Docker port"
[[ "$(published_port_count "${parser_container}")" -eq 0 ]] \
    || amigo_die "laboratory parser unexpectedly publishes a Docker port"
[[ -z "$(ss -H -ltn 'sport = :8090')" ]] \
    || amigo_die "AI gateway port 8090 unexpectedly listens on the host"
[[ -z "$(ss -H -ltn 'sport = :8085')" ]] \
    || amigo_die "laboratory parser port 8085 unexpectedly listens on the host"
amigo_log "PASS only web and ingest publish loopback ports; gateway/parser remain unpublished"

curl --fail --silent --show-error --max-time 10 --output /dev/null "${AMIGO_DIRECT_HEALTH_URL}"
curl --fail --silent --show-error --max-time 10 --output /dev/null "http://127.0.0.1:18182/healthz"
nginx -t >/dev/null
[[ "$(grep -Ec '^[[:space:]]*# BEGIN AMIGO V2 ROUTE[[:space:]]*$' "${AMIGO_NGINX_CONFIG}")" -eq 2 ]] \
    || amigo_die "origin nginx config does not contain exactly two managed route markers"
cmp --silent "${SCRIPT_DIR}/nginx/amigo.locations.conf" "${AMIGO_NGINX_SNIPPET}" \
    || amigo_die "installed nginx snippet differs from the release"
cmp --silent "${SCRIPT_DIR}/nginx/amigo.http.conf" "${AMIGO_NGINX_HTTP_CONFIG}" \
    || amigo_die "installed nginx rate-limit configuration differs from the release"

ORIGIN_REDIRECT_STATUS="$(
    curl --silent --show-error --max-time 10 \
        --header 'Host: amigo.tolstik.ru' \
        --output /dev/null \
        --write-out '%{http_code}' \
        http://127.0.0.1/amigo
)"
[[ "${ORIGIN_REDIRECT_STATUS}" == "308" ]] \
    || amigo_die "origin /amigo redirect returned ${ORIGIN_REDIRECT_STATUS}, expected 308"
amigo_wait_for_origin_http_200 "/amigo/" 15 \
    || amigo_die "authenticated application shell did not stabilize at origin"

PUBLIC_REDIRECT_STATUS="$(
    curl --silent --show-error --max-time 15 \
        --proto '=https' \
        --tlsv1.2 \
        --dump-header "${REDIRECT_HEADERS}" \
        --output /dev/null \
        --write-out '%{http_code}' \
        https://amigo.tolstik.ru/amigo
)"
[[ "${PUBLIC_REDIRECT_STATUS}" == "308" ]] \
    || amigo_die "public /amigo redirect returned ${PUBLIC_REDIRECT_STATUS}, expected 308"
grep --ignore-case --quiet --extended-regexp '^location:[[:space:]]*/amigo/[[:space:]]*$' \
    "${REDIRECT_HEADERS}" \
    || amigo_die "public /amigo redirect is not relative"

curl --fail --silent --show-error --max-time 20 \
    --proto '=https' \
    --tlsv1.2 \
    --dump-header "${ASSETLINKS_HEADERS}" \
    --output "${ASSETLINKS_BODY}" \
    https://amigo.tolstik.ru/.well-known/assetlinks.json
require_header '^content-type:[[:space:]]*application/json' "${ASSETLINKS_HEADERS}"
require_header '^cache-control:[[:space:]]*public,[[:space:]]*max-age=3600' \
    "${ASSETLINKS_HEADERS}"
require_header '^x-content-type-options:[[:space:]]*nosniff' "${ASSETLINKS_HEADERS}"
python3 - "${ASSETLINKS_BODY}" <<'PY'
import json
from pathlib import Path
import sys

expected = [{
    "relation": ["delegate_permission/common.handle_all_urls"],
    "target": {
        "namespace": "android_app",
        "package_name": "ru.tolstik.amigo.sync",
        "sha256_cert_fingerprints": [
            "25:CC:38:EC:B3:10:81:F6:82:6F:F0:49:B8:07:33:5A:05:E8:6E:E9:89:54:70:97:5E:85:21:AF:95:19:1C:02"
        ],
    },
}]
payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload != expected:
    raise SystemExit("assetlinks contract differs from the signed Amigo release")
PY
ORIGIN_ASSETLINKS_POST_STATUS="$(
    curl --silent --show-error --max-time 10 \
        --request POST \
        --header 'Host: amigo.tolstik.ru' \
        --output /dev/null \
        --write-out '%{http_code}' \
        http://127.0.0.1/.well-known/assetlinks.json
)"
[[ "${ORIGIN_ASSETLINKS_POST_STATUS}" == "405" ]] \
    || amigo_die "origin assetlinks POST returned ${ORIGIN_ASSETLINKS_POST_STATUS}, expected 405"
ASSETLINKS_POST_STATUS="$(public_status "/.well-known/assetlinks.json" POST)"
[[ "${ASSETLINKS_POST_STATUS}" == "403" || "${ASSETLINKS_POST_STATUS}" == "405" ]] \
    || amigo_die "public assetlinks POST returned ${ASSETLINKS_POST_STATUS}, expected 403 or 405"
amigo_log "PASS verified Android App Links association"

curl --fail --silent --show-error --max-time 20 \
    --proto '=https' \
    --tlsv1.2 \
    --dump-header "${DASHBOARD_HEADERS}" \
    --output "${DASHBOARD_BODY}" \
    "${AMIGO_PUBLIC_URL}"
[[ -s "${DASHBOARD_BODY}" ]] || amigo_die "login shell returned an empty body"
require_header '^cache-control:.*no-store' "${DASHBOARD_HEADERS}"
require_header '^x-robots-tag:.*noindex.*noarchive' "${DASHBOARD_HEADERS}"
require_header '^x-content-type-options:[[:space:]]*nosniff' "${DASHBOARD_HEADERS}"
require_header '^content-security-policy:' "${DASHBOARD_HEADERS}"
amigo_log "PASS public login shell, TLS, relative redirect, and defensive headers"

mapfile -t FRONTEND_ASSET_PATHS < <(python3 - "${DASHBOARD_BODY}" <<'PY'
from pathlib import Path
import re
import sys

html = Path(sys.argv[1]).read_text(encoding="utf-8")
paths = sorted(set(re.findall(r'["\'](/amigo/assets/[^"\']+\.(?:css|js))["\']', html)))
if not any(path.endswith(".js") for path in paths):
    raise SystemExit(1)
if not any(path.endswith(".css") for path in paths):
    raise SystemExit(1)
print(*paths, sep="\n")
PY
)
[[ ${#FRONTEND_ASSET_PATHS[@]} -ge 2 ]] \
    || amigo_die "login shell is missing hashed JavaScript or CSS assets"
for asset_path in "${FRONTEND_ASSET_PATHS[@]}"; do
    curl --fail --silent --show-error --max-time 20 \
        --proto '=https' \
        --tlsv1.2 \
        --dump-header "${ASSET_HEADERS}" \
        --output /dev/null \
        "https://amigo.tolstik.ru${asset_path}"
    require_header '^cache-control:[[:space:]]*public,[[:space:]]*max-age=31536000,[[:space:]]*immutable' \
        "${ASSET_HEADERS}"
done
amigo_log "PASS hashed frontend assets retain immutable caching"

for protected_path in \
    api/v1/auth/session \
    api/v1/overview \
    'api/v1/data-quality?range=30d' \
    api/v1/export/weight.csv \
    api/v1/export/circumference.csv \
    api/v1/series/circumference?range=30d \
    api/v1/labs/documents \
    api/v1/studies/documents \
    'api/v1/tasks?state=open' \
    api/v1/reports/doctor/00000000-0000-0000-0000-000000000000 \
    api/v1/reports/doctor/00000000-0000-0000-0000-000000000000.pdf \
    api/v1/reports/doctor/00000000-0000-0000-0000-000000000000.html \
    api/v1/app-update \
    api/v1/assistant/messages; do
    [[ "$(public_status "${protected_path}")" == "401" ]] \
        || amigo_die "unauthenticated protected route did not return 401: ${protected_path}"
done
readonly LAB_RESULT_CREATE_PATH="api/v1/labs/documents/00000000-0000-0000-0000-000000000000/results"
[[ "$(public_status "${LAB_RESULT_CREATE_PATH}" POST)" == "401" ]] \
    || amigo_die "unauthenticated protected POST route did not return 401: ${LAB_RESULT_CREATE_PATH}"
[[ "$(public_status 'api/v1/labs/uploads' POST)" == "401" ]] \
    || amigo_die "unauthenticated laboratory upload route did not return 401"
[[ "$(public_status 'api/v1/studies/uploads' POST)" == "401" ]] \
    || amigo_die "unauthenticated study upload route did not return 401"
for protected_post_path in \
    api/v1/labs/compare \
    api/v1/tasks \
    api/v1/reports/doctor; do
    [[ "$(public_status "${protected_post_path}" POST)" == "401" ]] \
        || amigo_die "unauthenticated protected POST route did not return 401: ${protected_post_path}"
done
[[ "$(public_status 'api/v1/body-measurements/2026-08-28' DELETE)" == "401" ]] \
    || amigo_die "unauthenticated circumference DELETE route did not return 401"
CIRCUMFERENCE_PUT_STATUS="$(curl --silent --show-error --max-time 20 \
    --proto '=https' --tlsv1.2 --request PUT \
    --header 'Content-Type: application/json' --data '{"waist_cm":96.5}' \
    --output /dev/null --write-out '%{http_code}' \
    "${AMIGO_PUBLIC_URL}api/v1/body-measurements/2026-08-28")"
[[ "${CIRCUMFERENCE_PUT_STATUS}" == "401" ]] \
    || amigo_die "unauthenticated circumference PUT route did not return 401"
amigo_log "PASS dashboard JSON, CSV, quality, laboratory comparison, tasks, doctor reports, updater, and assistant require authentication"

amigo_compose run --rm --no-deps --user 0 \
    --volume "${VERIFICATION_DIR}:/verification" \
    worker python -m app.cli auth-verification-session --directory /verification >/dev/null
[[ -f "${SESSION_DESCRIPTOR}" && ! -L "${SESSION_DESCRIPTOR}" ]] \
    || amigo_die "verification session descriptor is missing"
[[ "$(stat -c '%a' "${SESSION_DESCRIPTOR}")" == "400" ]] \
    || amigo_die "verification session descriptor mode is not 0400"

python3 - \
    "${SESSION_DESCRIPTOR}" \
    "${AUTH_CURL_CONFIG}" \
    "${ORIGIN_NO_CSRF_CURL_CONFIG}" <<'PY'
from pathlib import Path
import json
import os
import sys

source, authenticated, no_csrf = map(Path, sys.argv[1:])
payload = json.loads(source.read_text(encoding="utf-8"))
session = payload["session"]
csrf = payload["csrf"]
if not isinstance(session, str) or not isinstance(csrf, str) or not session or not csrf:
    raise SystemExit(1)
cookie = f"__Secure-amigo_session={session}; __Secure-amigo_csrf={csrf}"
common = [
    "silent",
    "show-error",
    "max-time = 20",
    'proto = "=https"',
    "tlsv1.2",
    f'cookie = "{cookie}"',
]
documents = {
    authenticated: common + [
        'header = "Origin: https://amigo.tolstik.ru"',
        f'header = "X-CSRF-Token: {csrf}"',
    ],
    no_csrf: common + ['header = "Origin: https://amigo.tolstik.ru"'],
}
for target, lines in documents.items():
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")
PY

check_authenticated_json_api() {
    local path=$1
    local contract=$2
    curl --config "${AUTH_CURL_CONFIG}" \
        --dump-header "${API_HEADERS}" \
        --output "${API_BODY}" \
        "${AMIGO_PUBLIC_URL}${path}"
    require_header '^cache-control:.*no-store' "${API_HEADERS}"
    python3 - "${API_BODY}" "${contract}" <<'PY'
from pathlib import Path
import json
import sys

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
contract = sys.argv[2]
if not isinstance(payload, dict):
    raise SystemExit("API response is not an object")
if contract == "session":
    if payload.get("authenticated") is not True or not isinstance(payload.get("expires_at"), str):
        raise SystemExit("session contract is incomplete")
elif contract == "profile":
    if not isinstance(payload.get("height_cm"), (int, float)):
        raise SystemExit("profile contract is incomplete")
elif contract == "overview":
    if not isinstance(payload.get("weight"), dict) or not isinstance(payload.get("pressure"), dict):
        raise SystemExit("overview contract is incomplete")
elif contract in {"activity", "recovery"}:
    if not isinstance(payload.get("daily"), list) or not isinstance(payload.get("weekly"), list):
        raise SystemExit(f"{contract} contract is incomplete")
    if contract == "recovery":
        for row in payload["daily"]:
            if not isinstance(row, dict):
                raise SystemExit("recovery daily row is not an object")
            value = row.get("sleep_minutes")
            if value is not None and not isinstance(value, (int, float)):
                raise SystemExit("recovery API no longer preserves sleep_minutes")
            if "sleep_hours" in row:
                raise SystemExit("recovery persistence/API contract unexpectedly changed to hours")
elif contract == "circumference":
    if not isinstance(payload.get("points"), list) or not isinstance(payload.get("meta"), dict):
        raise SystemExit("circumference API contract is incomplete")
    for row in payload["points"]:
        if not isinstance(row, dict) or not isinstance(row.get("measured_on"), str):
            raise SystemExit("circumference point is invalid")
        if row.get("waist_cm") is None and row.get("hip_cm") is None:
            raise SystemExit("circumference point has no value")
elif contract == "data-quality":
    sources = payload.get("sources")
    metrics = payload.get("metrics")
    if not isinstance(sources, dict) or not all(
        isinstance(sources.get(key), dict)
        for key in ("withings", "health_connect", "mi_fitness")
    ):
        raise SystemExit("data-quality source contract is incomplete")
    if not isinstance(metrics, list):
        raise SystemExit("data-quality metric contract is incomplete")
    steps = [item for item in metrics if isinstance(item, dict) and item.get("key") == "steps"]
    if len(steps) != 1 or steps[0].get("source_policy") != "xiaomi_finalized_only":
        raise SystemExit("data-quality steps are not Xiaomi-finalized-only")
    coverage = steps[0].get("coverage")
    if not isinstance(coverage, dict) or coverage.get("health_connect") != 0:
        raise SystemExit("data-quality exposes Health Connect steps")
    days = steps[0].get("days")
    if not isinstance(days, list) or any(
        not isinstance(day, dict)
        or day.get("state") not in {"available", "confirmed_empty", "missing"}
        or day.get("source") not in {None, "mi_fitness"}
        for day in days
    ):
        raise SystemExit("data-quality step-day source contract is invalid")
elif contract == "ai":
    if payload.get("ai_generated") is not True or payload.get("status") != "fresh":
        raise SystemExit("AI payload is not a fresh generated result")
    if payload.get("prompt_version") != "amigo-health-v4":
        raise SystemExit("AI payload does not use amigo-health-v4")
    if payload.get("model") != "gpt-5.6-sol":
        raise SystemExit("AI payload does not use gpt-5.6-sol")
    recommendations = payload.get("recommendations")
    if not isinstance(recommendations, list) or not recommendations:
        raise SystemExit("AI payload has no validated recommendations")
    evidence = payload.get("evidence")
    if not isinstance(evidence, dict) or not evidence:
        raise SystemExit("AI payload has no stable evidence descriptors")
    for item in [*payload.get("insights", []), *recommendations]:
        keys = item.get("evidence_ids") if isinstance(item, dict) else None
        if not isinstance(keys, list) or not keys or any(key not in evidence for key in keys):
            raise SystemExit("AI item does not resolve every evidence ID")
    for key, descriptor in evidence.items():
        if (
            not isinstance(key, str)
            or not isinstance(descriptor, dict)
            or descriptor.get("key") != key
            or descriptor.get("kind") not in {"fact", "series", "laboratory"}
            or not isinstance(descriptor.get("target"), dict)
        ):
            raise SystemExit("AI evidence descriptor contract is incomplete")
elif contract in {"documents", "lab-summary", "analytes", "assistant", "tasks"}:
    if not isinstance(payload.get("items"), list):
        raise SystemExit(f"{contract} items contract is incomplete")
    if contract == "lab-summary" and not isinstance(payload.get("counts"), dict):
        raise SystemExit("laboratory summary counts are missing")
    if contract == "assistant" and not isinstance(payload.get("recommendations"), list):
        raise SystemExit("assistant recommendations are missing")
    if contract == "assistant":
        for item in payload["items"]:
            if not isinstance(item, dict):
                raise SystemExit("assistant item is not an object")
            evidence = item.get("evidence")
            keys = item.get("evidence_keys")
            if evidence is not None and (
                not isinstance(evidence, dict)
                or not isinstance(keys, list)
                or set(evidence) != set(keys)
            ):
                raise SystemExit("assistant stable evidence snapshot is inconsistent")
        recommendations = payload["recommendations"]
        evidence = payload.get("evidence")
        if (
            not recommendations
            or not isinstance(payload.get("analysis_id"), int)
            or not isinstance(evidence, dict)
            or not evidence
        ):
            raise SystemExit("assistant recommendation evidence/task source is incomplete")
        for index, item in enumerate(recommendations, 1):
            keys = item.get("evidence_ids") if isinstance(item, dict) else None
            if (
                not isinstance(item, dict)
                or item.get("id") != f"recommendation-{index}"
                or not isinstance(keys, list)
                or not keys
                or any(key not in evidence for key in keys)
            ):
                raise SystemExit("assistant recommendation cannot resolve its stable evidence")
    if contract == "tasks" and not isinstance(payload.get("open_count"), int):
        raise SystemExit("task list count is missing")
elif contract == "analyte-guide":
    guide = payload.get("guide")
    if not isinstance(guide, dict) or not all(
        isinstance(guide.get(key), str) and guide[key]
        for key in ("summary", "why_tested", "low_meaning", "high_meaning", "version", "source")
    ):
        raise SystemExit("laboratory analyte guide contract is incomplete")
elif contract == "update":
    if (
        payload.get("version_code") != 16
        or payload.get("version_name") != "1.4.1"
        or payload.get("sha256") != "fd5a13cf89440a80d8ee44444607077bce9f5466f3653372c26cd153add965e5"
        or payload.get("download_url") != "/amigo/api/v1/app-update/apk"
        or payload.get("size_bytes") != 3520750
    ):
        raise SystemExit("Android update metadata contract is incomplete")
else:
    raise SystemExit("unknown verification contract")
PY
}

check_authenticated_json_api "api/v1/auth/session" session
check_authenticated_json_api "api/v1/profile" profile
check_authenticated_json_api "api/v1/overview" overview
check_authenticated_json_api "api/v1/series/activity?range=30d" activity
check_authenticated_json_api "api/v1/series/recovery?range=30d" recovery
check_authenticated_json_api "api/v1/series/circumference?range=30d" circumference
check_authenticated_json_api "api/v1/data-quality?range=30d" data-quality
check_authenticated_json_api "api/v1/ai-analysis" ai
check_authenticated_json_api "api/v1/labs/documents" documents
check_authenticated_json_api "api/v1/studies/documents" documents
check_authenticated_json_api "api/v1/labs/summary" lab-summary
check_authenticated_json_api "api/v1/labs/analytes" analytes
check_authenticated_json_api "api/v1/labs/analytes/leukocytes/history" analyte-guide
check_authenticated_json_api "api/v1/assistant/messages" assistant
check_authenticated_json_api "api/v1/tasks?state=open" tasks
check_authenticated_json_api "api/v1/app-update" update

amigo_compose exec -T web python -c '
from datetime import datetime, timedelta
from app.config import get_settings
from app.data_quality import data_quality
from app.db import SessionLocal
from app.health_analytics import _records
from app.mi_fitness_models import MiFitnessRecord
settings = get_settings()
start = datetime.now(settings.tz).date() - timedelta(days=89)
with SessionLocal() as db:
    rows = _records(db, frozenset({"steps"}), settings.tz, start)
    if any(not isinstance(row, MiFitnessRecord) for row in rows):
        raise SystemExit("published step selector contains a non-Xiaomi row")
    quality = data_quality(db, settings.tz, "90d")
    steps = [item for item in quality["metrics"] if item["key"] == "steps"]
    if len(steps) != 1 or steps[0]["source_policy"] != "xiaomi_finalized_only":
        raise SystemExit("step source policy is not Xiaomi-finalized-only")
    if steps[0]["coverage"]["health_connect"] != 0:
        raise SystemExit("Health Connect steps escaped rollback history")
    if any(day["source"] not in (None, "mi_fitness") for day in steps[0]["days"]):
        raise SystemExit("step quality day exposes a non-Xiaomi source")
'
amigo_log "PASS dashboard/CSV/Telegram/AI shared selector publishes only active finalized Xiaomi Cloud steps"

curl --config "${AUTH_CURL_CONFIG}" \
    --dump-header "${APK_HEADERS}" \
    --output "${APK_BODY}" \
    "${AMIGO_PUBLIC_URL}api/v1/app-update/apk"
require_header '^content-type:[[:space:]]*application/vnd.android.package-archive' "${APK_HEADERS}"
require_header '^cache-control:.*no-store' "${APK_HEADERS}"
[[ "$(sha256sum "${APK_BODY}" | awk '{ print $1 }')" \
    == "${EXPECTED_ANDROID_APK_SHA256}" ]] \
    || amigo_die "authenticated Android update download hash differs from metadata"

curl --config "${AUTH_CURL_CONFIG}" \
    --dump-header "${CSV_HEADERS}" \
    --output "${CSV_BODY}" \
    "${AMIGO_PUBLIC_URL}api/v1/export/weight.csv"
[[ -s "${CSV_BODY}" ]] || amigo_die "authenticated CSV export is empty"
require_header '^content-type:[[:space:]]*text/csv' "${CSV_HEADERS}"
amigo_log "PASS authenticated dashboard, data quality, tasks, lab/study, analyte guide, updater, stable AI evidence, and CSV contracts"

install -o root -g root -m 0600 /dev/null "${UNSUPPORTED_FILE}"
CSRF_REJECTION_STATUS="$(
    curl --config "${ORIGIN_NO_CSRF_CURL_CONFIG}" \
        --request POST \
        --form "file=@${UNSUPPORTED_FILE};type=text/plain" \
        --output "${UPLOAD_BODY}" \
        --write-out '%{http_code}' \
        "${AMIGO_PUBLIC_URL}api/v1/labs/uploads"
)"
[[ "${CSRF_REJECTION_STATUS}" == "403" ]] \
    || amigo_die "authenticated upload without CSRF returned ${CSRF_REJECTION_STATUS}, expected 403"

LAB_CREATE_CSRF_STATUS="$(
    curl --config "${ORIGIN_NO_CSRF_CURL_CONFIG}" \
        --request POST \
        --header 'Content-Type: application/json' \
        --data '{"analyte_name":"verification","value_text":"present"}' \
        --output "${UPLOAD_BODY}" \
        --write-out '%{http_code}' \
        "${AMIGO_PUBLIC_URL}api/v1/labs/documents/00000000-0000-0000-0000-000000000000/results"
)"
[[ "${LAB_CREATE_CSRF_STATUS}" == "403" ]] \
    || amigo_die "manual laboratory result without CSRF returned ${LAB_CREATE_CSRF_STATUS}, expected 403"

LAB_CREATE_ROUTE_STATUS="$(
    curl --config "${AUTH_CURL_CONFIG}" \
        --request POST \
        --header 'Content-Type: application/json' \
        --data '{"analyte_name":"verification","value_text":"present"}' \
        --output "${UPLOAD_BODY}" \
        --write-out '%{http_code}' \
        "${AMIGO_PUBLIC_URL}api/v1/labs/documents/00000000-0000-0000-0000-000000000000/results"
)"
[[ "${LAB_CREATE_ROUTE_STATUS}" == "404" ]] \
    || amigo_die "manual laboratory result allowlist returned ${LAB_CREATE_ROUTE_STATUS}, expected safe 404"

for csrf_case in \
    'api/v1/labs/compare|{"document_ids":["00000000-0000-0000-0000-000000000000","11111111-1111-1111-1111-111111111111"]}' \
    'api/v1/tasks|{"title":"verification","next_due_at":"2099-01-01T09:00:00+03:00"}' \
    'api/v1/reports/doctor|{"period":"30d","sections":["summary"]}'; do
    csrf_path=${csrf_case%%|*}
    csrf_payload=${csrf_case#*|}
    csrf_status="$(
        curl --config "${ORIGIN_NO_CSRF_CURL_CONFIG}" \
            --request POST \
            --header 'Content-Type: application/json' \
            --data "${csrf_payload}" \
            --output "${UPLOAD_BODY}" \
            --write-out '%{http_code}' \
            "${AMIGO_PUBLIC_URL}${csrf_path}"
    )"
    [[ "${csrf_status}" == "403" ]] \
        || amigo_die "authenticated mutation without CSRF returned ${csrf_status}: ${csrf_path}"
done

LAB_COMPARE_ROUTE_STATUS="$(
    curl --config "${AUTH_CURL_CONFIG}" \
        --request POST \
        --header 'Content-Type: application/json' \
        --data '{"document_ids":["00000000-0000-0000-0000-000000000000","11111111-1111-1111-1111-111111111111"]}' \
        --output "${UPLOAD_BODY}" \
        --write-out '%{http_code}' \
        "${AMIGO_PUBLIC_URL}api/v1/labs/compare"
)"
[[ "${LAB_COMPARE_ROUTE_STATUS}" == "404" ]] \
    || amigo_die "laboratory comparison allowlist returned ${LAB_COMPARE_ROUTE_STATUS}, expected safe 404"

TASK_CREATE_VALIDATION_STATUS="$(
    curl --config "${AUTH_CURL_CONFIG}" \
        --request POST \
        --header 'Content-Type: application/json' \
        --data '{"title":"","next_due_at":"2099-01-01T09:00:00+03:00"}' \
        --output "${UPLOAD_BODY}" \
        --write-out '%{http_code}' \
        "${AMIGO_PUBLIC_URL}api/v1/tasks"
)"
[[ "${TASK_CREATE_VALIDATION_STATUS}" == "422" ]] \
    || amigo_die "task validation route returned ${TASK_CREATE_VALIDATION_STATUS}, expected safe 422"
TASK_PATCH_ROUTE_STATUS="$(
    curl --config "${AUTH_CURL_CONFIG}" \
        --request PATCH \
        --header 'Content-Type: application/json' \
        --data '{"title":"verification"}' \
        --output "${UPLOAD_BODY}" \
        --write-out '%{http_code}' \
        "${AMIGO_PUBLIC_URL}api/v1/tasks/00000000-0000-0000-0000-000000000000"
)"
[[ "${TASK_PATCH_ROUTE_STATUS}" == "404" ]] \
    || amigo_die "task PATCH allowlist returned ${TASK_PATCH_ROUTE_STATUS}, expected safe 404"
for task_action in complete cancel; do
    task_action_status="$(
        curl --config "${AUTH_CURL_CONFIG}" \
            --request POST \
            --output "${UPLOAD_BODY}" \
            --write-out '%{http_code}' \
            "${AMIGO_PUBLIC_URL}api/v1/tasks/00000000-0000-0000-0000-000000000000/${task_action}"
    )"
    [[ "${task_action_status}" == "404" ]] \
        || amigo_die "task ${task_action} allowlist returned ${task_action_status}, expected safe 404"
done

REPORT_CREATE_STATUS="$(
    curl --config "${AUTH_CURL_CONFIG}" \
        --max-time 60 \
        --request POST \
        --header 'Content-Type: application/json' \
        --data '{"period":"30d","sections":["summary","weight","pressure","activity","recovery","labs","studies","ai"]}' \
        --dump-header "${REPORT_HEADERS}" \
        --output "${REPORT_BODY}" \
        --write-out '%{http_code}' \
        "${AMIGO_PUBLIC_URL}api/v1/reports/doctor"
)"
[[ "${REPORT_CREATE_STATUS}" == "201" ]] \
    || amigo_die "doctor report creation returned ${REPORT_CREATE_STATUS}, expected 201"
require_header '^content-type:[[:space:]]*application/json' "${REPORT_HEADERS}"
require_header '^cache-control:.*no-store' "${REPORT_HEADERS}"
DOCTOR_REPORT_ID="$(python3 - "${REPORT_BODY}" <<'PY'
from pathlib import Path
import json
import re
import sys

report_id = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")).get("id")
if not isinstance(report_id, str) or not re.fullmatch(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    report_id,
):
    raise SystemExit("doctor report ID is not a canonical lowercase UUID")
print(report_id)
PY
)"
python3 - "${REPORT_BODY}" "${DOCTOR_REPORT_ID}" <<'PY'
from datetime import datetime, timezone
from pathlib import Path
import json
import sys

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
report_id = sys.argv[2]
if payload.get("id") != report_id:
    raise SystemExit("doctor report response ID changed during verification")
if payload.get("download_url") != f"/amigo/api/v1/reports/doctor/{report_id}.pdf":
    raise SystemExit("doctor report download URL is not exact")
if payload.get("html_download_url") != f"/amigo/api/v1/reports/doctor/{report_id}.html":
    raise SystemExit("doctor report HTML download URL is not exact")
if not isinstance(payload.get("page_count"), int) or not 1 <= payload["page_count"] <= 40:
    raise SystemExit("doctor report page bound is invalid")
if not isinstance(payload.get("size_bytes"), int) or not 0 < payload["size_bytes"] <= 10 * 1024 * 1024:
    raise SystemExit("doctor report byte bound is invalid")
if not isinstance(payload.get("html_size_bytes"), int) or not 0 < payload["html_size_bytes"] <= 10 * 1024 * 1024:
    raise SystemExit("doctor report HTML byte bound is invalid")
created = datetime.fromisoformat(str(payload.get("created_at")).replace("Z", "+00:00"))
expires = datetime.fromisoformat(str(payload.get("expires_at")).replace("Z", "+00:00"))
ttl = (expires.astimezone(timezone.utc) - created.astimezone(timezone.utc)).total_seconds()
if not 23.9 * 3600 <= ttl <= 24.1 * 3600:
    raise SystemExit("doctor report retention is not 24 hours")
preview = payload.get("preview")
if not isinstance(preview, dict) or not isinstance(preview.get("sections"), dict):
    raise SystemExit("doctor report snapshot is incomplete")
blocked_fragments = (
    "filename", "ocr", "original", "device_id", "account_", "provider_payload",
    "raw_payload", "cookie", "token", "authorization", "chat", "message",
)
def walk(value):
    if isinstance(value, dict):
        for key, nested in value.items():
            lowered = str(key).lower()
            if any(fragment in lowered for fragment in blocked_fragments):
                raise SystemExit(f"doctor report contains forbidden field: {key}")
            walk(nested)
    elif isinstance(value, list):
        for nested in value:
            walk(nested)
walk(preview)
sections = preview["sections"]
if any(
    item.get("verification_status") not in {"verified", "corrected"}
    for item in sections.get("labs", [])
    if isinstance(item, dict)
):
    raise SystemExit("doctor report contains an unverified laboratory result")
for row in (sections.get("recovery") or {}).get("daily", []):
    if not isinstance(row, dict):
        raise SystemExit("doctor report recovery row is invalid")
    if "sleep_hours" in row:
        raise SystemExit("doctor report snapshot changed the internal sleep unit")
    if row.get("sleep_minutes") is not None and not isinstance(row["sleep_minutes"], (int, float)):
        raise SystemExit("doctor report snapshot lost sleep minutes")
PY

curl --config "${AUTH_CURL_CONFIG}" \
    --dump-header "${API_HEADERS}" \
    --output "${API_BODY}" \
    "${AMIGO_PUBLIC_URL}api/v1/reports/doctor/${DOCTOR_REPORT_ID}"
require_header '^content-type:[[:space:]]*application/json' "${API_HEADERS}"
require_header '^cache-control:.*no-store' "${API_HEADERS}"
cmp --silent "${REPORT_BODY}" "${API_BODY}" \
    || amigo_die "doctor report GET differs from its immutable creation snapshot"

curl --config "${AUTH_CURL_CONFIG}" \
    --max-time 60 \
    --dump-header "${REPORT_PDF_HEADERS}" \
    --output "${REPORT_PDF_BODY}" \
    "${AMIGO_PUBLIC_URL}api/v1/reports/doctor/${DOCTOR_REPORT_ID}.pdf"
require_header '^content-type:[[:space:]]*application/pdf' "${REPORT_PDF_HEADERS}"
require_header '^content-disposition:.*amigo-doctor-report\.pdf' "${REPORT_PDF_HEADERS}"
require_header '^cache-control:.*no-store' "${REPORT_PDF_HEADERS}"
python3 - "${REPORT_BODY}" "${REPORT_PDF_BODY}" <<'PY'
from pathlib import Path
import json
import sys

metadata = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
size = Path(sys.argv[2]).stat().st_size
if size != metadata["size_bytes"] or not 0 < size <= 10 * 1024 * 1024:
    raise SystemExit("downloaded doctor PDF size differs from its immutable snapshot")
PY
curl --config "${AUTH_CURL_CONFIG}" \
    --max-time 60 \
    --dump-header "${REPORT_HTML_HEADERS}" \
    --output "${REPORT_HTML_BODY}" \
    "${AMIGO_PUBLIC_URL}api/v1/reports/doctor/${DOCTOR_REPORT_ID}.html"
require_header '^content-type:[[:space:]]*text/html' "${REPORT_HTML_HEADERS}"
require_header '^content-disposition:.*amigo-doctor-report\.html' "${REPORT_HTML_HEADERS}"
require_header '^cache-control:.*no-store' "${REPORT_HTML_HEADERS}"
python3 - "${REPORT_BODY}" "${REPORT_HTML_BODY}" <<'PY'
from pathlib import Path
import json
import sys

metadata = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
html = Path(sys.argv[2]).read_text(encoding="utf-8")
if len(html.encode("utf-8")) != metadata.get("html_size_bytes"):
    raise SystemExit("downloaded doctor HTML size differs from immutable snapshot")
if "<svg" not in html or "<style>" not in html or "http://" in html or "https://" in html:
    raise SystemExit("doctor HTML is not self-contained")
if "Лабораторные" not in html and "лаборатор" not in html.lower():
    raise SystemExit("doctor HTML lacks laboratory section")
PY
amigo_compose run --rm --no-deps \
    --volume "${VERIFICATION_DIR}:/verification:ro" \
    web python -c '
from pathlib import Path
import fitz
import json
payload = json.loads(Path("/verification/doctor-report.json").read_text(encoding="utf-8"))
with fitz.open("/verification/doctor-report.pdf") as document:
    if not 1 <= document.page_count <= 40:
        raise SystemExit("doctor PDF page bound is invalid")
    text = "\n".join(page.get_text() for page in document)
if "только Xiaomi Cloud" not in text or "Продолжительность сна" not in text:
    raise SystemExit("doctor PDF lacks its explicit activity/recovery units and source labels")
sleep_present = any(
    isinstance(row, dict) and isinstance(row.get("sleep_minutes"), (int, float))
    for row in payload["preview"]["sections"]["recovery"]["daily"]
)
if sleep_present and "часы" not in text:
    raise SystemExit("doctor PDF sleep scale is not displayed in hours")
'

REPORT_DELETE_STATUS="$(
    curl --config "${AUTH_CURL_CONFIG}" \
        --request DELETE \
        --output /dev/null \
        --write-out '%{http_code}' \
        "${AMIGO_PUBLIC_URL}api/v1/reports/doctor/${DOCTOR_REPORT_ID}"
)"
[[ "${REPORT_DELETE_STATUS}" == "204" ]] \
    || amigo_die "doctor report cleanup returned ${REPORT_DELETE_STATUS}, expected 204"
REPORT_GONE_STATUS="$(
    curl --config "${AUTH_CURL_CONFIG}" \
        --output /dev/null \
        --write-out '%{http_code}' \
        "${AMIGO_PUBLIC_URL}api/v1/reports/doctor/${DOCTOR_REPORT_ID}"
)"
[[ "${REPORT_GONE_STATUS}" == "404" ]] \
    || amigo_die "deleted doctor report returned ${REPORT_GONE_STATUS}, expected 404"
DOCTOR_REPORT_ID=""
amigo_log "PASS new mutation CSRF/routes and temporary privacy-bounded doctor PDF with sleep displayed in hours"

curl --config "${AUTH_CURL_CONFIG}" \
    --output "${API_BODY}" \
    "${AMIGO_PUBLIC_URL}api/v1/labs/documents"
LAB_DOCUMENT_COUNT_BEFORE="$(python3 - "${API_BODY}" <<'PY'
from pathlib import Path
import json
import sys
payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(len(payload["items"]))
PY
)"
UPLOAD_REJECTION_STATUS="$(
    curl --config "${AUTH_CURL_CONFIG}" \
        --request POST \
        --form "file=@${UNSUPPORTED_FILE};type=text/plain" \
        --dump-header "${UPLOAD_HEADERS}" \
        --output "${UPLOAD_BODY}" \
        --write-out '%{http_code}' \
        "${AMIGO_PUBLIC_URL}api/v1/labs/uploads"
)"
[[ "${UPLOAD_REJECTION_STATUS}" == "409" || "${UPLOAD_REJECTION_STATUS}" == "422" ]] \
    || amigo_die "safe unsupported upload returned unexpected status ${UPLOAD_REJECTION_STATUS}"
python3 - "${UPLOAD_BODY}" <<'PY'
from pathlib import Path
import json
import sys
payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("detail") not in {"ai_data_consent_required", "empty_file", "unsupported_file_type"}:
    raise SystemExit(1)
PY
curl --config "${AUTH_CURL_CONFIG}" \
    --output "${API_BODY}" \
    "${AMIGO_PUBLIC_URL}api/v1/labs/documents"
LAB_DOCUMENT_COUNT_AFTER="$(python3 - "${API_BODY}" <<'PY'
from pathlib import Path
import json
import sys
payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(len(payload["items"]))
PY
)"
[[ "${LAB_DOCUMENT_COUNT_AFTER}" == "${LAB_DOCUMENT_COUNT_BEFORE}" ]] \
    || amigo_die "safe upload rejection unexpectedly created a laboratory document"
amigo_log "PASS exact Origin/CSRF boundary and safe upload rejection without stored data"

curl --config "${AUTH_CURL_CONFIG}" \
    --proto '=http' \
    --header 'Host: amigo.tolstik.ru' \
    --dump-header "${SSE_ORIGIN_HEADERS}" \
    --output /dev/null \
    "http://127.0.0.1/amigo/api/v1/assistant/messages/00000000-0000-0000-0000-000000000000/events"
require_header '^x-accel-buffering:[[:space:]]*no' "${SSE_ORIGIN_HEADERS}"

curl --config "${AUTH_CURL_CONFIG}" \
    --dump-header "${SSE_HEADERS}" \
    --output "${SSE_BODY}" \
    "${AMIGO_PUBLIC_URL}api/v1/assistant/messages/00000000-0000-0000-0000-000000000000/events"
require_header '^content-type:[[:space:]]*text/event-stream' "${SSE_HEADERS}"
require_header '^cache-control:.*no-store' "${SSE_HEADERS}"
grep --quiet --fixed-strings 'event: error' "${SSE_BODY}" \
    || amigo_die "authenticated SSE route did not return its bounded not-found event"
[[ "$(public_status 'api/v1/assistant/messages/00000000-0000-0000-0000-000000000000/events')" == "401" ]] \
    || amigo_die "unauthenticated assistant SSE route did not return 401"

check_queue_sse() {
    local path=$1
    local curl_status=0
    if curl --config "${AUTH_CURL_CONFIG}" \
        --max-time 3 \
        --proto '=http' \
        --header 'Host: amigo.tolstik.ru' \
        --dump-header "${SSE_ORIGIN_HEADERS}" \
        --output "${SSE_BODY}" \
        "http://127.0.0.1/amigo/${path}"; then
        curl_status=0
    else
        curl_status=$?
    fi
    [[ ${curl_status} -eq 0 || ${curl_status} -eq 28 ]] \
        || amigo_die "queue SSE check failed for ${path} with curl status ${curl_status}"
    require_header '^content-type:[[:space:]]*text/event-stream' "${SSE_ORIGIN_HEADERS}"
    require_header '^cache-control:.*no-store' "${SSE_ORIGIN_HEADERS}"
    require_header '^x-accel-buffering:[[:space:]]*no' "${SSE_ORIGIN_HEADERS}"
    grep --quiet --fixed-strings 'event: queue' "${SSE_BODY}" \
        || amigo_die "queue SSE did not publish its initial state for ${path}"
}

check_queue_sse "api/v1/labs/events"
check_queue_sse "api/v1/studies/events"
[[ "$(public_status 'api/v1/labs/events')" == "401" ]] \
    || amigo_die "unauthenticated laboratory queue SSE route did not return 401"
[[ "$(public_status 'api/v1/studies/events')" == "401" ]] \
    || amigo_die "unauthenticated study queue SSE route did not return 401"
amigo_log "PASS assistant and queue SSE authentication/no-buffer contracts"

for ingest_path in \
    health-connect/batches \
    mi-fitness/batches \
    mi-fitness/status; do
    INGEST_REJECTION_STATUS="$(
        curl --disable --silent --show-error --max-time 20 \
            --proto '=https' \
            --tlsv1.2 \
            --request POST \
            --header 'Content-Type: application/json' \
            --data '{}' \
            --dump-header "${INGEST_HEADERS}" \
            --output "${INGEST_BODY}" \
            --write-out '%{http_code}' \
            "https://amigo.tolstik.ru/amigo-ingest/v1/${ingest_path}"
    )"
    [[ "${INGEST_REJECTION_STATUS}" == "400" ]] \
        || amigo_die "unsigned exact ingest route ${ingest_path} returned ${INGEST_REJECTION_STATUS}, expected 400"
    require_header '^cache-control:.*no-store' "${INGEST_HEADERS}"
    python3 - "${INGEST_BODY}" <<'PY'
from pathlib import Path
import json
import sys
payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload != {"detail": {"code": "missing_signature_header"}}:
    raise SystemExit(1)
PY
done
amigo_log "PASS signed Health Connect and Xiaomi ingest stay independent and reject unsigned empty input exactly"

for hidden_path in \
    healthz \
    amigo/healthz \
    amigo/internal/health \
    amigo-ingest/healthz \
    amigo-ai/healthz \
    amigo-lab-parser/healthz; do
    external_health_status="$(
        curl --silent --show-error --location --max-time 15 \
            --proto '=https' \
            --tlsv1.2 \
            --output /dev/null \
            --write-out '%{http_code}' \
            "https://amigo.tolstik.ru/${hidden_path}"
    )"
    if [[ "${external_health_status}" =~ ^2 ]]; then
        amigo_die "health endpoint is externally published: /${hidden_path}"
    fi
done
amigo_log "PASS internal health endpoints are not externally published"

crontab -u "${AMIGO_LEGACY_CRON_USER}" -l >"${CRONTAB_FILE}"
[[ "$(amigo_count_exact_line "${AMIGO_LEGACY_WITHINGS_CRON_LINE}" "${CRONTAB_FILE}")" -eq 0 ]] \
    || amigo_die "legacy Withings cron line is still active"
[[ "$(amigo_count_exact_line "${AMIGO_LEGACY_WITHINGS_DISABLED_LINE}" "${CRONTAB_FILE}")" -eq 1 ]] \
    || amigo_die "legacy Withings disabled marker is missing"
[[ "$(amigo_count_exact_line "${AMIGO_SHARED_TELEGRAM_CRON_LINE}" "${CRONTAB_FILE}")" -ge 1 ]] \
    || amigo_die "shared send_telergam cron line is missing"
[[ -d "${AMIGO_LEGACY_WEB_DIR}" ]] \
    || amigo_die "legacy web directory is no longer present"
[[ "$(mariadb --batch --skip-column-names -e \
    "SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='amigo'" 2>/dev/null)" == "1" ]] \
    || amigo_die "legacy MariaDB database is no longer present"
amigo_log "PASS rollback assets and shared cron job remain intact"

amigo_log "ALL PRODUCTION CHECKS PASSED: ${AMIGO_PUBLIC_URL}"
