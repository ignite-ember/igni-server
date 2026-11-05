# Releasing igni server

A release is a version number, a commit on `main` of the private
repository `ignite-ember/igni__server` that CD has built, signed and sealed
into an offline bundle, and a signed git tag `v<version>` on that commit
with release notes. The public mirror `ignite-ember/igni-server` runs CD's
tests only: the bundle carries the paid module images, so it is never
built where a run's artefacts are public. This is the checklist; [OFFLINE_UPDATES.md](OFFLINE_UPDATES.md)
is what CD does with the commit and how a customer verifies the result.

## Before

- The release keys exist and are in the `prod` GitHub environment
  ([OFFLINE_UPDATES.md](OFFLINE_UPDATES.md), the last section). CD refuses
  to push an unsigned image, so without them nothing is released.
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
3. **Merge to `main`.** CD builds and pushes `igni-api` and `igni-portal`
   tagged `<sha>`, `<version>` and `latest`, signs them and attests their
   SBOMs, then seals `igni-bundle-<version>.tar` and keeps it as
   `ghcr.io/ignite-ember/igni-bundle:<version>`. Only the first build of a
   version is kept; later ones say so in the run and are CI artefacts only,
   so bump the version for each release.
4. **Check what CD made.** Fetch the kept bundle (`oras pull
   ghcr.io/ignite-ember/igni-bundle:<version>`) and verify it as a customer
   would, with the public keys you hold and not copies from the run:
   `igni-import --bundle … --public-key igni-release.pub --expect-version
   <version> --verify-only`, and `cosign verify` on both images.
5. **Tag the commit CD built**, signed, and push the tag:

   ```sh
   git tag -s v<version> <commit> -m "igni server <version>"
   git push origin v<version>
   ```

   The commit is the one in the bundle's MANIFEST (`# commit:`).
6. **Publish the GitHub Release** from the tag, with the notes:

   ```sh
   gh release create v<version> --repo ignite-ember/igni-server --verify-tag \
     --title "igni server <version>" --notes-file docs/releases/v<version>.md
   ```

## A security release

The same steps, from a private fork of the advisory when the fix must not be
public before the release ([VULNERABILITY_MANAGEMENT.md](VULNERABILITY_MANAGEMENT.md),
"Advisories and CVE identifiers"). Publish the advisory after the release
is out, and name the fixed version in it.
