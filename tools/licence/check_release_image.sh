#!/usr/bin/env bash
#
# A built image trusts Ignite Ember's release keys and nothing else
# (app/services/licence/keys.py). CI runs it on the image it builds:
#
#   tools/licence/check_release_image.sh IMAGE
#
# 1. The release marker is in the image, where the build writes it.
# 2. Nothing under /app names the test key: not its id, not its public half.
# 3. Told every way there is to trust the test key — APP_ENV=dev and
#    ENVIRONMENT=dev, with the development keys file and a pyproject.toml
#    mounted where a source checkout keeps them — the image still refuses
#    a licence signed with it, as `unknown_key`, through the same reader the
#    portal's licence page uses.
set -euo pipefail

image="${1:?usage: check_release_image.sh IMAGE}"
repo="$(cd "$(dirname "$0")/../.." && pwd)"
fixtures="$repo/tests/fixtures/licence"
key_id=igni-test-1
public="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$fixtures/dev-keys.json" "$key_id")"
fail() { echo "check_release_image: $*" >&2; exit 1; }

docker run --rm --entrypoint test "$image" -f /app/app/services/licence/RELEASE \
    || fail "$image has no release marker at /app/app/services/licence/RELEASE"

hits="$(docker run --rm --entrypoint grep "$image" -rlaF -e "$key_id" -e "$public" /app || true)"
[ -z "$hits" ] || fail "$image names the test key in: $hits"

reason="$(docker run --rm \
    -e APP_ENV=dev -e ENVIRONMENT=dev -e IGNI_LICENCE_FILE=/licence/licence.json \
    -e SECRET_ENCRYPTION_KEY=jdlEOjgCCb-E6_7ChdCTvVPB7jmbuOSSpk21W4buHDQ= \
    -v "$fixtures/golden.licence.json:/licence/licence.json:ro" \
    -v "$fixtures/dev-keys.json:/app/tests/fixtures/licence/dev-keys.json:ro" \
    -v "$repo/pyproject.toml:/app/pyproject.toml:ro" \
    --entrypoint python "$image" -c \
    'from app.services.deployment_licence import DeploymentLicence; print(DeploymentLicence.from_settings().overview().problem)')"
[ "$reason" = unknown_key ] \
    || fail "$image, told to trust the test key, read a licence signed with it as '$reason', not 'unknown_key'"

echo "check_release_image: $image trusts only the release keys"
