# Releasing igni server

A release is a version number, a commit on `main` of this repository
(`ignite-ember/igni-server`), a signed git tag `v<version>` on that
commit with release notes, and the images and offline bundle built, signed
and sealed from it. Development is public, here; releases are built in
private, by `ignite-ember/igni__release`. That repository holds no source:
it calls [`.github/workflows/release.yml`](../.github/workflows/release.yml)
from this repository, and holds the `prod` environment with the release
keys. The bundle carries the paid module images, so it is never built in a
run of this public repository, whose CI artefacts anyone signed in to
GitHub can download (S27). This is the checklist;
[OFFLINE_UPDATES.md](OFFLINE_UPDATES.md) is what the release workflow does
with the commit and how a customer verifies the result.

## Before

- The release keys exist and are in the `prod` GitHub environment of
  `igni__release` ([OFFLINE_UPDATES.md](OFFLINE_UPDATES.md), the last
  section). The release refuses to push an unsigned image, so without them
  nothing is released.
- CI is green on the commit, and so is the latest scheduled
  `vulnerability-scan.yml` run: a release does not ship a known, fixable
  HIGH or CRITICAL.
- `containers/release/modules.tsv` names the module versions this release
  carries, each already signed on GHCR.

## Steps

1. **The version.** Set it in `pyproject.toml`, `app/config.py`
   (`app_version`) and `charts/igni/Chart.yaml` (`appVersion`);
   `tests/test_one_version_number.py` fails if they disagree. Bump the
   chart's own `version` too when the chart changed.
2. **The changelog.** Move what is under `## [Unreleased]` in
   [CHANGELOG.md](../CHANGELOG.md) to `## [<version>] - <date>`, add the
   compare links at the bottom, and write `docs/releases/v<version>.md`:
   what a customer gets, what changed for them, how to upgrade, and the
   advisories it fixes. Security fixes name their GHSA or CVE identifier.
3. **Merge to `main`.** `cd.yml` runs the tests on the merged commit and
   nothing else; no image is built in this repository.
4. **Tag that commit**, signed, once its CD run has passed, and push the
   tag:

   ```sh
   git tag -s v<version> <commit> -m "igni server <version>"
   git push origin v<version>
   ```

5. **Build the release** from the tag, in `igni__release`:

   ```sh
   gh workflow run release.yml -R ignite-ember/igni__release -f ref=v<version>
   ```

   It refuses a ref that is not on `main`, a tag that is not
   `v<version>` of the `pyproject.toml` it points at, and a commit with no
   passing CD run on `main`. Then it builds and pushes `igni-api` and
   `igni-portal` tagged `<sha>`, `<version>` and `latest`, signs them and
   attests their SBOMs, seals `igni-bundle-<version>.tar` and keeps it as
   `ghcr.io/ignite-ember/igni-bundle:<version>`. Only the first build of a
   version is kept; later ones say so in the run and are CI artefacts of
   `igni__release` only, so bump the version for each release.
6. **Check what the release made.** Fetch the kept bundle (`oras pull
   ghcr.io/ignite-ember/igni-bundle:<version>`) and verify it as a
   customer would, with the public key you hold and not a copy from the
   run: `igni-import --bundle … --public-key cosign.pub
   --expect-version <version> --verify-only`, and `cosign verify` on both
   images. The commit in the bundle's MANIFEST (`# commit:`) is the one
   the tag points at.
7. **Publish the GitHub Release** on this repository, from the tag, with
   the notes:

   ```sh
   gh release create v<version> --repo ignite-ember/igni-server --verify-tag \
     --title "igni server <version>" --notes-file docs/releases/v<version>.md
   ```

## A security release

The same steps. The fix is prepared in a private fork of the advisory when
it must not be public before the release
([VULNERABILITY_MANAGEMENT.md](VULNERABILITY_MANAGEMENT.md), "Advisories
and CVE identifiers"), but a release is built from `main` only, so the fix
is public from its merge: have the version, the changelog and the notes in
the same merge, and tag and build as soon as CD passes. Publish the
advisory after the release is out, and name the fixed version in it.
