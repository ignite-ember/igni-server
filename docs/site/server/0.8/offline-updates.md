Each igni release ships as one signed file, `igni-bundle-<version>.tar`. You carry it into your network, check it with a public key you already hold, and import it into your own container registry when it suits you. Nothing in this process contacts Ignite Ember or the internet. This page summarises how; the full procedure, including how a bundle is made, is `docs/OFFLINE_UPDATES.md` in the server repository.

## Where images and bundles come from

igni's release images are published to GitHub Container Registry, at `ghcr.io/ignite-ember/<name>` under the same names the bundle uses. A release is an unprefixed tag such as `0.8.0`, and the commit it was built from is tagged with that commit's SHA. There are no `dev` or `stage` images: 0.7.0 removed them, because nothing deployed them. The server images (`igni-api`, `igni-portal`) are **public** — anyone can pull them without a registry login — while the offline bundle is **private**, because it carries the paid modules' images. Each bundle is built from those release images, and Ignite Ember hands it to you. An air-gapped install never contacts the registry: everything it needs is in the bundle.

A bundle is also kept beside those images, as a private OCI artefact holding the tarball and its `.sha256`. With authorized access and a route out you can fetch it yourself (log in first, `oras login ghcr.io`); otherwise Ignite Ember hands it to you:

```bash
oras pull ghcr.io/ignite-ember/igni-bundle:0.8.0    # igni-bundle-0.8.0.tar and .sha256
```

Where it came from does not matter to `igni-import`: the signature does.

## What is in a bundle

```
igni-bundle-0.8.0/
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
  sboms/<name>.cdx.json  a CycloneDX SBOM per image, plus one for the portal's npm packages
  charts/igni-<chart version>.tgz
```

The images are `igni-api`, `igni-portal`, `igni-artifacts`, `igni-artifacts-embedder` and `igni-codeindex`, each tagged with its own version, plus the third-party images the chart runs — `postgres:17.6-alpine`, for the backup CronJob — pinned by digest and signed with the same key, so one key verifies every image the chart runs. The CodeIndex image carries what its extension needs on people's machines, including Neo4j, a Java runtime and the embedding model.

**Artefacts** are the releases that ship as files rather than images. Fleet is one: its gateway, node agent and guest agent are binaries for the KVM hosts. A bundle carries an artefact only if it was signed with the release key when the bundle was made; `artefacts.tsv` and `artefacts/` are absent from a bundle that carries none. Artefacts are never pushed anywhere — `--keep` leaves them in the kept directory, to install as the module's own documentation says.

**What a bundle does not carry:**

- **The desktop app and the CLI.** See [Installing igni](/docs/app/installing-igni).
- **Model weights.** Your inference nodes are yours to run. See [Models and the Factory](/docs/core/models-and-the-factory).
- **The licence file.** It comes from Ignite Ember separately. See [Licensing and evaluations](/docs/server/licensing-and-evaluations).

## The one key

One key signs a release. You need its public half, `cosign.pub`.

| Signed with the release key | Checked by |
|---|---|
| The bundle's `MANIFEST`, so every file in it | `igni-import`, or `cosign verify-blob` by hand |
| Each image, and its SBOM attestation | `cosign verify` against your registry, or an admission controller |
| Each artefact's `SHA256SUMS` | `igni-import --cosign-key`, or `cosign verify-blob` by hand |

The version is a line of the `MANIFEST`, so it is covered by the signature: a bundle signed as one release cannot be relabelled as another.

Get the public key from Ignite Ember once, through a channel other than the bundle — with the contract, say, or read aloud and compared by fingerprint. Never trust a key that arrives inside the bundle it is meant to check.

## Verify and import

The import host needs `tar`, `cosign` and `skopeo`. `tar` and `skopeo` are in the Debian, Ubuntu, RHEL and Alpine package repositories. `cosign` is a single binary: take it from Sigstore's releases and check it against the release's checksums, on a connected machine, before carrying it in. It is the one tool that checks both the bundle and the images, and nothing here makes it contact the network.

```bash
# Verify only: the signature and every hash. Nothing is pushed.
./igni-import --bundle igni-bundle-0.8.0.tar --public-key cosign.pub \
              --expect-version 0.8.0 --verify-only --keep ./igni-0.8.0

# Import into your registry. Log in first: skopeo login registry.example.internal
./igni-import --bundle igni-bundle-0.8.0.tar --public-key cosign.pub \
              --registry registry.example.internal/igni --expect-version 0.8.0 \
              --cosign-key cosign.pub --keep ./igni-0.8.0
```

| Flag | What it does |
|---|---|
| `--bundle FILE` | The bundle. Required. |
| `--public-key FILE` | igni's cosign release key, obtained out of band. Required. |
| `--registry URL` | Where to push. Required unless `--verify-only`. |
| `--expect-version V` | Refuse a bundle of any other version. |
| `--verify-only` | Check everything, push nothing. |
| `--keep DIR` | Keep the unpacked bundle (the chart, the SBOMs and the artefacts) here. The directory must not exist yet. |
| `--cosign-key FILE` | After pushing, check each image's signature and SBOM attestation in your registry, and each artefact's signature over its `SHA256SUMS`. |
| `--authfile FILE` | A registry credentials file other than skopeo's default. |
| `--dest-tls-verify false` | For a plain-HTTP registry. |

`igni-import` refuses if:

- the archive holds an absolute path, a `..`, a link or a device file;
- the `MANIFEST` signature does not verify with the key you gave;
- the version in the signed `MANIFEST` differs from `--expect-version`;
- any file is missing, altered, or present but not listed in the `MANIFEST`;
- an artefact's files do not match its own `SHA256SUMS`, or its directory holds a file `SHA256SUMS` does not list;
- your registry stores an image under a digest other than the signed one;
- with `--cosign-key`, an artefact's signature over its `SHA256SUMS` does not verify — checked before anything is pushed, and with `--verify-only` too — or an image's signature or SBOM attestation does not verify in your registry.

A refusal prints `igni-import: REFUSED: <reason>` and exits non-zero. Images are pushed one at a time, so if a later image is refused, the earlier ones are already in your registry. Nothing that failed is pushed.

On success it prints `Verified igni <version>`, then each image as `registry/name:tag@sha256:…`, and each artefact with its version and what was checked.

**Use the `igni-import` you already trust.** The first time, run the copy from the bundle only after checking the bundle by hand, below. After that, each verified bundle carries the next version of the tool.

## Install from the bundle

`igni-import` imports images only. It does not install anything. Install the chart from the kept directory and point it at your registry:

```bash
helm upgrade --install igni ./igni-0.8.0/igni-bundle-0.8.0/charts/igni-0.2.0.tgz \
  --namespace igni \
  --set image.repository=registry.example.internal/igni/igni-api \
  --set portalImage.repository=registry.example.internal/igni/igni-portal \
  --set image.tag=0.8.0 --set portalImage.tag=0.8.0 \
  --reuse-values
```

Every image the chart names comes from beside `image.repository`. The CodeIndex and Artifacts images are deployed from your own manifests; igni's chart does not deploy them.

Before upgrading, take a backup. A `helm rollback` moves the pods back but not the database schema. See [Upgrading, and what a rollback does not undo](/docs/server/self-hosting-the-server).

## Verify by hand

For a security team that would rather not run the script:

```bash
tar -xf igni-bundle-0.8.0.tar && cd igni-bundle-0.8.0
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

Images keep their digests through the import, so their cosign signatures and attestations remain valid in your registry. igni's images and `MANIFEST` are signed with a key and deliberately not recorded in the public Rekor log, so there is no log entry to check — hence `--insecure-ignore-tlog`:

```bash
cosign verify --key cosign.pub --insecure-ignore-tlog=true \
  registry.example.internal/igni/igni-api@sha256:…
cosign verify-attestation --key cosign.pub --insecure-ignore-tlog=true \
  --type cyclonedx registry.example.internal/igni/igni-api@sha256:…
```

The same key works in an admission controller, such as Sigstore policy-controller or Kyverno, to refuse unsigned igni images.

## How long releases are kept

Every release stays available for at least **ten years** from the day it is published, which is what the Cyber Resilience Act asks of each security update.

| What | Where | Kept |
|---|---|---|
| The offline bundle and its `.sha256` | `ghcr.io/ignite-ember/igni-bundle:<version>` | written once per version, never overwritten or deleted |
| `igni-api` | `ghcr.io/ignite-ember/igni-api:<commit>`, `:<version>`, and a moving `:latest` | never deleted; the commit tag pins the exact image |
| `igni-portal` | `ghcr.io/ignite-ember/igni-portal:<commit>`, `:<version>`, and a moving `:latest` | never deleted; the commit tag pins the exact image |
| Their signatures and SBOM attestations | beside each image, at `sha256-<digest>.sig` and `.att` | as the image |
| Module images | `ghcr.io/ignite-ember/<name>:<version>`, by their own CD | as the image |
| Release notes and changelog | the git tag `v<version>`, its GitHub Release, `CHANGELOG.md` | as the repository |

In the bundle and in the registry you import it into, both images are carried under `:<version>`. The bundle re-tags the portal to the version as well, so the tag the chart defaults to resolves inside your own registry.

The rule that makes this a policy: **nobody deletes a release tag or a release bundle**, and any clean-up of old packages must leave every unprefixed tag alone. The `dev` and `stage` tags that used to sit beside them are gone — 0.7.0 removed those images — so there is no pre-release tag of ours left to tidy away.

## What is not finished

- **The server images are public; the bundle is not.** `v0.8.0` is a signed tag on the release commit, and the release workflow signs every image and the bundle it builds. The server images are publicly readable on GitHub Container Registry now — an anonymous `docker pull ghcr.io/ignite-ember/igni-api:0.8.0` works — but the offline bundle (`igni-bundle`) stays private, because it carries the paid modules' images. If you cannot reach it, ask Ignite Ember for the bundle and for the public key.
- **The release is built in private.** The workflow that builds and signs it, `.github/workflows/release.yml`, runs from a private repository, which holds the `release` environment and the signing key. The public repository holds the workflow's logic and its tests; the key is in Cloud KMS and is not exportable.
- **The release key has not been published to customers yet.** Ask Ignite Ember for the fingerprint before your first import.
- **The desktop app updates outside the bundle.** It can be pointed at a release mirror you host (`update_endpoint`), but the bundle does not carry the desktop builds or their update manifest, and only the macOS build is in that manifest today. On an air-gapped machine, the simplest course is to turn update checks off and install new versions by hand. See [Installing igni](/docs/app/installing-igni).

## Where to go next

- **[Self-hosting the igni server](/docs/server/self-hosting-the-server)** — the chart values the install command above sets.
- **[Licensing and evaluations](/docs/server/licensing-and-evaluations)** — the licence file, which the bundle does not carry.
