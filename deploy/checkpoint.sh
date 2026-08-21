#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
# shellcheck source=lib/common.sh
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

usage() {
    printf 'Usage: %s [--verification-passed] /srv/amigo-rollbacks/YYYYMMDDTHHMMSSZ\n' \
        "${0##*/}" >&2
    exit 2
}

VERIFICATION_PASSED=0
case $# in
    1)
        SNAPSHOT=$1
        ;;
    2)
        [[ $1 == "--verification-passed" ]] || usage
        VERIFICATION_PASSED=1
        SNAPSHOT=$2
        ;;
    *)
        usage
        ;;
esac
readonly VERIFICATION_PASSED SNAPSHOT

amigo_require_root
amigo_require_commands \
    awk bash cat date docker flock git install mktemp python3 realpath rm sha256sum stat
amigo_require_production_layout
amigo_assert_snapshot "${SNAPSHOT}"
amigo_acquire_deploy_lock

[[ -f "${AMIGO_APP_DIR}/AGENTS.md" ]] || amigo_die "project AGENTS.md is missing"
[[ -f "${AMIGO_APP_DIR}/docs/runbook.md" ]] || amigo_die "project runbook is missing"
[[ -f "${AMIGO_APP_DIR}/docs/production-checkpoint.md" ]] \
    || amigo_die "production checkpoint document is missing"
[[ -f "${SCRIPT_DIR}/checkpoint_markdown.py" ]] \
    || amigo_die "checkpoint Markdown updater is missing"

git -C "${AMIGO_APP_DIR}" diff --cached --quiet \
    || amigo_die "staged production changes exist before checkpoint"
git -C "${AMIGO_APP_DIR}" diff --quiet -- \
    . \
    ':(exclude)AGENTS.md' \
    ':(exclude)docs/runbook.md' \
    ':(exclude)docs/production-checkpoint.md' \
    || amigo_die "non-checkpoint tracked files are dirty; reconcile them first"
[[ -z "$(git -C "${AMIGO_APP_DIR}" ls-files --others --exclude-standard)" ]] \
    || amigo_die "untracked production checkout files exist before checkpoint"

if [[ ${VERIFICATION_PASSED} -eq 1 ]]; then
    amigo_log "using the complete verification suite that passed immediately before checkpoint"
else
    amigo_log "running the complete verification suite before recording the checkpoint"
    bash "${SCRIPT_DIR}/verify-production.sh"
fi

GIT_SHA="$(amigo_current_release)"
CHECKED_AT_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
CHECKED_AT_MOSCOW="$(TZ=Europe/Moscow date +'%Y-%m-%d %H:%M:%S %Z')"
COMPOSE_SHA256="$(sha256sum "${AMIGO_COMPOSE_FILE}" | awk '{ print $1 }')"
NGINX_SNIPPET_SHA256="$(sha256sum "${AMIGO_NGINX_SNIPPET}" | awk '{ print $1 }')"
NGINX_HTTP_SHA256="$(sha256sum "${AMIGO_NGINX_HTTP_CONFIG}" | awk '{ print $1 }')"
CODEX_BINARY_SHA256="$(sha256sum /srv/amigo/data/codex-bin/codex | awk '{ print $1 }')"
RELEASE_WRAPPER_SHA256="$(sha256sum /usr/local/sbin/amigo-release | awk '{ print $1 }')"
RELEASE_SUDOERS_SHA256="$(sha256sum /etc/sudoers.d/amigo-release | awk '{ print $1 }')"
TEMP_BLOCK="$(mktemp /run/amigo-checkpoint-block.XXXXXX)"
TEMP_DOCUMENT="$(mktemp /run/amigo-checkpoint-document.XXXXXX)"
TEMP_IMAGES="$(mktemp /run/amigo-checkpoint-images.XXXXXX)"
readonly GIT_SHA CHECKED_AT_UTC CHECKED_AT_MOSCOW
readonly COMPOSE_SHA256 NGINX_SNIPPET_SHA256 NGINX_HTTP_SHA256 CODEX_BINARY_SHA256
readonly RELEASE_WRAPPER_SHA256 RELEASE_SUDOERS_SHA256
readonly TEMP_BLOCK TEMP_DOCUMENT TEMP_IMAGES

cleanup() {
    rm -f -- "${TEMP_BLOCK}" "${TEMP_DOCUMENT}" "${TEMP_IMAGES}"
}
trap cleanup EXIT

for service in web worker ingest ai-worker ai-gateway lab-parser db; do
    container_id=$(amigo_compose ps -q "${service}")
    [[ -n "${container_id}" ]] || amigo_die "cannot record image for missing service: ${service}"
    image_ref=$(docker inspect --format '{{.Config.Image}}' "${container_id}")
    image_id=$(docker inspect --format '{{.Image}}' "${container_id}")
    # Literal backticks are intentional Markdown.
    # shellcheck disable=SC2016
    printf -- '- `%s`: `%s` (`%s`)\n' "${service}" "${image_ref}" "${image_id}" \
        >>"${TEMP_IMAGES}"
done

# The backticks below are intentional Markdown, not shell substitutions.
# shellcheck disable=SC2016
{
    printf -- '- Status: **deployed and verified**\n'
    printf -- '- Production URL: `%s`\n' "${AMIGO_PUBLIC_URL}"
    printf -- '- Verified at: `%s` (`%s`)\n' "${CHECKED_AT_UTC}" "${CHECKED_AT_MOSCOW}"
    printf -- '- Git SHA: `%s`\n' "${GIT_SHA}"
    printf -- '- Latest rollback snapshot: `%s`\n' "${SNAPSHOT}"
    printf -- '- Installed config SHA-256: Compose `%s`; nginx locations `%s`; nginx rate limit `%s`.\n' \
        "${COMPOSE_SHA256}" "${NGINX_SNIPPET_SHA256}" "${NGINX_HTTP_SHA256}"
    printf -- '- Pinned Codex: `0.148.0` (`sha256:%s`).\n' "${CODEX_BINARY_SHA256}"
    printf -- '- Release access SHA-256: wrapper `%s`; sudoers policy `%s`.\n' \
        "${RELEASE_WRAPPER_SHA256}" "${RELEASE_SUDOERS_SHA256}"
    printf -- '- Verification: all seven Compose services healthy; application services use the release image; PostgreSQL ready; the current worker completed a successful post-start Withings incremental job; web and ingest are bound only to `127.0.0.1:18181` and `127.0.0.1:18182`; database-owned originals, repaired laboratory dates, analyte guides, signed Android updater/APK, laboratory and study queues, assistant/queue SSE, authentication, exact Origin/CSRF, authenticated API/CSV/upload checks, root-only laboratory storage, parser/gateway isolation and unpublished ports, container secret boundaries, pinned Codex hash, fixed `gpt-5.6-sol`/`amigo-health-v4` gateway health, root-owned least-privilege release access, signed-ingest rejection, origin proxy, HTTPS login shell, hidden health routes, immutable frontend assets, cron isolation, previous-release auth-floor recovery assets, and the explicit legacy disaster-fallback guard passed.\n'
    printf -- '- Installed image references and IDs:\n\n'
    cat "${TEMP_IMAGES}"
    printf -- '- Previous-release recovery command: `sudo /srv/amigo/deploy/restore-previous-release.sh %s`\n' "${SNAPSHOT}"
    printf -- '- Legacy disaster fallback command: `sudo /srv/amigo/deploy/rollback.sh --to-legacy %s`\n' "${SNAPSHOT}"
} >"${TEMP_BLOCK}"

{
    printf '# Production checkpoint\n\n'
    # Literal backticks are intentional Markdown.
    # shellcheck disable=SC2016
    printf '> Generated by `deploy/checkpoint.sh` after all production checks passed.\n\n'
    cat "${TEMP_BLOCK}"
    printf '\nThe checkpoint contains no credentials. Commit these documentation changes back to the canonical repository before reporting the deployment complete.\n'
} >"${TEMP_DOCUMENT}"

install -o "$(stat -c '%U' "${AMIGO_APP_DIR}/docs/production-checkpoint.md")" \
    -g "$(stat -c '%G' "${AMIGO_APP_DIR}/docs/production-checkpoint.md")" \
    -m 0644 "${TEMP_DOCUMENT}" "${AMIGO_APP_DIR}/docs/production-checkpoint.md"
python3 "${SCRIPT_DIR}/checkpoint_markdown.py" \
    "${AMIGO_APP_DIR}/AGENTS.md" "${TEMP_BLOCK}"
python3 "${SCRIPT_DIR}/checkpoint_markdown.py" \
    "${AMIGO_APP_DIR}/docs/runbook.md" "${TEMP_BLOCK}"

install -d -o root -g root -m 0700 \
    "${AMIGO_STATE_DIR}" "${AMIGO_STATE_DIR}/deployments"
install -o root -g root -m 0600 \
    "${TEMP_DOCUMENT}" "${AMIGO_STATE_DIR}/deployments/${CHECKED_AT_UTC//:/-}.md"

amigo_log "recording a local documentation-only checkpoint commit"
git -C "${AMIGO_APP_DIR}" add -- \
    AGENTS.md docs/runbook.md docs/production-checkpoint.md
git -C "${AMIGO_APP_DIR}" diff --cached --quiet \
    && amigo_die "checkpoint generated no documentation changes"
git -C "${AMIGO_APP_DIR}" \
    -c user.name='Amigo production checkpoint' \
    -c user.email='checkpoint@localhost' \
    commit --no-gpg-sign --no-verify \
    -m "docs: record production checkpoint ${GIT_SHA}"
CHECKPOINT_COMMIT="$(git -C "${AMIGO_APP_DIR}" rev-parse HEAD)"
CHECKPOINT_REF="refs/amigo/checkpoints/${GIT_SHA}"
readonly CHECKPOINT_COMMIT CHECKPOINT_REF
git -C "${AMIGO_APP_DIR}" update-ref "${CHECKPOINT_REF}" "${CHECKPOINT_COMMIT}"
[[ -z "$(git -C "${AMIGO_APP_DIR}" status --porcelain=v1 -uall)" ]] \
    || amigo_die "production checkout is dirty after checkpoint commit"

amigo_log "DOCUMENTATION CHECKPOINT WRITTEN AND COMMITTED: ${CHECKPOINT_COMMIT}"
amigo_log "copy the checkpoint facts into the canonical repository before reporting completion"
