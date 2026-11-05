# Offline updates

Each igni release ships as one signed tarball, `igni-bundle-<version>.tar`.
You carry it into your network, verify it with a public key you already
hold, and import it into your own container registry when it suits you.
Nothing in this process contacts Ignite Ember or the internet.

**Status.** The bundle format, the signing steps in CD and `igni-import` are
built and tested (`tests/test_an_offline_bundle_is_verified_before_import.py`).
The release keys are not: the key ceremony at the end of this document has
not happened, so CD cannot sign yet and no bundle has been produced. Until it
has, Ignite Ember cannot give you the public keys this document asks you to
hold.

## Where images and bundles come from

Release images are published to GitHub Container Registry under the
`ignite-ember` organisation, one name per image, the same name the bundle
and your registry use: `ghcr.io/ignite-ember/igni-api`, `igni-portal`,
`igni-artifacts`, `igni-artifacts-embedder` and `igni-codeindex`. A release
is an unprefixed tag (`0.6.0`, or the commit); tags beginning `dev-` or
`stage-`, and the moving `dev` and `stage`, are Ignite Ember's pre-release
builds and are never put in a bundle. The bundle is built from those
release images and kept beside them as `ghcr.io/ignite-ember/igni-bundle:<version>`
(an OCI artefact holding the tarball and its `.sha256`). Ignite Ember hands
it over, or gives you read access to fetch it yourself on a connected
machine:

```sh
oras pull ghcr.io/ignite-ember/igni-bundle:0.6.0    # igni-bundle-0.6.0.tar and .sha256
```

Where it came from does not matter to `igni-import`: the signature does.

An air-gapped install never contacts GHCR: everything it needs is in the
bundle.

## What is in a bundle

```
igni-bundle-0.6.0/
  MANIFEST            version, commit, and the sha256 of every other file
  MANIFEST.minisig    minisign signature over MANIFEST; the version is in its signed comment
  igni-import         the import tool, covered by the MANIFEST like everything else
  images.tsv          name, tag and digest of each image
  images/<name>/image/   the image, in skopeo's dir layout, byte for byte as signed
  images/<name>/sig/     its cosign signature
  images/<name>/att/     its cosign SBOM attestation
  artefacts.tsv       name, version and SHA256SUMS digest of each release that is not an image
  artefacts/<name>/      that release as it was built: its files, their SBOMs,
                         SHA256SUMS, and SHA256SUMS.sig (cosign, over SHA256SUMS)
  sboms/<name>.cdx.json  CycloneDX SBOM per image, plus igni-portal-npm.cdx.json,
                         and <name>-<file>.cdx.json for each artefact's SBOMs
  charts/igni-<chart version>.tgz
```

Images: `igni-api`, `igni-portal`, `igni-artifacts`, `igni-artifacts-embedder`
and `igni-codeindex`, each tagged with its own version, and the third-party
images an install runs, under their upstream tag: `postgres:17.6-alpine`, for
the chart's backup CronJob. A third-party image is pinned by digest
(`containers/release/third-party.tsv`), copied to
`ghcr.io/ignite-ember/<name>` with that digest unchanged, and signed and
attested with the same cosign release key as the rest, so one key verifies
every image the chart runs.

Artefacts are the modules that ship as files rather than images. Fleet is
one: its gateway, node agent, `cvm` and guest agent are binaries for the KVM
hosts and the guest image. A bundle carries such a release only if it was
signed with the release cosign key when the bundle was made; an artefact's
SHA256SUMS lists every file in its directory, and SHA256SUMS.sig signs
SHA256SUMS. `artefacts.tsv` and `artefacts/` are absent from a bundle that
carries none. Artefacts are not pushed anywhere: `--keep` leaves them in the
kept directory, to install as the module's own documentation says.

Two keys sign a release, and you need the public half of each:

| Key | Signs | Verified by |
|---|---|---|
| minisign release key (`igni-release.pub`) | The bundle's MANIFEST, so every file in it | `igni-import`, or `minisign` by hand |
| cosign release key (`igni-cosign.pub`) | Each image, and its SBOM attestation | `cosign verify` against your registry, or an admission controller |

Obtain both public keys from Ignite Ember once, through a channel other than
the bundle (for example with the contract, or read aloud and compared by
fingerprint). Never trust a key that arrives inside the bundle it is meant to
check.

## Verify and import

You need `tar`, `minisign` and `skopeo` on the import host. All three are in
the Debian, Ubuntu, RHEL (EPEL for minisign) and Alpine package repositories.
`cosign` is optional.

```sh
# Verify only: signature, every hash, nothing pushed
./igni-import --bundle igni-bundle-0.6.0.tar --public-key igni-release.pub \
              --expect-version 0.6.0 --verify-only --keep ./igni-0.6.0

# Import into your registry (log in first: `skopeo login registry.example.internal`)
./igni-import --bundle igni-bundle-0.6.0.tar --public-key igni-release.pub \
              --registry registry.example.internal/igni --expect-version 0.6.0 \
              --cosign-key igni-cosign.pub --keep ./igni-0.6.0
```

`igni-import` refuses, and pushes nothing that failed, if:

- the archive holds an absolute path, a `..`, a link or a device file;
- the MANIFEST signature does not verify with the key you gave;
- the signed version differs from the MANIFEST's, or from `--expect-version`;
- any file is missing, altered, or present but not listed in the MANIFEST;
- an artefact's files do not match its own SHA256SUMS, or its directory holds
  a file SHA256SUMS does not list;
- your registry stores an image under a digest other than the signed one;
- with `--cosign-key`, an artefact's signature over its SHA256SUMS does not
  verify (checked before anything is pushed, and with `--verify-only` too),
  or an image's signature or SBOM attestation does not verify in your
  registry.

It then prints each image as `registry/name:tag@sha256:…`, and each artefact
with its version and what was checked. Install with the
chart from the kept directory, pointing it at your registry:

```sh
helm upgrade --install igni ./igni-0.6.0/igni-bundle-0.6.0/charts/igni-0.1.0.tgz \
  --set image.repository=registry.example.internal/igni/igni-api \
  --set portalImage.repository=registry.example.internal/igni/igni-portal \
  --set portalImage.tag=0.6.0 \
  --set backup.enabled=true --set backup.existingClaim=igni-backups ...
```

Every image the chart names comes from beside `image.repository`. With
backups on, the CronJob runs `registry.example.internal/igni/postgres:17.6-alpine`,
which `igni-import` pushed there with the rest; set `backup.image.repository`
and `backup.image.tag` only to use another Postgres image. Nothing the chart
renders points at Docker Hub or any other public registry.

**Use the `igni-import` you already trust.** The first time, take it from the
bundle only after checking the bundle by hand (below). After that, each
verified bundle carries the next version of the tool.

`--dest-tls-verify false` is passed to skopeo for a plain-HTTP registry.
`--authfile` names a credentials file other than skopeo's default.

## Verify by hand

For a security team that would rather not run our script:

```sh
tar -xf igni-bundle-0.6.0.tar && cd igni-bundle-0.6.0
minisign -V -p igni-release.pub -m MANIFEST     # prints the signed version
grep -v '^#' MANIFEST | sha256sum -c -          # every file
find . -type f ! -name 'MANIFEST*' | sed 's#^\./##' | sort \
  | diff - <(grep -v '^#' MANIFEST | cut -c67- | sort)   # nothing unlisted

# Each artefact, against the cosign release key
cd artefacts/igni-fleet
cosign verify-blob --key igni-cosign.pub --insecure-ignore-tlog=true \
  --signature SHA256SUMS.sig SHA256SUMS
sha256sum -c SHA256SUMS
```

## Verify in your registry

Images keep their digests through the import, so their cosign signatures and
attestations remain valid in your registry. With no transparency log to
consult:

```sh
cosign verify --key igni-cosign.pub --insecure-ignore-tlog=true \
  registry.example.internal/igni/igni-api@sha256:…
cosign verify-attestation --key igni-cosign.pub --insecure-ignore-tlog=true \
  --type cyclonedx registry.example.internal/igni/igni-api@sha256:…
```

`--insecure-ignore-tlog` is correct here: igni's images are signed with a
key pair and deliberately not recorded in the public Rekor log, so there is
no log entry to check. The same key works in an admission controller
(Sigstore policy-controller, Kyverno) to refuse unsigned igni images.

## How long releases are kept

Every release stays available for at least **ten years** from the day it
is published, which is what the Cyber Resilience Act asks of each security
update (Art. 13(9)).

| What | Where | Kept |
|---|---|---|
| The offline bundle and its `.sha256` | `ghcr.io/ignite-ember/igni-bundle:<version>` | written once per version, never overwritten or deleted |
| `igni-api` and `igni-portal` | `ghcr.io/ignite-ember/<name>:<commit>`, and `:<version>` | never deleted; the commit tag pins the exact image, since `:<version>` and `latest` move with later builds of `main` |
| Their signatures and SBOM attestations | beside each image, at `sha256-<digest>.sig` and `.att` | as the image |
| Module images | `ghcr.io/ignite-ember/<name>:<version>`, by their own CD | as the image |
| Release notes and changelog | the git tag `v<version>`, its GitHub Release, `CHANGELOG.md` | as the repository |
| The bundle as a CI artefact | the CD run | 90 days; a convenience copy, not the record |

GitHub Container Registry keeps a package version until somebody deletes
it; there is no expiry to configure. The rule that makes this a policy:
**nobody deletes a release tag or a release bundle**, and any clean-up of
old packages (a retention action, a manual purge) must leave every
unprefixed tag alone. Only `dev-` and `stage-` tags may be cleaned up.

Two things are a person's to do once, after the first bundle is kept:

- set the `igni-bundle` package's visibility to match the module images it
  carries (private unless they are public), and give the people who hand
  bundles over read access;
- decide whether to keep a second copy outside GitHub. Ten years is longer
  than any one provider is a safe assumption; a copy of each
  `igni-bundle:<version>` in object storage with versioning and deletion
  protection would cover it. Not set up: it needs an account.

## For Ignite Ember: how a bundle is made

`.github/workflows/cd.yml` on `main`, in the private development
repository `ignite-ember/igni__server` only. The public mirror
`ignite-ember/igni-server` carries the same workflow but runs only its
tests: a bundle holds the paid module images, and a public repository's CI
artefacts are downloadable by any signed-in GitHub user, so the image
builds, the bundle and its GHCR push are guarded by `github.repository ==
'ignite-ember/igni__server'`. The module packages on GHCR stay private,
readable by `igni__server`'s workflows only (package settings, "Manage
Actions access").

Each image job pushes its image to
`ghcr.io/ignite-ember/<name>` with `GITHUB_TOKEN`, signs it by digest and
attests its SBOM; `offline-bundle` copies every image from there by
digest, verifies it against the cosign public key, exports it with
`tools/release/igni-bundle export-image`, pulls each release pinned in
`containers/release/artefacts.tsv` from GHCR (an OCI artefact, with `oras`)
and stages it with `igni-bundle add-artefact`, which refuses it unless its
cosign signature verifies with the release key and every file matches its
SHA256SUMS, adds the Helm chart and SBOMs, signs with `igni-bundle seal`, checks the result with `igni-import
--verify-only`, pushes `igni-bundle-<version>.tar` and its `.sha256` to
`ghcr.io/ignite-ember/igni-bundle:<version>` with `oras` unless that
version is already kept (above), and uploads both as a CI artefact
(90 days). Module versions are pinned in
`containers/release/modules.tsv`, and releases that are not images in
`containers/release/artefacts.tsv`. Fleet is listed there: its CD signs
its release directory (scripts/release/sign.sh in its repository) and, on
master, pushes it to `ghcr.io/ignite-ember/igni-fleet:<version>` with
`oras push`, once per version and never unsigned. Until Fleet's first
signed push of the pinned version exists, and until that package grants
this repository read access ("Manage Actions access"), the bundle job
fails at `oras pull` rather than ship a bundle without Fleet.

**Dev, stage and prod on one registry.** The three branches used to push
into three Google Cloud projects. On GHCR each image has one name and the
environment is in the tag, the same in all three repositories:

| Branch | GitHub environment | Tags pushed |
|---|---|---|
| `dev` | `dev` | `dev-<sha>`, `dev` |
| `stage` | `stage` | `stage-<sha>`, `stage` |
| `main` | `prod` | `<sha>`, `<version>`, `latest` |

So a release tag only ever comes from `main`, and the bundle copies only
release tags (the commit for igni-api and igni-portal, the pinned version
for the modules). The naming is a convention; the guarantee is the key.
`GITHUB_TOKEN` from any branch can write any tag of a package, and GHCR
has no immutable tags, so what stops a non-release image reaching a
customer is that the release cosign key lives in the `prod` environment
only, whose deployment branch policy allows `main` only, and the bundle
job refuses any image that key did not sign. `dev` and `stage` sign with a
separate, non-release cosign key pair under the same secret names.

Secrets and variables, set on the `prod` GitHub environment of
`igni__server` (not the public mirror), and the cosign ones also on the `prod` environments of
`igni__artifacts` and `igni__code-index`. Every push is signed, so the
`dev` and `stage` environments of all three need the cosign names too,
holding the non-release pair:

| Name | Kind | Content |
|---|---|---|
| `COSIGN_PRIVATE_KEY` | secret | `cosign.key`, encrypted PEM |
| `COSIGN_PASSWORD` | secret | its password |
| `COSIGN_PUBLIC_KEY` | variable | `cosign.pub` |
| `MINISIGN_SECRET_KEY` | secret | the minisign secret key file, password-protected |
| `MINISIGN_PASSWORD` | secret | its password |
| `MINISIGN_PUBLIC_KEY` | variable | the minisign `.pub` file, both lines |

Why minisign for the MANIFEST: it is offline by construction, a small
single binary in every major distribution, the format the desktop updater
already uses, and its signed trusted comment binds the version to the
signature. Cosign stays for images because that is what registries and
admission controllers understand.

**Key generation is a person's job, done once, offline:** `cosign
generate-key-pair` and `minisign -G` on an offline machine, private keys
stored in two places under the company's key-custody policy, public keys
and their fingerprints published to customers out of band. Rotation is the
same ceremony, with the new public key sent before the first release signed
by it.
