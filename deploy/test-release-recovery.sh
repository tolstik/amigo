#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
readonly PROJECT_ROOT
# shellcheck source=lib/common.sh
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

usage() {
    printf 'Usage: %s PREVIOUS_RELEASE_GIT_SHA\n' "${0##*/}" >&2
    exit 2
}

[[ $# -eq 1 ]] || usage
readonly PREVIOUS_RELEASE_SHA=$1
[[ "${PREVIOUS_RELEASE_SHA}" =~ ^[0-9a-f]{40,64}$ ]] || usage
CANDIDATE_RELEASE_SHA="$(git -C "${PROJECT_ROOT}" rev-parse HEAD)"
readonly CANDIDATE_RELEASE_SHA

for script in \
    deploy.sh \
    pre-cutover-backup.sh \
    restore-previous-release.sh \
    takeover-from-legacy.sh \
    rollback.sh \
    nginx-control.sh \
    cron-control.sh \
    checkpoint.sh \
    install-release-wrapper.sh \
    test-recovery-transitions.sh \
    lib/common.sh; do
    bash -n "${SCRIPT_DIR}/${script}"
done
php -l "${SCRIPT_DIR}/extract_legacy_secrets.php" >/dev/null
bash "${SCRIPT_DIR}/test-recovery-transitions.sh"

for executable_script in \
    deploy.sh pre-cutover-backup.sh restore-previous-release.sh \
    takeover-from-legacy.sh rollback.sh nginx-control.sh install-release-wrapper.sh \
    test-recovery-transitions.sh; do
    [[ -x "${SCRIPT_DIR}/${executable_script}" ]] \
        || amigo_die "deployment script is not executable: ${executable_script}"
done
bash -n "${SCRIPT_DIR}/amigo-release"

[[ -x "${SCRIPT_DIR}/amigo-release" ]] \
    || amigo_die "versioned release wrapper is not executable"
grep --quiet --fixed-strings 'bash "${SCRIPT_DIR}/install-release-wrapper.sh"' \
    "${SCRIPT_DIR}/deploy.sh" \
    || amigo_die "deploy does not install the least-privilege release wrapper"
[[ "$(<"${SCRIPT_DIR}/sudoers/amigo-release")" \
    == 'tolstik ALL=(root) NOPASSWD: NOSETENV: /usr/local/sbin/amigo-release *' ]] \
    || amigo_die "release sudoers policy is not narrowly scoped to the wrapper"

amigo_assert_release_rollback_compatible \
    "${PROJECT_ROOT}" "${PREVIOUS_RELEASE_SHA}" "${CANDIDATE_RELEASE_SHA}"

grep --quiet --fixed-strings \
    "\"\${SCRIPT_DIR}/restore-previous-release.sh\" \"\${SNAPSHOT}\"" \
    "${SCRIPT_DIR}/deploy.sh" \
    || amigo_die "deploy error trap does not call previous-release recovery"
if grep --quiet --extended-regexp \
    'AMIGO_DEPLOY_LOCK_HELD=.*rollback\.sh' "${SCRIPT_DIR}/deploy.sh"; then
    amigo_die "deploy still invokes legacy disaster fallback automatically"
fi
grep --quiet --fixed-strings 'stop --timeout 120 ai-worker' "${SCRIPT_DIR}/deploy.sh" \
    || amigo_die "deploy does not grant the existing AI worker its required stop timeout"
[[ "$(grep --count --fixed-strings \
    'python -m app.cli ai-retry-current --worker-stopped' "${SCRIPT_DIR}/deploy.sh")" -eq 1 ]] \
    || amigo_die "deploy must prepare exactly one current AI retry"
grep --quiet --fixed-strings 'for ai_attempt in {1..4}' "${SCRIPT_DIR}/deploy.sh" \
    || amigo_die "deploy is missing the four-attempt foreground AI bound"
grep --quiet --fixed-strings "if [[ \${ai_attempt} -lt 4 ]]" "${SCRIPT_DIR}/deploy.sh" \
    || amigo_die "deploy does not limit backoff removal to attempts before the fourth"
grep --quiet --fixed-strings 'candidate SHA is already the recorded release' \
    "${SCRIPT_DIR}/deploy.sh" \
    || amigo_die "deploy does not reject a mutable same-SHA rebuild"
pull_line=$(grep --line-number --fixed-strings 'amigo_compose pull db' \
    "${SCRIPT_DIR}/deploy.sh" | cut -d: -f1)
cutover_line=$(grep --line-number --fixed-strings 'CUTOVER_STARTED=1' \
    "${SCRIPT_DIR}/deploy.sh" | head -n 1 | cut -d: -f1)
[[ -n "${pull_line}" && -n "${cutover_line}" && ${cutover_line} -lt ${pull_line} ]] \
    || amigo_die "mutable PostgreSQL pull is outside automatic previous-release recovery"

rollback_output="$(mktemp)"
trap 'rm -f -- "${rollback_output}"' EXIT
if bash "${SCRIPT_DIR}/rollback.sh" \
    /srv/amigo-rollbacks/20000101T000000Z >"${rollback_output}" 2>&1; then
    amigo_die "legacy fallback accepted a snapshot without --to-legacy"
else
    rollback_status=$?
fi
[[ ${rollback_status} -eq 2 ]] \
    || amigo_die "legacy fallback without confirmation returned an unexpected status"
grep --quiet --fixed-strings -- '--to-legacy' "${rollback_output}" \
    || amigo_die "legacy fallback usage does not require --to-legacy"

for metadata_key in \
    candidate_git_sha \
    previous_release_sha \
    previous_application_image \
    previous_application_image_id \
    previous_application_rollback_image \
    previous_database_image \
    previous_database_image_id \
    previous_database_rollback_image \
    previous_ai_model \
    previous_ai_prompt_version \
    previous_auth_floor \
    previous_managed_route_state \
    previous_compose_sha256; do
    grep --quiet --fixed-strings "${metadata_key}" "${SCRIPT_DIR}/pre-cutover-backup.sh" \
        || amigo_die "snapshot producer is missing metadata key: ${metadata_key}"
    grep --quiet --fixed-strings "${metadata_key}" "${SCRIPT_DIR}/restore-previous-release.sh" \
        || amigo_die "release recovery is missing metadata key: ${metadata_key}"
done

grep --quiet --fixed-strings 'SNAPSHOT_FORMAT="legacy-v0"' \
    "${SCRIPT_DIR}/takeover-from-legacy.sh" \
    || amigo_die "takeover does not support the verified older production snapshot format"
grep --quiet --fixed-strings 'amigo_assert_image_revision' \
    "${SCRIPT_DIR}/takeover-from-legacy.sh" \
    || amigo_die "takeover does not bind the recorded image to its OCI release"
grep --quiet --fixed-strings 'LEGACY_DISABLE_MINUTE' \
    "${SCRIPT_DIR}/takeover-from-legacy.sh" \
    || amigo_die "takeover does not wait through a cron boundary"
grep --quiet --fixed-strings -- '--allow-unhealthy-legacy-origin' \
    "${SCRIPT_DIR}/takeover-from-legacy.sh" \
    || amigo_die "takeover lacks the explicit degraded legacy-origin override"
grep --quiet --fixed-strings 'LEGACY_ORIGIN_WAS_HEALTHY' \
    "${SCRIPT_DIR}/takeover-from-legacy.sh" \
    || amigo_die "takeover failure reversal is not bound to legacy-origin health"
grep --quiet --fixed-strings 'amigo_assert_managed_route_active' \
    "${SCRIPT_DIR}/restore-previous-release.sh" \
    || amigo_die "previous-release recovery does not reject legacy route state"
grep --quiet --fixed-strings 'RECOVERY_MINUTE_BUCKET' \
    "${SCRIPT_DIR}/restore-previous-release.sh" \
    || amigo_die "previous-release recovery does not avoid minute-key collision"
grep --quiet --fixed-strings 'nginx-control.sh" maintenance' \
    "${SCRIPT_DIR}/restore-previous-release.sh" \
    || amigo_die "previous-release recovery does not enforce the auth-floor maintenance route"
grep --quiet --fixed-strings 'lab-parser' "${SCRIPT_DIR}/rollback.sh" \
    || amigo_die "legacy disaster fallback does not stop the isolated laboratory parser"
# Literal variable syntax is the unsafe source pattern being rejected.
# shellcheck disable=SC2016
if grep --quiet --fixed-strings \
    'cp -- "${SNAPSHOT}/nginx/my.conf" "${CANDIDATE_FILE}"' \
    "${SCRIPT_DIR}/nginx-control.sh"; then
    amigo_die "managed route recovery still replaces the shared nginx file wholesale"
fi
if grep --quiet --fixed-strings 'create --no-build --no-deps' \
    "${SCRIPT_DIR}/restore-previous-release.sh" \
    "${SCRIPT_DIR}/takeover-from-legacy.sh"; then
    amigo_die "recovery uses an unsupported docker compose create flag"
fi
for route_script in deploy.sh restore-previous-release.sh takeover-from-legacy.sh rollback.sh; do
    grep --quiet --fixed-strings 'amigo_wait_for_origin_http_200' \
        "${SCRIPT_DIR}/${route_script}" \
        || amigo_die "post-nginx route check lacks bounded HTTP 200 retry: ${route_script}"
done

if grep --quiet --fixed-strings 'pg_restore' \
    "${SCRIPT_DIR}/restore-previous-release.sh" "${SCRIPT_DIR}/takeover-from-legacy.sh"; then
    amigo_die "runtime recovery must not restore PostgreSQL automatically"
fi

takeover_extract_line=$(grep --line-number --fixed-strings \
    'extract_legacy_secrets.php' "${SCRIPT_DIR}/takeover-from-legacy.sh" \
    | tail -n 1 | cut -d: -f1)
takeover_compare_line=$(grep --line-number --fixed-strings \
    'legacy and Amigo Withings client IDs differ' "${SCRIPT_DIR}/takeover-from-legacy.sh" \
    | cut -d: -f1)
[[ -n "${takeover_extract_line}" && -n "${takeover_compare_line}" \
    && ${takeover_compare_line} -gt ${takeover_extract_line} ]] \
    || amigo_die "legacy client comparison must run only after live credential extraction"

if command -v shellcheck >/dev/null 2>&1; then
    shellcheck \
        "${SCRIPT_DIR}/deploy.sh" \
        "${SCRIPT_DIR}/pre-cutover-backup.sh" \
        "${SCRIPT_DIR}/restore-previous-release.sh" \
        "${SCRIPT_DIR}/takeover-from-legacy.sh" \
        "${SCRIPT_DIR}/rollback.sh" \
        "${SCRIPT_DIR}/nginx-control.sh" \
        "${SCRIPT_DIR}/checkpoint.sh" \
        "${SCRIPT_DIR}/install-release-wrapper.sh" \
        "${SCRIPT_DIR}/test-recovery-transitions.sh" \
        "${SCRIPT_DIR}/lib/common.sh" \
        "${SCRIPT_DIR}/amigo-release"
else
    printf 'shellcheck unavailable; syntax and recovery-contract checks still passed\n' >&2
fi

rm -f -- "${rollback_output}"
trap - EXIT
printf 'release recovery contract checks passed for %s -> %s\n' \
    "${PREVIOUS_RELEASE_SHA}" "${CANDIDATE_RELEASE_SHA}"
