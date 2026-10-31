#!/usr/bin/env bash
#
# The visual baselines, in the environment that owns them.
#
#   ./scripts/baselines-linux.sh            # compare (what CI does)
#   ./scripts/baselines-linux.sh --update   # regenerate and write them
#
# E4. Playwright names baselines per-platform, and only darwin ones
# were ever committed — so on the one platform CI runs, the visual
# suite had nothing to compare against and was excluded with a
# `--grep-invert` that produced no skipped test. Half of F67 was open on
# exactly this.
#
# **Only the Linux baselines are committed.** Two sets means every
# intentional design change has to regenerate both, and a developer who
# regenerates one leaves CI red for reasons that have nothing to do
# with their change. One set, owned by the environment that gates, and
# this script to work with it from anywhere. On a Mac the visual tests
# declare themselves skipped (`@needs-baseline`) and the run says so
# every time.
#
# The container is the same image CI uses, pinned to the Playwright
# version in package.json, so the renderer and the fonts match. A local
# Linux VM or a different image tag produces baselines that disagree
# with CI's rendering and then fail forever on antialiasing.
#
# The repository is mounted read-only and copied inside, because
# `npm ci` in the container would otherwise replace the host's
# darwin-built node_modules. Only the PNGs come back out.
#
set -euo pipefail

MODE="compare"
if [[ "${1:-}" == "--update" ]]; then
  MODE="update"
elif [[ -n "${1:-}" ]]; then
  echo "usage: $(basename "$0") [--update]" >&2
  exit 2
fi

PORTAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SNAPSHOTS="$PORTAL_DIR/tests/e2e/visual.spec.ts-snapshots"
# Where a failed comparison leaves its diff images, for review.
DIFFS="$PORTAL_DIR/test-results/baseline-diffs"

# Pinned to the version in the *lockfile*, which is what `npm ci`
# installs inside the container — not the range in package.json. Those
# differ: this client's package.json says ^1.61.0 while the lockfile
# resolves 1.61.1, and Playwright refuses to run a browser build that
# does not match its library ("Please update docker image as well").
# The portal's two happened to be equal, which is how a wrong rule
# looks right until it does not.
PLAYWRIGHT_VERSION="$(node -p "require('$PORTAL_DIR/package-lock.json').packages['node_modules/@playwright/test'].version")"
IMAGE="mcr.microsoft.com/playwright:v${PLAYWRIGHT_VERSION}-noble"

mkdir -p "$SNAPSHOTS"
echo "$MODE, in $IMAGE"

if [[ "$MODE" == "update" ]]; then
  INNER='npx playwright test tests/e2e/visual.spec.ts --update-snapshots --reporter=line
    cp tests/e2e/visual.spec.ts-snapshots/*-linux.png /out/'
  MOUNT_OUT=(-v "$SNAPSHOTS":/out)
else
  # Read-only for baselines: a comparison run must not bless itself.
  # But the diffs have to come out, or "look at the diff before you
  # update" — which this file tells you to do — is not possible: they
  # were being written inside the container and thrown away with it.
  INNER='npx playwright test tests/e2e/visual.spec.ts --reporter=line || true
    if [ -d test-results ]; then cp -r test-results/. /diffs/ 2>/dev/null || true; fi
    ls /diffs >/dev/null 2>&1 && find /diffs -name "*-diff.png" | sed "s|/diffs/|diff: |"'
  rm -rf "$DIFFS" && mkdir -p "$DIFFS"
  MOUNT_OUT=(-v "$DIFFS":/diffs)
fi

# --platform: CI runners are x86_64, and the rendering is not
# architecture-independent. The desktop app's 34 baselines,
# generated on an arm64 Mac, failed all 34 when compared under
# x86_64 emulation — while the portal's 32 passed, which is luck
# rather than portability. Pinned so the whole environment is
# stated: image, version, architecture.
docker run --rm --platform linux/amd64 \
  -v "$PORTAL_DIR":/src:ro \
  ${MOUNT_OUT[@]+"${MOUNT_OUT[@]}"} \
  -e CI= \
  "$IMAGE" \
  bash -euc "
    cp -r /src /work
    cd /work
    npm ci --legacy-peer-deps --no-audit --no-fund > /dev/null
    # No backend in here. global-setup says so and carries on; every
    # /v1 call the visual suite makes is replayed from fixtures.
    $INNER
  "

if [[ "$MODE" == "update" ]]; then
  echo
  echo "$(ls -1 "$SNAPSHOTS" | grep -c -- '-linux.png') Linux baseline(s) in $SNAPSHOTS"
  echo "Review the diff before committing: these are what CI compares against."
fi
