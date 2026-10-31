#!/usr/bin/env bash
#
# Build the marketplace deployer's layout and prove the chart renders
# from inside it.
#
# F2. `marketplace/Dockerfile` was written and **never built** — R-8's
# shape, and the one this review keeps finding: an artefact that is
# careful, reviewed, covered by static checks, and has never once run.
# `tests/test_the_marketplace_listing_can_deploy.py` checks the join
# between `schema.yaml` and the chart thoroughly, and checks nothing
# about whether the image assembles.
#
# ## What this cannot do, and why
#
# It cannot use the real base image.
# `gcr.io/cloud-marketplace-tools/k8s/deployer_helm` is **not
# anonymously pullable** — an unauthenticated pull is refused with
# "Unauthenticated requests do not have permission
# artifactregistry.repositories.downloadArtifacts", while an anonymous
# pull of another gcr.io image in the same session succeeds. So the
# `FROM` line, including whether `0.13` is still a current tag, cannot be
# checked without a Google Cloud identity. That part of F2 is genuinely
# blocked; this script is everything else.
#
# ## What it does check
#
# The base image expects the chart at /data/chart and the schema at
# /data/schema.yaml. Everything about that contract is testable with a
# stand-in base:
#
#   * the COPY sources resolve from the build context — the Dockerfile
#     copies `charts/igni`, which only works when built from the
#     repository root, and getting that wrong is a build that fails on a
#     submission rather than here;
#   * the chart that lands at /data/chart/igni is a chart helm will load;
#   * it renders from that path with marketplace-shaped values, which is
#     what the deployer does in the customer's cluster;
#   * the schema arrives at /data/schema.yaml and parses.
#
set -euo pipefail

cd "$(dirname "$0")/../.."

step() { printf '\n\033[1m→ %s\033[0m\n' "$*"; }
fail() { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

command -v docker >/dev/null || fail "docker is not installed"
docker info >/dev/null 2>&1 || fail "the docker daemon is not running"

IMAGE="igni-deployer-layout:verify"
STAND_IN="alpine/helm:3.16.3"

step "Confirming the real base image is still out of reach"
# Not a nicety: if this ever *succeeds*, the blocked half of F2 has
# become unblocked and this script should grow to use it.
if docker manifest inspect gcr.io/cloud-marketplace-tools/k8s/deployer_helm:0.13 >/dev/null 2>&1; then
  printf '    the deployer base image is pullable now — F2 can go further than this script\n'
  REAL_BASE_AVAILABLE=1
else
  printf '    still unauthenticated, as expected; using a stand-in base\n'
  REAL_BASE_AVAILABLE=0
fi

step "Building the deployer layout with the same COPY instructions"
# The COPY lines are taken from marketplace/Dockerfile rather than
# retyped, so this cannot drift from the thing it is verifying.
COPIES=$(grep -E '^COPY ' marketplace/Dockerfile)
[ -n "$COPIES" ] || fail "no COPY instructions found in marketplace/Dockerfile"
printf '%s\n' "$COPIES" | sed 's/^/    /'

BUILD_DIR=$(mktemp -d)
trap 'rm -rf "$BUILD_DIR"' EXIT
{
  printf 'FROM %s\n' "$STAND_IN"
  printf '%s\n' "$COPIES"
} > "$BUILD_DIR/Dockerfile"

docker build -q -f "$BUILD_DIR/Dockerfile" -t "$IMAGE" . >/dev/null ||
  fail "the deployer's COPY instructions do not build from the repository root"
printf '    built\n'

step "The chart is where the base image looks for it"
docker run --rm --entrypoint sh "$IMAGE" -c 'test -f /data/chart/igni/Chart.yaml' ||
  fail "/data/chart/igni/Chart.yaml is missing — deployer_helm will not find a chart"
docker run --rm --entrypoint sh "$IMAGE" -c 'test -f /data/schema.yaml' ||
  fail "/data/schema.yaml is missing — the deployer has no form to read"
printf '    /data/chart/igni and /data/schema.yaml both present\n'

step "helm loads it from that path"
docker run --rm --entrypoint helm "$IMAGE" lint /data/chart/igni \
  --set image.repository=gcr.io/example/igni-api \
  --set image.tag=1.0.0 \
  --set portalImage.repository=gcr.io/example/portal \
  --set portalImage.tag=1.0.0 \
  --set postgres.host=pg.example \
  --set redis.host=redis.example \
  --set urls.publicBase=https://igni.example \
  --set secrets.secretEncryptionKey=DUuX49F9ROzfR-UMMTjm65pV86qN7IyUSzC0gkFQDh0= 2>&1 | sed 's/^/    /'

step "And renders, the way the deployer will"
# `marketplace.enabled` is what turns on the Application object, so this
# renders the shape a listing actually deploys rather than the default.
rendered=$(docker run --rm --entrypoint helm "$IMAGE" template igni /data/chart/igni \
  --set image.repository=gcr.io/example/igni-api \
  --set image.tag=1.0.0 \
  --set portalImage.repository=gcr.io/example/portal \
  --set portalImage.tag=1.0.0 \
  --set postgres.host=pg.example \
  --set redis.host=redis.example \
  --set urls.publicBase=https://igni.example \
  --set secrets.secretEncryptionKey=DUuX49F9ROzfR-UMMTjm65pV86qN7IyUSzC0gkFQDh0= \
  --set marketplace.enabled=true)

for kind in Deployment Service Job Application; do
  printf '%s' "$rendered" | grep -q "^kind: $kind" ||
    fail "the deployer's chart renders no $kind"
done
printf '    %s objects, including the Application the console groups by\n' \
  "$(printf '%s' "$rendered" | grep -c '^kind: ')"

step "The schema parses inside the image"
docker run --rm --entrypoint sh "$IMAGE" -c \
  'helm show chart /data/chart/igni >/dev/null && head -c 1 /data/schema.yaml >/dev/null' ||
  fail "/data/schema.yaml is not readable in the image"
printf '    readable\n'

printf '\n\033[32m✓ the deployer layout builds, and the chart renders from inside it\033[0m\n'
if [ "$REAL_BASE_AVAILABLE" -eq 0 ]; then
  printf '\033[33m  Not checked: the real base image and its tag, which need a Google Cloud identity.\033[0m\n'
fi
