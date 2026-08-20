#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
# shellcheck source=lib/common.sh
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

TRACE_FILE="$(mktemp)"
readonly TRACE_FILE
trap 'rm -f -- "${TRACE_FILE}"' EXIT

MOCK_ROUTE_DISABLE_STATUS=0
MOCK_ROUTE_MARKERS=0
MOCK_LEGACY_HTTP=200
MOCK_HANDBACK_STATUS=0
MOCK_COLLECTOR_STOP_STATUS=0

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
    printf '%s' "${MOCK_LEGACY_HTTP}"
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
    MOCK_ROUTE_DISABLE_STATUS=0
    MOCK_ROUTE_MARKERS=0
    MOCK_LEGACY_HTTP=200
    MOCK_HANDBACK_STATUS=0
    MOCK_COLLECTOR_STOP_STATUS=0
}

readonly TEST_COMPOSE="/snapshot/release/compose.yaml"
readonly TEST_RELEASE="0123456789abcdef0123456789abcdef01234567"
readonly TEST_SNAPSHOT="/srv/amigo-rollbacks/20000101T000000Z"

reset_mocks
amigo_revert_legacy_takeover \
    "${TEST_COMPOSE}" "${TEST_RELEASE}" "${TEST_SNAPSHOT}" 0 0
assert_trace "compose ${TEST_COMPOSE} ${TEST_RELEASE} stop worker ai-worker
compose ${TEST_COMPOSE} ${TEST_RELEASE} stop web ingest ai-gateway db
cron enable"

reset_mocks
amigo_revert_legacy_takeover \
    "${TEST_COMPOSE}" "${TEST_RELEASE}" "${TEST_SNAPSHOT}" 1 1
assert_trace "compose ${TEST_COMPOSE} ${TEST_RELEASE} stop worker ai-worker
handback ${TEST_COMPOSE} ${TEST_RELEASE}
nginx disable ${TEST_SNAPSHOT}
compose ${TEST_COMPOSE} ${TEST_RELEASE} stop web ingest ai-gateway db
cron enable"

reset_mocks
MOCK_ROUTE_DISABLE_STATUS=1
amigo_revert_legacy_takeover \
    "${TEST_COMPOSE}" "${TEST_RELEASE}" "${TEST_SNAPSHOT}" 0 1
assert_trace "compose ${TEST_COMPOSE} ${TEST_RELEASE} stop worker ai-worker
nginx disable ${TEST_SNAPSHOT}
cron disable"

reset_mocks
MOCK_ROUTE_MARKERS=2
amigo_revert_legacy_takeover \
    "${TEST_COMPOSE}" "${TEST_RELEASE}" "${TEST_SNAPSHOT}" 0 1
assert_trace "compose ${TEST_COMPOSE} ${TEST_RELEASE} stop worker ai-worker
nginx disable ${TEST_SNAPSHOT}
cron disable"

reset_mocks
MOCK_HANDBACK_STATUS=1
amigo_revert_legacy_takeover \
    "${TEST_COMPOSE}" "${TEST_RELEASE}" "${TEST_SNAPSHOT}" 1 0
assert_trace "compose ${TEST_COMPOSE} ${TEST_RELEASE} stop worker ai-worker
handback ${TEST_COMPOSE} ${TEST_RELEASE}
compose ${TEST_COMPOSE} ${TEST_RELEASE} stop web ingest ai-gateway db
cron disable"

reset_mocks
MOCK_COLLECTOR_STOP_STATUS=1
amigo_revert_legacy_takeover \
    "${TEST_COMPOSE}" "${TEST_RELEASE}" "${TEST_SNAPSHOT}" 1 1
assert_trace "compose ${TEST_COMPOSE} ${TEST_RELEASE} stop worker ai-worker
nginx disable ${TEST_SNAPSHOT}
cron disable"

printf 'mocked recovery transition checks passed\n'
