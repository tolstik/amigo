#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
# shellcheck source=lib/common.sh
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

TRACE_FILE="$(mktemp)"
CURL_COUNT_FILE="$(mktemp)"
readonly TRACE_FILE CURL_COUNT_FILE
trap 'rm -f -- "${TRACE_FILE}" "${CURL_COUNT_FILE}"' EXIT

MOCK_ROUTE_DISABLE_STATUS=0
MOCK_ROUTE_MARKERS=0
MOCK_LEGACY_HTTP=200
MOCK_HANDBACK_STATUS=0
MOCK_COLLECTOR_STOP_STATUS=0
MOCK_HTTP_FAILURES=0

trace() {
    printf '%s\n' "$*" >>"${TRACE_FILE}"
}

amigo_log() {
    :
}

amigo_compose_file_release() {
    local compose_file=$1
    local release_sha=$2
    shift 2
    if [[ "$*" == "config --services" ]]; then
        printf '%s\n' db web worker ingest ai-worker ai-gateway lab-parser
        return 0
    fi
    trace "compose ${compose_file} ${release_sha} $*"
    if [[ "$*" == "stop worker ai-worker" ]]; then
        return "${MOCK_COLLECTOR_STOP_STATUS}"
    fi
}

amigo_handback_withings_tokens() {
    trace "handback $*"
    return "${MOCK_HANDBACK_STATUS}"
}

bash() {
    case $1 in
        */nginx-control.sh)
            trace "nginx $2 $3"
            return "${MOCK_ROUTE_DISABLE_STATUS}"
            ;;
        */cron-control.sh)
            trace "cron $2"
            return 0
            ;;
        *)
            return 99
            ;;
    esac
}

amigo_assert_managed_route_inactive() {
    [[ "${MOCK_ROUTE_MARKERS}" -eq 0 ]]
}

curl() {
    local calls
    printf 'call\n' >>"${CURL_COUNT_FILE}"
    calls=$(wc -l <"${CURL_COUNT_FILE}")
    if [[ ${calls} -le ${MOCK_HTTP_FAILURES} ]]; then
        printf '404'
    else
        printf '%s' "${MOCK_LEGACY_HTTP}"
    fi
}

sleep() {
    :
}

assert_trace() {
    local expected=$1
    local actual
    actual=$(<"${TRACE_FILE}")
    [[ "${actual}" == "${expected}" ]] || {
        printf 'unexpected recovery trace\nexpected:\n%s\nactual:\n%s\n' \
            "${expected}" "${actual}" >&2
        return 1
    }
}

reset_mocks() {
    : >"${TRACE_FILE}"
    : >"${CURL_COUNT_FILE}"
    MOCK_ROUTE_DISABLE_STATUS=0
    MOCK_ROUTE_MARKERS=0
    MOCK_LEGACY_HTTP=200
    MOCK_HANDBACK_STATUS=0
    MOCK_COLLECTOR_STOP_STATUS=0
    MOCK_HTTP_FAILURES=0
}

readonly TEST_COMPOSE="/snapshot/release/compose.yaml"
readonly TEST_RELEASE="0123456789abcdef0123456789abcdef01234567"
readonly TEST_SNAPSHOT="/srv/amigo-rollbacks/20000101T000000Z"

reset_mocks
MOCK_HTTP_FAILURES=2
amigo_wait_for_origin_http_200 "/amigo/api/v1/overview" 3
[[ "$(wc -l <"${CURL_COUNT_FILE}")" -eq 3 ]]

reset_mocks
MOCK_HTTP_FAILURES=3
if amigo_wait_for_origin_http_200 "/amigo/" 2; then
    printf 'origin retry unexpectedly accepted a non-200 response\n' >&2
    exit 1
fi
[[ "$(wc -l <"${CURL_COUNT_FILE}")" -eq 2 ]]

reset_mocks
if amigo_wait_for_origin_http_200 "/amigo/../../healthz" 1; then
    printf 'origin retry unexpectedly accepted a non-allowlisted path\n' >&2
    exit 1
fi
[[ "$(wc -l <"${CURL_COUNT_FILE}")" -eq 0 ]]

reset_mocks
amigo_revert_legacy_takeover \
    "${TEST_COMPOSE}" "${TEST_RELEASE}" "${TEST_SNAPSHOT}" 0 0 1
assert_trace "compose ${TEST_COMPOSE} ${TEST_RELEASE} stop worker ai-worker
compose ${TEST_COMPOSE} ${TEST_RELEASE} stop web ingest ai-gateway lab-parser db
cron enable"

reset_mocks
amigo_revert_legacy_takeover \
    "${TEST_COMPOSE}" "${TEST_RELEASE}" "${TEST_SNAPSHOT}" 1 1 1
assert_trace "compose ${TEST_COMPOSE} ${TEST_RELEASE} stop worker ai-worker
handback ${TEST_COMPOSE} ${TEST_RELEASE}
nginx disable ${TEST_SNAPSHOT}
compose ${TEST_COMPOSE} ${TEST_RELEASE} stop web ingest ai-gateway lab-parser db
cron enable"

reset_mocks
MOCK_ROUTE_DISABLE_STATUS=1
amigo_revert_legacy_takeover \
    "${TEST_COMPOSE}" "${TEST_RELEASE}" "${TEST_SNAPSHOT}" 0 1 1
assert_trace "compose ${TEST_COMPOSE} ${TEST_RELEASE} stop worker ai-worker
nginx disable ${TEST_SNAPSHOT}
cron disable"

reset_mocks
MOCK_ROUTE_MARKERS=2
amigo_revert_legacy_takeover \
    "${TEST_COMPOSE}" "${TEST_RELEASE}" "${TEST_SNAPSHOT}" 0 1 1
assert_trace "compose ${TEST_COMPOSE} ${TEST_RELEASE} stop worker ai-worker
nginx disable ${TEST_SNAPSHOT}
cron disable"

reset_mocks
MOCK_HANDBACK_STATUS=1
amigo_revert_legacy_takeover \
    "${TEST_COMPOSE}" "${TEST_RELEASE}" "${TEST_SNAPSHOT}" 1 0 1
assert_trace "compose ${TEST_COMPOSE} ${TEST_RELEASE} stop worker ai-worker
handback ${TEST_COMPOSE} ${TEST_RELEASE}
compose ${TEST_COMPOSE} ${TEST_RELEASE} stop web ingest ai-gateway lab-parser db
cron disable"

reset_mocks
MOCK_COLLECTOR_STOP_STATUS=1
amigo_revert_legacy_takeover \
    "${TEST_COMPOSE}" "${TEST_RELEASE}" "${TEST_SNAPSHOT}" 1 1 1
assert_trace "compose ${TEST_COMPOSE} ${TEST_RELEASE} stop worker ai-worker
nginx disable ${TEST_SNAPSHOT}
cron disable"

reset_mocks
amigo_revert_legacy_takeover \
    "${TEST_COMPOSE}" "${TEST_RELEASE}" "${TEST_SNAPSHOT}" 0 0 0
assert_trace "compose ${TEST_COMPOSE} ${TEST_RELEASE} stop worker ai-worker
cron disable"

reset_mocks
MOCK_LEGACY_HTTP=500
amigo_revert_legacy_takeover \
    "${TEST_COMPOSE}" "${TEST_RELEASE}" "${TEST_SNAPSHOT}" 0 0 1
assert_trace "compose ${TEST_COMPOSE} ${TEST_RELEASE} stop worker ai-worker
cron disable"

reset_mocks
amigo_revert_legacy_takeover \
    "${TEST_COMPOSE}" "${TEST_RELEASE}" "${TEST_SNAPSHOT}" 0 1 0
assert_trace "compose ${TEST_COMPOSE} ${TEST_RELEASE} stop worker ai-worker
cron disable"

printf 'mocked recovery transition checks passed\n'
