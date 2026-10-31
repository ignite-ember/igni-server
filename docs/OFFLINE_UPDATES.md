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
release images and handed over by Ignite Ember; there is no public
download point for bundles yet.

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
  sboms/<name>.cdx.json  CycloneDX SBOM per image, plus igni-portal-npm.cdx.json
  charts/igni-<chart version>.tgz
```

Images: `igni-api`, `igni-portal`, `igni-artifacts`, `igni-artifacts-embedder`
and `igni-codeindex`, each tagged with its own version.

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
- your registry stores an image under a digest other than the signed one;
- with `--cosign-key`, an image's signature or SBOM attestation does not
  verify in your registry.

It then prints each image as `registry/name:tag@sha256:…`. Install with the
chart from the kept directory, pointing it at your registry:

```sh
helm upgrade --install igni ./igni-0.6.0/igni-bundle-0.6.0/charts/igni-0.1.0.tgz \
  --set image.repository=registry.example.internal/igni/igni-api \
  --set portalImage.repository=registry.example.internal/igni/igni-portal \
  --set portalImage.tag=0.6.0 ...
```

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

## For Ignite Ember: how a bundle is made

`.github/workflows/cd.yml` on `main`. Each image job pushes its image to
`ghcr.io/ignite-ember/<name>` with `GITHUB_TOKEN`, signs it by digest and
attests its SBOM; `offline-bundle` copies every image from there by
digest, verifies it against the cosign public key, exports it with
`tools/release/igni-bundle export-image`, adds the Helm chart and SBOMs,
signs with `igni-bundle seal`, checks the result with `igni-import
--verify-only`, and uploads `igni-bundle-<version>.tar` and its `.sha256`
as a CI artefact (90 days). Module versions are pinned in
`containers/release/modules.tsv`.

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
`igni-server`, and the cosign ones also on the `prod` environments of
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
