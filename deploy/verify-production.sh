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
    awk cmp crontab curl docker git grep mariadb mktemp nginx python3 rm rmdir ss stat
amigo_require_production_layout

TMP_DIR="$(mktemp -d /run/amigo-verify.XXXXXX)"
readonly TMP_DIR
readonly DASHBOARD_HEADERS="${TMP_DIR}/dashboard.headers"
readonly DASHBOARD_BODY="${TMP_DIR}/dashboard.body"
readonly API_HEADERS="${TMP_DIR}/api.headers"
readonly API_BODY="${TMP_DIR}/api.body"
readonly ASSET_HEADERS="${TMP_DIR}/asset.headers"
readonly REDIRECT_HEADERS="${TMP_DIR}/redirect.headers"
readonly CRONTAB_FILE="${TMP_DIR}/tolstik.crontab"

cleanup() {
    rm -f -- \
        "${DASHBOARD_HEADERS}" \
        "${DASHBOARD_BODY}" \
        "${API_HEADERS}" \
        "${API_BODY}" \
        "${ASSET_HEADERS}" \
        "${REDIRECT_HEADERS}" \
        "${CRONTAB_FILE}"
    rmdir -- "${TMP_DIR}" 2>/dev/null || true
}
trap cleanup EXIT

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
    [[ "${health}" == "none" || "${health}" == "healthy" ]] \
        || amigo_die "Compose service is unhealthy: ${service} (${health})"
    amigo_log "PASS service ${service}: running, health=${health}"
}

require_header() {
    local header_pattern=$1
    local file=$2
    grep --ignore-case --quiet --extended-regexp "${header_pattern}" "${file}" \
        || amigo_die "required response header is missing: ${header_pattern}"
}

amigo_log "validating Compose and containers"
amigo_compose config --quiet
check_service db
check_service web
check_service worker
amigo_compose exec -T db pg_isready -U amigo -d amigo >/dev/null

EXPECTED_IMAGE="amigo:$(amigo_current_release)"
readonly EXPECTED_IMAGE
for application_service in web worker; do
    application_container=$(amigo_compose ps -q "${application_service}")
    actual_image=$(docker inspect --format '{{.Config.Image}}' "${application_container}")
    [[ "${actual_image}" == "${EXPECTED_IMAGE}" ]] \
        || amigo_die "${application_service} runs ${actual_image}, expected immutable ${EXPECTED_IMAGE}"
done
amigo_log "PASS web and worker use the Git-SHA image tag"

web_container=$(amigo_compose ps -q web)
worker_container=$(amigo_compose ps -q worker)
web_secret_destinations="$(docker inspect "${web_container}" | python3 -c '
import json, sys
mounts = json.load(sys.stdin)[0]["Mounts"]
print(" ".join(sorted(m["Destination"] for m in mounts if m["Destination"].startswith("/run/secrets/"))))
')"
worker_secret_count="$(docker inspect "${worker_container}" | python3 -c '
import json, sys
mounts = json.load(sys.stdin)[0]["Mounts"]
print(sum(m["Destination"].startswith("/run/secrets/") for m in mounts))
')"
[[ "${web_secret_destinations}" == "/run/secrets/postgres_password" ]] \
    || amigo_die "public web container has unexpected integration secret mounts"
[[ "${worker_secret_count}" -eq 8 ]] \
    || amigo_die "worker does not have the expected eight secret mounts"
amigo_log "PASS integration secrets are isolated from the public web process"

[[ -s "${AMIGO_LEGACY_WEIGHT_IMPORT}" && ! -L "${AMIGO_LEGACY_WEIGHT_IMPORT}" ]] \
    || amigo_die "root-only legacy weight import is missing"
IMPORT_MODE="$(stat -c '%a' "${AMIGO_LEGACY_WEIGHT_IMPORT}")"
readonly IMPORT_MODE
readonly IMPORT_MODE_NUMERIC=$((8#${IMPORT_MODE}))
(( (IMPORT_MODE_NUMERIC & 077) == 0 )) \
    || amigo_die "legacy weight import is readable by group/world"
[[ "$(stat -c '%U' "${AMIGO_LEGACY_WEIGHT_IMPORT}")" == "root" ]] \
    || amigo_die "legacy weight import is not owned by root"
WEB_CONTAINER="$(amigo_compose ps -q web)"
IMPORT_MOUNT_RW="$(docker inspect \
    --format '{{range .Mounts}}{{if eq .Destination "/imports"}}{{.RW}}{{end}}{{end}}' \
    "${WEB_CONTAINER}")"
readonly WEB_CONTAINER IMPORT_MOUNT_RW
[[ "${IMPORT_MOUNT_RW}" == "false" ]] \
    || amigo_die "web /imports mount is missing or is not read-only"
amigo_log "PASS legacy import is root-only on host and read-only in the container"

LISTENERS="$(ss -H -ltn 'sport = :18181')"
readonly LISTENERS
[[ -n "${LISTENERS}" ]] || amigo_die "nothing is listening on TCP port 18181"
awk '$4 != "127.0.0.1:18181" { exit 1 }' <<<"${LISTENERS}" \
    || amigo_die "port 18181 is not restricted to 127.0.0.1"
amigo_log "PASS web port is bound only to 127.0.0.1:18181"

curl --fail --silent --show-error --max-time 10 \
    --output /dev/null "${AMIGO_DIRECT_HEALTH_URL}"
amigo_log "PASS direct health endpoint"

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
readonly ORIGIN_REDIRECT_STATUS
[[ "${ORIGIN_REDIRECT_STATUS}" == "308" ]] \
    || amigo_die "origin /amigo redirect returned ${ORIGIN_REDIRECT_STATUS}, expected 308"
curl --fail --silent --show-error --max-time 10 \
    --header 'Host: amigo.tolstik.ru' \
    --output /dev/null \
    http://127.0.0.1/amigo/
amigo_log "PASS origin nginx route and redirect"

PUBLIC_REDIRECT_STATUS="$(
    curl --silent --show-error --max-time 15 \
        --proto '=https' \
        --tlsv1.2 \
        --dump-header "${REDIRECT_HEADERS}" \
        --output /dev/null \
        --write-out '%{http_code}' \
        https://amigo.tolstik.ru/amigo
)"
readonly PUBLIC_REDIRECT_STATUS
[[ "${PUBLIC_REDIRECT_STATUS}" == "308" ]] \
    || amigo_die "public /amigo redirect returned ${PUBLIC_REDIRECT_STATUS}, expected 308"
grep --ignore-case --quiet --extended-regexp '^location:[[:space:]]*/amigo/[[:space:]]*$' \
    "${REDIRECT_HEADERS}" \
    || amigo_die "public /amigo redirect is not the required relative /amigo/ redirect"

curl --fail --silent --show-error --max-time 20 \
    --proto '=https' \
    --tlsv1.2 \
    --dump-header "${DASHBOARD_HEADERS}" \
    --output "${DASHBOARD_BODY}" \
    "${AMIGO_PUBLIC_URL}"
[[ -s "${DASHBOARD_BODY}" ]] || amigo_die "public dashboard returned an empty body"
require_header '^cache-control:.*no-store' "${DASHBOARD_HEADERS}"
require_header '^x-robots-tag:.*noindex.*noarchive' "${DASHBOARD_HEADERS}"
require_header '^x-content-type-options:[[:space:]]*nosniff' "${DASHBOARD_HEADERS}"
require_header '^content-security-policy:' "${DASHBOARD_HEADERS}"
amigo_log "PASS public dashboard, TLS, and defensive headers"

ASSET_PATH="$(python3 - "${DASHBOARD_BODY}" <<'PY'
from pathlib import Path
import re
import sys

html = Path(sys.argv[1]).read_text(encoding="utf-8")
match = re.search(r'["\'](/amigo/assets/[^"\']+\.js)["\']', html)
if match is None:
    raise SystemExit(1)
print(match.group(1))
PY
)"
readonly ASSET_PATH
curl --fail --silent --show-error --max-time 20 \
    --proto '=https' \
    --tlsv1.2 \
    --dump-header "${ASSET_HEADERS}" \
    --output /dev/null \
    "https://amigo.tolstik.ru${ASSET_PATH}"
require_header '^cache-control:[[:space:]]*public,[[:space:]]*max-age=31536000,[[:space:]]*immutable' \
    "${ASSET_HEADERS}"
[[ "$(grep --ignore-case --count '^cache-control:' "${ASSET_HEADERS}")" -eq 1 ]] \
    || amigo_die "hashed asset returned conflicting Cache-Control headers"
amigo_log "PASS hashed frontend asset has one immutable cache policy"

curl --fail --silent --show-error --max-time 20 \
    --proto '=https' \
    --tlsv1.2 \
    --dump-header "${API_HEADERS}" \
    --output "${API_BODY}" \
    "${AMIGO_PUBLIC_URL}api/v1/overview"
python3 -m json.tool "${API_BODY}" >/dev/null
require_header '^cache-control:.*no-store' "${API_HEADERS}"
amigo_log "PASS public overview API returns JSON"

for hidden_path in healthz amigo/healthz amigo/internal/health; do
    external_health_status=$(
        curl --silent --show-error --location --max-time 15 \
            --proto '=https' \
            --tlsv1.2 \
            --output /dev/null \
            --write-out '%{http_code}' \
            "https://amigo.tolstik.ru/${hidden_path}"
    )
    if [[ "${external_health_status}" =~ ^2 ]]; then
        amigo_die "health endpoint is externally published: /${hidden_path}"
    fi
done
amigo_log "PASS direct and prefixed health endpoints are not externally published"

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
