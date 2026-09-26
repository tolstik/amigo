#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib/private-staging.sh
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/private-staging.sh"

fail() {
    printf 'private staging contract failed: %s\n' "$1" >&2
    exit 1
}

fixture_dir="$(mktemp -d)"
trap 'rm -rf -- "${fixture_dir}"' EXIT
fixture="${fixture_dir}/staged file.html"
printf 'synthetic original\n' >"${fixture}"
chmod 0600 "${fixture}"
original_id="$(amigo_private_staging_identity "${fixture}")"

# A replacement under the same pathname must survive cleanup.
mv -- "${fixture}" "${fixture_dir}/original.html"
printf 'synthetic replacement\n' >"${fixture}"
chmod 0600 "${fixture}"
[[ "$(amigo_private_staging_identity "${fixture}")" != "${original_id}" ]] \
    || fail "replacement reused the original inode"
if amigo_remove_private_staging_if_same "${fixture}" "${original_id}"; then
    fail "replaced staging was deleted"
fi
[[ "$(<"${fixture}")" == 'synthetic replacement' ]] \
    || fail "replacement content changed"

# A symlink and directory are never treated as an imported regular file.
rm -- "${fixture}"
ln -s -- "${fixture_dir}/original.html" "${fixture}"
if amigo_remove_private_staging_if_same "${fixture}" "${original_id}"; then
    fail "symlink staging was deleted"
fi
[[ -L "${fixture}" ]] || fail "symlink was changed"
rm -- "${fixture}"
mkdir -- "${fixture}"
if amigo_remove_private_staging_if_same "${fixture}" "${original_id}"; then
    fail "directory staging was deleted"
fi
rmdir -- "${fixture}"

# The exact imported regular file is removed after successful deployment.
if ! amigo_remove_private_staging_if_same "${fixture_dir}/original.html" "${original_id}"; then
    fail "original staging was not removed"
fi
[[ ! -e "${fixture_dir}/original.html" ]] || fail "original staging remains"
