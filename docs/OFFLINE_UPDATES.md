# Offline updates

Each igni release ships as one signed tarball, `igni-bundle-<version>.tar`.
You carry it into your network, verify it with a public key you already
hold, and import it into your own container registry when it suits you.
Nothing in this process contacts Ignite Ember or the internet.

**Status.** The bundle format, the release workflow's signing steps and `igni-import` are
built and tested (`tests/test_an_offline_bundle_is_verified_before_import.py`).
The release key exists (2026-11-07, "The release key" below), but no
release has been signed with it yet, so no bundle has been produced.
Ignite Ember gives you its public key, out of band, with the first one.

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
  MANIFEST            format, version, commit, and the sha256 of every other file
  MANIFEST.sig        cosign signature over MANIFEST, with the release key
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

One key signs a release, and you need its public half, `cosign.pub`:

| Signed with the release key | Verified by |
|---|---|
| The bundle's MANIFEST, so every file in it | `igni-import`, or `cosign verify-blob` by hand |
| Each image, and its SBOM attestation | `cosign verify` against your registry, or an admission controller |
| Each artefact's SHA256SUMS | `igni-import --cosign-key`, or `cosign verify-blob` by hand |

The version is a line of the MANIFEST, so it is covered by the signature:
a bundle signed as one release cannot be relabelled as another.

Obtain the public key from Ignite Ember once, through a channel other than
the bundle (for example with the contract, or read aloud and compared by
fingerprint). Never trust a key that arrives inside the bundle it is meant to
check.

## Verify and import

You need `tar`, `cosign` and `skopeo` on the import host. `tar` and `skopeo`
are in the Debian, Ubuntu, RHEL and Alpine package repositories. `cosign` is
a single binary: take it from Sigstore's releases
(`github.com/sigstore/cosign`) and check it against the release's checksums,
on a connected machine, before carrying it in. It is the one tool that
checks both the bundle and the images, and nothing here makes it contact
the network.

```sh
# Verify only: signature, every hash, nothing pushed
./igni-import --bundle igni-bundle-0.6.0.tar --public-key cosign.pub \
              --expect-version 0.6.0 --verify-only --keep ./igni-0.6.0

# Import into your registry (log in first: `skopeo login registry.example.internal`)
./igni-import --bundle igni-bundle-0.6.0.tar --public-key cosign.pub \
              --registry registry.example.internal/igni --expect-version 0.6.0 \
              --cosign-key cosign.pub --keep ./igni-0.6.0
```

`igni-import` refuses, and pushes nothing that failed, if:

- the archive holds an absolute path, a `..`, a link or a device file;
- the MANIFEST signature does not verify with the key you gave;
- the version in the signed MANIFEST differs from `--expect-version`;
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
cosign verify-blob --key cosign.pub --insecure-ignore-tlog=true \
  --signature MANIFEST.sig MANIFEST             # the signature
head -n 4 MANIFEST                              # format, version, commit, date
grep -v '^#' MANIFEST | sha256sum -c -          # every file
find . -type f ! -name 'MANIFEST*' | sed 's#^\./##' | sort \
  | diff - <(grep -v '^#' MANIFEST | cut -c67- | sort)   # nothing unlisted

# Each artefact, against the cosign release key
cd artefacts/igni-fleet
cosign verify-blob --key cosign.pub --insecure-ignore-tlog=true \
  --signature SHA256SUMS.sig SHA256SUMS
sha256sum -c SHA256SUMS
```

## Verify in your registry

Images keep their digests through the import, so their cosign signatures and
attestations remain valid in your registry. With no transparency log to
consult:

```sh
cosign verify --key cosign.pub --insecure-ignore-tlog=true \
  registry.example.internal/igni/igni-api@sha256:…
cosign verify-attestation --key cosign.pub --insecure-ignore-tlog=true \
  --type cyclonedx registry.example.internal/igni/igni-api@sha256:…
```

`--insecure-ignore-tlog` is correct here, and for the bundle: igni's images
and MANIFEST are signed with a key and deliberately not recorded in the
public Rekor log, so there is no log entry to check. The same key works in an admission controller
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
| The bundle as a CI artefact | the release run, in the private `igni__release` | 90 days; a convenience copy, not the record |

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

`.github/workflows/release.yml` in this repository, called from the
private repository `ignite-ember/igni__release` with the tag or commit to
release (docs/RELEASING.md). A bundle holds the paid module images, and a
public repository's CI artefacts are downloadable by any signed-in GitHub
user, so the release runs only in a run that belongs to `igni__release`:
its first job, `resolve`, is guarded by `github.repository ==
'ignite-ember/igni__release'` (in a called workflow that is the caller),
and every other job needs it. `resolve` also refuses a ref that is not on
`main` of this repository, or whose CD run on `main` (the tests) did not
pass, and every job checks out the commit it resolved. This repository's
own `cd.yml` runs the tests on each push to `main` and builds nothing.
`igni__release` holds no source: one caller workflow and the `prod`
environment, which is the only thing allowed to sign with the release key. The module packages on GHCR stay private,
readable by `igni__release`'s workflows only (package settings, "Manage
Actions access"); `igni-api`, `igni-portal` and `igni-bundle` grant it
write access the same way.

Each image job pushes its image to
`ghcr.io/ignite-ember/<name>` with `GITHUB_TOKEN`, signs it by digest and
attests its SBOM; `offline-bundle` copies every image from there by
digest, verifies it against the release public key, exports it with
`tools/release/igni-bundle export-image`, pulls each release pinned in
`containers/release/artefacts.tsv` from GHCR (an OCI artefact, with `oras`)
and stages it with `igni-bundle add-artefact`, which refuses it unless its
cosign signature verifies with the release key and every file matches its
SHA256SUMS, adds the Helm chart and SBOMs, signs the MANIFEST with the
release key in `igni-bundle seal`, checks the result with `igni-import
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
`igni__release` read access ("Manage Actions access"), the bundle job
fails at `oras pull` rather than ship a bundle without Fleet.

**Dev, stage and prod on one registry.** The three branches used to push
into three Google Cloud projects. On GHCR each image has one name and the
environment is in the tag. igni server's images are built for releases
only now, so `igni-api` and `igni-portal` get the `main` row alone, from
`igni__release`; the module repositories still push all three, and the
`dev-` and `stage-` tags pushed earlier remain:

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
customer is that only `igni__release`'s `prod` environment can sign with
the release key, its deployment branch policy allows `main` only (which
runs the release from a commit on this repository's `main`), and the
bundle job refuses any image that key did not sign. The module
repositories sign their own images in their own CD; the bundle takes a
module image only if it carries this key's signature.

### The release key

The release key is a Cloud KMS key in Ignite Ember's Google Cloud project:
ECDSA P-256, held in an HSM, and not exportable. Nobody has a copy of the
private key, so there is none to leak, to keep in two places, or to hand
over when somebody leaves. CD signs through it with GitHub's OIDC token
(Workload Identity Federation): the jobs that sign get `id-token: write`,
and Google accepts the token only when GitHub issued it to a job in
`igni__release`'s `prod` environment. There is no signing secret in
GitHub. cosign names the key as `gcpkms://projects/…/cryptoKeyVersions/N`,
and the same key signs the images, their SBOM attestations and the
bundle's MANIFEST, so a customer holds one public key and needs one tool.

Variables, set on the `prod` GitHub environment of `igni__release` (never
on this public repository, whose workflows use no environment). None is a
secret: each names something, and only the environment's OIDC token can
use it.

| Name | Kind | Content |
|---|---|---|
| `COSIGN_KMS_KEY` | variable | the key, as cosign names it: `gcpkms://projects/…/cryptoKeyVersions/N` |
| `COSIGN_PUBLIC_KEY` | variable | `cosign.pub`, the public key customers hold |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | variable | the workload identity provider that trusts `igni__release`'s tokens |
| `GCP_SIGNER_SERVICE_ACCOUNT` | variable | the service account allowed to sign with the key, and nothing else |

Why cosign for the MANIFEST as well as the images: one key and one tool
for the customer, and a key that never leaves the HSM. cosign is made
offline explicitly: a plain signature, nothing sent to a transparency log,
and `igni-import` verifies with a key file and no network.

`cosign.pub` is exported from KMS (`cosign public-key --key
gcpkms://…`) and published to customers, with its fingerprint, out of
band. Rotation is a new key version: its public key is sent to customers
before the first release signed by it.
