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
grep --quiet --fixed-strings "bash \"\${SCRIPT_DIR}/install-release-wrapper.sh\"" \
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
grep --quiet --fixed-strings -- '--no-auto-recovery' \
    "${SCRIPT_DIR}/deploy.sh" "${SCRIPT_DIR}/amigo-release" \
    || amigo_die "explicit fix-forward session mode is not wired through the release path"
grep --quiet --fixed-strings 'CANDIDATE_RUNTIME_ACTIVE=1' \
    "${SCRIPT_DIR}/deploy.sh" \
    || amigo_die "fix-forward mode cannot distinguish an active candidate runtime"
grep --quiet --fixed-strings '.fix-forward-session' \
    "${SCRIPT_DIR}/deploy.sh" \
    || amigo_die "current release cannot activate its authorized fix-forward session marker"
if grep --quiet --extended-regexp \
    'AMIGO_DEPLOY_LOCK_HELD=.*rollback\.sh' "${SCRIPT_DIR}/deploy.sh"; then
    amigo_die "deploy still invokes legacy disaster fallback automatically"
fi
grep --quiet --fixed-strings 'stop --timeout 180 ai-worker' "${SCRIPT_DIR}/deploy.sh" \
    || amigo_die "deploy does not grant the existing AI worker its required stop timeout"
grep --quiet --fixed-strings \
    'AMIGO_AI_GATEWAY_TIMEOUT_SECONDS: "180"' \
    "${PROJECT_ROOT}/compose.yaml" \
    || amigo_die "AI worker client timeout is not pinned to the bounded analysis deadline"
grep --quiet --fixed-strings \
    'AMIGO_AI_CODEX_TIMEOUT_SECONDS: "75"' \
    "${PROJECT_ROOT}/compose.yaml" \
    || amigo_die "non-analysis Codex contracts lost their fixed deadline"
grep --quiet --fixed-strings \
    'AMIGO_AI_ANALYSIS_TIMEOUT_SECONDS: "150"' \
    "${PROJECT_ROOT}/compose.yaml" \
    || amigo_die "routine analysis does not have its separate bounded deadline"
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
grep --quiet --fixed-strings \
    "checkpoint.sh\" --verification-passed \"\${SNAPSHOT}\"" \
    "${SCRIPT_DIR}/deploy.sh" \
    || amigo_die "deploy checkpoint does not reuse its immediately successful verification"
grep --quiet --fixed-strings "bash \"\${SCRIPT_DIR}/verify-production.sh\"" \
    "${SCRIPT_DIR}/checkpoint.sh" \
    || amigo_die "standalone checkpoint no longer runs the complete verification suite"
# Literal shell-variable syntax is the checkpoint source pattern being required.
# shellcheck disable=SC2016
grep --quiet --fixed-strings \
    'git -C "${AMIGO_APP_DIR}" add --' \
    "${SCRIPT_DIR}/checkpoint.sh" \
    || amigo_die "checkpoint no longer stages its three documentation files"
# Literal shell-variable syntax is the checkpoint source pattern being required.
# shellcheck disable=SC2016
grep --quiet --fixed-strings \
    'refs/amigo/checkpoints/${GIT_SHA}' \
    "${SCRIPT_DIR}/checkpoint.sh" \
    || amigo_die "checkpoint no longer preserves a durable local documentation ref"
grep --quiet --fixed-strings \
    'production checkout is dirty after checkpoint commit' \
    "${SCRIPT_DIR}/checkpoint.sh" \
    || amigo_die "checkpoint no longer guarantees a clean production checkout"
pull_line=$(grep --line-number --fixed-strings 'amigo_compose pull db' \
    "${SCRIPT_DIR}/deploy.sh" | cut -d: -f1)
# Literal shell-variable syntax is the deploy source pattern being required.
# shellcheck disable=SC2016
image_pull_line=$(grep --line-number --fixed-strings 'docker pull "${CANDIDATE_IMAGE_SOURCE}"' \
    "${SCRIPT_DIR}/deploy.sh" | cut -d: -f1)
# Literal command substitution is the deploy source pattern being required.
# shellcheck disable=SC2016
snapshot_line=$(grep --line-number --fixed-strings \
    'SNAPSHOT="$(AMIGO_DEPLOY_LOCK_HELD=1 bash "${SCRIPT_DIR}/pre-cutover-backup.sh")"' \
    "${SCRIPT_DIR}/deploy.sh" | cut -d: -f1)
cutover_line=$(grep --line-number --fixed-strings 'CUTOVER_STARTED=1' \
    "${SCRIPT_DIR}/deploy.sh" | head -n 1 | cut -d: -f1)
[[ -n "${pull_line}" && -n "${image_pull_line}" && -n "${snapshot_line}" \
    && -n "${cutover_line}" && ${pull_line} -lt ${snapshot_line} \
    && ${image_pull_line} -lt ${snapshot_line} && ${snapshot_line} -lt ${cutover_line} ]] \
    || amigo_die "release images must be prepared before the verified cutover snapshot"
if grep --quiet --fixed-strings 'amigo_compose build' "${SCRIPT_DIR}/deploy.sh"; then
    amigo_die "production deploy still builds the application image on the weak server"
fi
# Literal shell-variable syntax is the deploy source pattern being required.
# shellcheck disable=SC2016
grep --quiet --fixed-strings 'ghcr.io/tolstik/amigo:${RELEASE_SHA}' \
    "${SCRIPT_DIR}/deploy.sh" \
    || amigo_die "production deploy does not pull the immutable CI image"
[[ "$(grep --count --fixed-strings 'python -m app.cli bootstrap' "${SCRIPT_DIR}/deploy.sh")" -eq 1 ]] \
    || amigo_die "deploy must run exactly one bootstrap/migration pass"
if grep --quiet --fixed-strings 'python -m app.cli migrate' "${SCRIPT_DIR}/deploy.sh"; then
    amigo_die "deploy redundantly runs migrate before bootstrap"
fi
grep --quiet --fixed-strings 'python -m app.cli backfill-files' "${SCRIPT_DIR}/deploy.sh" \
    || amigo_die "deploy does not verify database-owned laboratory originals"
grep --quiet --fixed-strings 'python -m app.cli sync --suppress-notifications' \
    "${SCRIPT_DIR}/deploy.sh" \
    || amigo_die "deploy does not use the bounded incremental synchronization"
if grep --quiet --fixed-strings 'sync --full' "${SCRIPT_DIR}/deploy.sh"; then
    amigo_die "deploy still performs an expensive full Withings synchronization"
fi
# Literal shell-variable syntax is the deploy source pattern being required.
# shellcheck disable=SC2016
grep --quiet --fixed-strings 'cmp --silent "${LEGACY_IMPORT_CANDIDATE}"' \
    "${SCRIPT_DIR}/deploy.sh" \
    || amigo_die "deploy rewrites unchanged legacy rollback exports"
grep --quiet --fixed-strings \
    'https://github.com/tolstik/amigo/releases/download/v5.2.1/Amigo-1.4.0.apk' \
    "${SCRIPT_DIR}/deploy.sh" \
    || amigo_die "deploy does not fetch the published signed Android update"
grep --quiet --fixed-strings \
    '4a3a083c2b5c54482d2393526c0e6775087df53a0d3f6d6f9f568e80db32f995' \
    "${SCRIPT_DIR}/deploy.sh" \
    || amigo_die "deploy does not pin the signed Android update hash"
for android_release_pin in \
    'AMIGO_ANDROID_APK_VERSION_CODE: "15"' \
    'AMIGO_ANDROID_APK_VERSION_NAME: "1.4.0"'; do
    grep --quiet --fixed-strings "${android_release_pin}" \
        "${PROJECT_ROOT}/compose.yaml" \
        || amigo_die "Compose does not pin the Android 1.4.0 metadata contract"
done
grep --quiet --fixed-strings 'EXPECTED_ANDROID_APK_SIZE_BYTES=3504370' \
    "${SCRIPT_DIR}/verify-production.sh" \
    || amigo_die "production verification does not pin the signed Android update size"
grep --quiet --fixed-strings \
    'needs: [backend, frontend, frontend-e2e, android, release-gates]' \
    "${PROJECT_ROOT}/.github/workflows/ci.yml" \
    || amigo_die "CI image publication is not gated by every test job"
# Literal GitHub Actions variable syntax is the workflow source pattern being required.
# shellcheck disable=SC2016
grep --quiet --fixed-strings 'docker push "ghcr.io/tolstik/amigo:${GITHUB_SHA}"' \
    "${PROJECT_ROOT}/.github/workflows/ci.yml" \
    || amigo_die "CI does not publish an immutable SHA-tagged production image"

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
    previous_compose_sha256 \
    previous_android_apk_present; do
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
grep --quiet --fixed-strings \
    'error_page 503 =503 @amigo_auth_floor_503;' \
    "${SCRIPT_DIR}/nginx/amigo.maintenance.locations.conf" \
    || amigo_die "auth-floor maintenance status still depends on the shared 503 error page"
grep --quiet --fixed-strings 'location @amigo_auth_floor_503 {' \
    "${SCRIPT_DIR}/nginx/amigo.maintenance.locations.conf" \
    || amigo_die "auth-floor maintenance snippet lacks its isolated 503 handler"
grep --quiet --fixed-strings 'error_page 418 =418 /amigo/;' \
    "${SCRIPT_DIR}/nginx/amigo.maintenance.locations.conf" \
    || amigo_die "auth-floor named handler still inherits the shared 503 error page"
grep --quiet --fixed-strings \
    'installed auth-floor locations do not match the candidate maintenance release' \
    "${SCRIPT_DIR}/pre-cutover-backup.sh" \
    || amigo_die "pre-cutover backup cannot safely resume from an exact auth-floor route"
grep --quiet --fixed-strings \
    'installed auth-floor HTTP config does not match the candidate release' \
    "${SCRIPT_DIR}/pre-cutover-backup.sh" \
    || amigo_die "pre-cutover backup cannot safely resume with the exact candidate HTTP config"
# Literal nginx variable syntax is the source pattern being rejected.
# shellcheck disable=SC2016
if grep --quiet --fixed-strings 'rewrite ^/amigo/(.*)$ /$1 break;' \
    "${SCRIPT_DIR}/nginx/amigo.locations.conf"; then
    amigo_die "dynamic managed routes still use capture-unsafe URI rewriting"
fi
# These are literal nginx named-capture variables, not shell expansions.
# shellcheck disable=SC2016
for explicit_dynamic_proxy in \
    'api/v1/labs/documents/$amigo_lab_action_document_id/$amigo_lab_document_action' \
    'api/v1/labs/documents/$amigo_lab_view_document_id/view' \
    'api/v1/labs/documents/$amigo_lab_create_document_id/results' \
    'api/v1/labs/documents/$amigo_lab_detail_document_id' \
    'api/v1/labs/results/$amigo_lab_patch_result_id' \
    'api/v1/studies/documents/$amigo_study_action_document_id/$amigo_study_document_action' \
    'api/v1/studies/documents/$amigo_study_view_document_id/view' \
    'api/v1/studies/documents/$amigo_study_document_id' \
    'api/v1/assistant/messages/$amigo_chat_retry_id/retry' \
    'api/v1/assistant/messages/$amigo_chat_events_id/events' \
    'api/v1/tasks/$amigo_task_action_id/$amigo_task_action' \
    'api/v1/tasks/$amigo_task_id' \
    'api/v1/reports/doctor/$amigo_doctor_pdf_id.pdf' \
    'api/v1/reports/doctor/$amigo_doctor_report_id'; do
    grep --quiet --fixed-strings "${explicit_dynamic_proxy}" \
        "${SCRIPT_DIR}/nginx/amigo.locations.conf" \
        || amigo_die "managed route lacks explicit dynamic upstream URI: ${explicit_dynamic_proxy}"
done
managed_rate_limit_count="$(grep --count --fixed-strings 'limit_req zone=' \
    "${SCRIPT_DIR}/nginx/amigo.locations.conf")"
managed_rate_status_count="$(grep --count --fixed-strings 'limit_req_status 429;' \
    "${SCRIPT_DIR}/nginx/amigo.locations.conf")"
[[ "${managed_rate_limit_count}" -eq "${managed_rate_status_count}" ]] \
    || amigo_die "every managed rate limit must return explicit HTTP 429"
# Literal nginx variable syntax is the source pattern being required.
# shellcheck disable=SC2016
grep --quiet --fixed-strings \
    'limit_req_zone $binary_remote_addr zone=amigo_report:10m rate=60r/m;' \
    "${SCRIPT_DIR}/nginx/amigo.http.conf" \
    || amigo_die "doctor report creation has no dedicated rate-limit zone"
[[ "$(grep --count --fixed-strings 'limit_req zone=amigo_report burst=5 nodelay;' \
    "${SCRIPT_DIR}/nginx/amigo.locations.conf")" -eq 1 ]] \
    || amigo_die "doctor report creation must use its dedicated rate-limit zone exactly once"
doctor_create_limit="$(awk '
    /^location = \/amigo\/api\/v1\/reports\/doctor \{$/ { in_doctor_create = 1; next }
    in_doctor_create && /limit_req zone=/ {
        sub(/^[[:space:]]+/, "")
        print
        exit
    }
' "${SCRIPT_DIR}/nginx/amigo.locations.conf")"
[[ "${doctor_create_limit}" == 'limit_req zone=amigo_report burst=5 nodelay;' ]] \
    || amigo_die "exact doctor report creation route does not use the dedicated rate limit"
[[ "$(grep --count --fixed-strings 'limit_req zone=amigo_report burst=10 nodelay;' \
    "${SCRIPT_DIR}/nginx/amigo.locations.conf")" -eq 2 ]] \
    || amigo_die "doctor report access routes must share the dedicated zone with burst 10"
grep --quiet --fixed-strings 'limit_req zone=amigo_upload burst=25 nodelay;' \
    "${SCRIPT_DIR}/nginx/amigo.locations.conf" \
    || amigo_die "upload burst does not cover one bounded 25-file UI batch"
for ingest_route in \
    'location = /amigo-ingest/v1/health-connect/batches {' \
    'location = /amigo-ingest/v1/mi-fitness/batches {' \
    'location = /amigo-ingest/v1/mi-fitness/status {'; do
    grep --quiet --fixed-strings "${ingest_route}" \
        "${SCRIPT_DIR}/nginx/amigo.locations.conf" \
        || amigo_die "normal managed ingest route is missing: ${ingest_route}"
    grep --quiet --fixed-strings "${ingest_route}" \
        "${SCRIPT_DIR}/nginx/amigo.maintenance.locations.conf" \
        || amigo_die "maintenance managed ingest route is missing: ${ingest_route}"
done
for queue_route in \
    'location = /amigo/api/v1/labs/uploads {' \
    'location = /amigo/api/v1/labs/events {' \
    'location = /amigo/api/v1/studies/uploads {' \
    'location = /amigo/api/v1/studies/events {' \
    'location = /amigo/api/v1/data-quality {' \
    'location = /amigo/api/v1/labs/compare {' \
    'location = /amigo/api/v1/tasks {' \
    'location = /amigo/api/v1/reports/doctor {'; do
    grep --quiet --fixed-strings "${queue_route}" "${SCRIPT_DIR}/nginx/amigo.locations.conf" \
        || amigo_die "managed route is missing: ${queue_route}"
done
for canonical_uuid_capture in \
    'amigo_task_action_id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' \
    'amigo_task_id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' \
    'amigo_doctor_pdf_id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' \
    'amigo_doctor_report_id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'; do
    grep --quiet --fixed-strings "${canonical_uuid_capture}" \
        "${SCRIPT_DIR}/nginx/amigo.locations.conf" \
        || amigo_die "new dynamic route lacks a canonical lowercase UUID capture"
done
[[ "$(grep --count --fixed-strings 'proxy_pass_header X-Accel-Buffering;' \
    "${SCRIPT_DIR}/nginx/amigo.locations.conf")" -eq 3 ]] \
    || amigo_die "assistant, laboratory, and study SSE routes must pass no-buffer headers"
grep --quiet --fixed-strings 'proxy_pass_header X-Accel-Buffering;' \
    "${SCRIPT_DIR}/nginx/amigo.locations.conf" \
    || amigo_die "assistant SSE route does not pass its no-buffer response contract"
# Literal shell-variable syntax is the verifier source pattern being required.
# shellcheck disable=SC2016
grep --quiet --fixed-strings \
    '[[ "${ORIGIN_ASSETLINKS_POST_STATUS}" == "405" ]]' \
    "${SCRIPT_DIR}/verify-production.sh" \
    || amigo_die "production verification does not require origin assetlinks POST 405"
# Literal shell-variable syntax is the verifier source pattern being required.
# shellcheck disable=SC2016
grep --quiet --fixed-strings \
    '[[ "${ASSETLINKS_POST_STATUS}" == "403" || "${ASSETLINKS_POST_STATUS}" == "405" ]]' \
    "${SCRIPT_DIR}/verify-production.sh" \
    || amigo_die "production verification does not accept the public edge POST denial contract"
# Literal shell-variable syntax is the verifier source pattern being required.
# shellcheck disable=SC2016
grep --quiet --fixed-strings '"${SSE_ORIGIN_HEADERS}"' \
    "${SCRIPT_DIR}/verify-production.sh" \
    || amigo_die "production verification does not inspect the origin SSE headers"
[[ "$(grep --count --fixed-strings \
    "require_header '^x-accel-buffering:[[:space:]]*no'" \
    "${SCRIPT_DIR}/verify-production.sh")" -eq 2 ]] \
    || amigo_die "assistant plus shared queue verification must require origin no-buffer headers"
grep --quiet --fixed-strings 'lab-parser' "${SCRIPT_DIR}/rollback.sh" \
    || amigo_die "legacy disaster fallback does not stop the isolated laboratory parser"
for verification_contract in \
    '"api/v1/data-quality?range=30d" data-quality' \
    '"api/v1/tasks?state=open" tasks' \
    'DOCTOR_REPORT_ID' \
    'xiaomi_finalized_only' \
    'sleep_minutes' \
    'evidence_ids'; do
    grep --quiet --fixed-strings "${verification_contract}" \
        "${SCRIPT_DIR}/verify-production.sh" \
        || amigo_die "production verification is missing v5.2 contract: ${verification_contract}"
done
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
