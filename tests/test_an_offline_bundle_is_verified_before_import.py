"""An offline release bundle is imported only if every byte is the one signed.

`tools/release/igni-bundle seal` writes a MANIFEST (the sha256 of every
file, and the version), signs it with minisign, and tars the lot.
`tools/release/igni-import` is what an air-gapped customer runs: it checks
the signature with a public key they hold, checks every hash, and only then
pushes images to their registry. docs/OFFLINE_UPDATES.md is the customer's
description of the same thing.

What these pin is the refusals, because an importer that cannot refuse is
decoration: a changed file, a file added, a file removed, the wrong key, a
signed MANIFEST relabelled as another version, an archive that tries to
write outside its directory, and a registry that stores something other
than what was signed. Each refusal is asserted to have pushed nothing.

Runs with no network and no registry. `skopeo` is replaced by a stub that
records its arguments and reports, as the real one does with
`--preserve-digests`, the sha256 of the manifest it was given. The real
tools — `minisign`, `tar`, `sha256sum` — are the real ones.
"""

from __future__ import annotations

import hashlib
import io
import os
import pathlib
import re
import shutil
import subprocess
import tarfile

from pydantic import BaseModel
import pytest
import yaml

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_BUNDLE = _ROOT / 'tools/release/igni-bundle'
_IMPORT = _ROOT / 'tools/release/igni-import'
_RELEASE = _ROOT / '.github/workflows/release.yml'
_CD = _ROOT / '.github/workflows/cd.yml'

_VERSION = '0.6.0'
_PASSWORD = 'correct horse battery staple'
_REGISTRY = 'registry.example.internal/igni'

# The stub records one line per call and answers `--digestfile` with the
# sha256 of the source's manifest.json — what skopeo reports for a
# digest-preserving copy. IGNI_TEST_PUSHED_DIGEST overrides it, to play a
# registry that stored something else.
_SKOPEO_STUB = r"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$IGNI_TEST_SKOPEO_LOG"
digestfile=''
source=''
while [ $# -gt 0 ]; do
    case "$1" in
        --digestfile) digestfile="$2"; shift 2 ;;
        dir:*) source="${1#dir:}"; shift ;;
        *) shift ;;
    esac
done
if [ -n "$digestfile" ]; then
    if [ -n "${IGNI_TEST_PUSHED_DIGEST:-}" ]; then
        printf '%s' "$IGNI_TEST_PUSHED_DIGEST" > "$digestfile"
    else
        if command -v sha256sum > /dev/null; then
            hex="$(sha256sum "$source/manifest.json" | cut -d' ' -f1)"
        else
            hex="$(shasum -a 256 "$source/manifest.json" | cut -d' ' -f1)"
        fi
        printf 'sha256:%s' "$hex" > "$digestfile"
    fi
fi
"""

if shutil.which('minisign') is None:
    # A skip reads as a pass, so CI — which installs minisign for this —
    # fails instead.
    if os.environ.get('CI'):
        raise RuntimeError('minisign is not installed; the offline-bundle tests cannot run')
    pytest.skip('minisign is not installed', allow_module_level=True)


class KeyPair(BaseModel):
    public: pathlib.Path
    secret: pathlib.Path


class Sealed(BaseModel):
    """A bundle as the release produces it, and what is needed to import it."""

    tarball: pathlib.Path
    staging: pathlib.Path
    key: KeyPair
    other_key: KeyPair
    skopeo_log: pathlib.Path
    env: dict[str, str]
    digests: dict[str, str]


def _keypair(directory: pathlib.Path, *, name: str, password: str | None = None) -> KeyPair:
    """Without a password unless asked: each use of a protected key costs a
    deliberately slow key derivation, which across this module was most of
    its two minutes. One test below covers the password path."""
    pair = KeyPair(public=directory / f'{name}.pub', secret=directory / f'{name}.key')
    argv = ['minisign', '-G', '-p', str(pair.public), '-s', str(pair.secret)]
    subprocess.run(
        argv if password else [*argv, '-W'],
        input=f'{password}\n{password}\n' if password else '',
        text=True,
        check=True,
        capture_output=True,
    )
    return pair


@pytest.fixture(scope='module')
def keys(tmp_path_factory: pytest.TempPathFactory) -> tuple[KeyPair, KeyPair]:
    directory = tmp_path_factory.mktemp('keys')
    return _keypair(directory, name='release'), _keypair(directory, name='impostor')


def _image(staging: pathlib.Path, *, name: str, with_signatures: bool) -> str:
    """One image in skopeo's `dir:` layout; returns its manifest digest."""
    image = staging / 'images' / name / 'image'
    image.mkdir(parents=True)
    manifest = f'{{"schemaVersion":2,"name":"{name}"}}'.encode()
    (image / 'manifest.json').write_bytes(manifest)
    (image / 'version').write_text('Directory Transport Version: 1.1\n')
    (image / ('a' * 64)).write_bytes(os.urandom(4096))
    if with_signatures:
        for kind in ('sig', 'att'):
            extra = staging / 'images' / name / kind
            extra.mkdir()
            (extra / 'manifest.json').write_text(f'{{"kind":"{kind}"}}')
    (staging / 'sboms').mkdir(exist_ok=True)
    (staging / 'sboms' / f'{name}.cdx.json').write_text('{"bomFormat":"CycloneDX","specVersion":"1.5"}')
    return 'sha256:' + hashlib.sha256(manifest).hexdigest()


def _run(argv: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, env=env, text=True, capture_output=True, check=False)


def _import(sealed: Sealed, *extra: str, tarball: pathlib.Path | None = None) -> subprocess.CompletedProcess[str]:
    return _run(
        [str(_IMPORT), '--bundle', str(tarball or sealed.tarball), '--public-key', str(sealed.key.public), *extra],
        env=sealed.env,
    )


def _pushes(sealed: Sealed) -> list[str]:
    if not sealed.skopeo_log.exists():
        return []
    return sealed.skopeo_log.read_text().splitlines()


def _rewrite(tarball: pathlib.Path, out: pathlib.Path, *, edit) -> pathlib.Path:
    """Unpack, let `edit` change the tree, pack again — a bundle tampered
    with after it was signed."""
    tree = out / 'tree'
    with tarfile.open(tarball) as archive:
        archive.extractall(tree, filter='data')
    (top,) = tree.iterdir()
    edit(top)
    rewritten = out / 'rewritten.tar'
    with tarfile.open(rewritten, 'w') as archive:
        archive.add(top, arcname=top.name)
    return rewritten


@pytest.fixture
def sealed(tmp_path: pathlib.Path, keys: tuple[KeyPair, KeyPair]) -> Sealed:
    key, other = keys

    staging = tmp_path / f'igni-bundle-{_VERSION}'
    staging.mkdir()
    digests = {
        'igni-api': _image(staging, name='igni-api', with_signatures=True),
        'igni-portal': _image(staging, name='igni-portal', with_signatures=False),
    }
    (staging / 'images.tsv').write_text(''.join(f'{name}\t{_VERSION}\t{d}\n' for name, d in digests.items()))
    (staging / 'charts').mkdir()
    (staging / 'charts' / 'igni-0.1.0.tgz').write_bytes(os.urandom(512))

    stub_bin = tmp_path / 'bin'
    stub_bin.mkdir()
    (stub_bin / 'skopeo').write_text(_SKOPEO_STUB)
    (stub_bin / 'skopeo').chmod(0o755)
    env = {
        **os.environ,
        'PATH': f'{stub_bin}{os.pathsep}{os.environ["PATH"]}',
        'IGNI_TEST_SKOPEO_LOG': str(tmp_path / 'skopeo.log'),
        'TMPDIR': str(tmp_path),
    }

    result = _run(
        [
            str(_BUNDLE),
            'seal',
            '--staging',
            str(staging),
            '--version',
            _VERSION,
            '--commit',
            'f043313',
            '--key',
            str(key.secret),
            '--public-key',
            str(key.public),
            '--out',
            str(tmp_path / 'out'),
        ],
        env=env,
    )
    assert result.returncode == 0, result.stderr
    tarball = pathlib.Path(result.stdout.strip())
    assert tarball == tmp_path / 'out' / f'igni-bundle-{_VERSION}.tar'

    return Sealed(
        tarball=tarball,
        staging=staging,
        key=key,
        other_key=other,
        skopeo_log=tmp_path / 'skopeo.log',
        env=env,
        digests=digests,
    )


class TestAGoodBundle:
    def test_it_verifies(self, sealed: Sealed):
        result = _import(sealed, '--verify-only')

        assert result.returncode == 0, result.stderr
        assert f'Verified igni {_VERSION} (commit f043313' in result.stdout
        assert f'igni-api:{_VERSION}@{sealed.digests["igni-api"]}' in result.stdout
        assert 'nothing imported' in result.stdout
        assert _pushes(sealed) == []

    def test_it_imports_by_digest_with_its_signatures(self, sealed: Sealed):
        result = _import(sealed, '--registry', _REGISTRY + '/', '--expect-version', _VERSION)

        assert result.returncode == 0, result.stderr
        api = sealed.digests['igni-api']
        portal = sealed.digests['igni-portal']
        pushes = _pushes(sealed)
        destinations = [line.rsplit(' ', 1)[1] for line in pushes]
        # The signature and attestation go where cosign looks for them:
        # `sha256-<hex>.sig` beside the image, in the same repository.
        assert destinations == [
            f'docker://{_REGISTRY}/igni-api:{_VERSION}',
            f'docker://{_REGISTRY}/igni-api:{api.replace(":", "-")}.sig',
            f'docker://{_REGISTRY}/igni-api:{api.replace(":", "-")}.att',
            f'docker://{_REGISTRY}/igni-portal:{_VERSION}',
        ]
        assert all(line.startswith('copy --all --preserve-digests ') for line in pushes), pushes
        assert f'{_REGISTRY}/igni-api:{_VERSION}@{api} (sig att)' in result.stdout
        assert f'{_REGISTRY}/igni-portal:{_VERSION}@{portal}' in result.stdout
        assert 'Helm chart: charts/igni-0.1.0.tgz' in result.stdout

    def test_keep_leaves_the_verified_chart_and_sboms(self, sealed: Sealed, tmp_path: pathlib.Path):
        kept = tmp_path / 'kept'
        result = _import(sealed, '--verify-only', '--keep', str(kept))

        assert result.returncode == 0, result.stderr
        assert (kept / f'igni-bundle-{_VERSION}' / 'charts' / 'igni-0.1.0.tgz').is_file()
        assert (kept / f'igni-bundle-{_VERSION}' / 'sboms' / 'igni-api.cdx.json').is_file()

    def test_the_bundle_carries_the_importer_under_its_signature(self, sealed: Sealed):
        """So the customer's next importer arrives verified by this one."""
        manifest = (sealed.staging / 'MANIFEST').read_text()
        shipped = sealed.staging / 'igni-import'

        assert shipped.read_bytes() == _IMPORT.read_bytes()
        assert f'{hashlib.sha256(shipped.read_bytes()).hexdigest()}  igni-import' in manifest

    def test_the_manifest_can_be_checked_by_hand(self, sealed: Sealed):
        """The documented manual check, for a customer who wants to verify
        with nothing of ours: `minisign -V` and then `sha256sum -c`."""
        manifest = (sealed.staging / 'MANIFEST').read_text().splitlines()
        header = [line for line in manifest if line.startswith('#')]
        body = [line for line in manifest if not line.startswith('#')]

        assert f'# version: {_VERSION}' in header
        assert body and all(re.fullmatch(r'[0-9a-f]{64}  \S+', line) for line in body), body
        for line in body:
            digest, path = line.split('  ')
            assert hashlib.sha256((sealed.staging / path).read_bytes()).hexdigest() == digest, path

        verified = subprocess.run(
            [
                'minisign',
                '-V',
                '-p',
                str(sealed.key.public),
                '-m',
                str(sealed.staging / 'MANIFEST'),
                '-x',
                str(sealed.staging / 'MANIFEST.minisig'),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        assert verified.returncode == 0, verified.stderr
        assert f'Trusted comment: igni-bundle format=1 version={_VERSION}' in verified.stdout


class TestTheTarball:
    def test_it_holds_one_directory_and_nothing_hidden(self, sealed: Sealed):
        """macOS's tar adds a `._name` entry per file with extended
        attributes unless told not to. GNU tar on a customer's host unpacks
        those as files, which the importer then rightly refuses."""
        with tarfile.open(sealed.tarball) as archive:
            names = archive.getnames()

        assert names
        assert all(n == f'igni-bundle-{_VERSION}' or n.startswith(f'igni-bundle-{_VERSION}/') for n in names), names
        assert not [n for n in names if pathlib.PurePosixPath(n).name.startswith('._')]

    def test_a_password_protected_key_signs_with_minisign_password(self, sealed: Sealed, tmp_path: pathlib.Path):
        """How the release signs: the secret key is encrypted and its password is a
        second secret, read from the environment rather than a terminal."""
        protected = _keypair(tmp_path, name='protected', password=_PASSWORD)
        argv = [
            str(_BUNDLE),
            'seal',
            '--staging',
            str(sealed.staging),
            '--version',
            _VERSION,
            '--key',
            str(protected.secret),
            '--public-key',
            str(protected.public),
            '--out',
            str(tmp_path / 'protected'),
        ]

        without = _run(argv, env=sealed.env)
        signed = _run(argv, env={**sealed.env, 'MINISIGN_PASSWORD': _PASSWORD})

        assert without.returncode == 1
        assert 'could not sign the MANIFEST' in without.stderr
        assert signed.returncode == 0, signed.stderr
        verified = _run(
            [str(_IMPORT), '--bundle', signed.stdout.strip(), '--public-key', str(protected.public), '--verify-only'],
            env=sealed.env,
        )
        assert verified.returncode == 0, verified.stderr


class TestATamperedBundleIsRefused:
    def test_a_changed_file(self, sealed: Sealed, tmp_path: pathlib.Path):
        def edit(top: pathlib.Path) -> None:
            blob = top / 'images' / 'igni-api' / 'image' / ('a' * 64)
            blob.write_bytes(b'\x00' + blob.read_bytes()[1:])

        result = _import(sealed, '--registry', _REGISTRY, tarball=_rewrite(sealed.tarball, tmp_path, edit=edit))

        assert result.returncode == 1
        assert result.stderr.strip() == f'igni-import: REFUSED: hash mismatch: images/igni-api/image/{"a" * 64}'
        assert _pushes(sealed) == []

    def test_a_changed_file_with_the_manifest_rewritten_to_match(self, sealed: Sealed, tmp_path: pathlib.Path):
        """The hashes then agree; only the signature can catch it."""

        def edit(top: pathlib.Path) -> None:
            sbom = top / 'sboms' / 'igni-api.cdx.json'
            old = hashlib.sha256(sbom.read_bytes()).hexdigest()
            sbom.write_text('{"bomFormat":"CycloneDX","components":[]}')
            new = hashlib.sha256(sbom.read_bytes()).hexdigest()
            manifest = top / 'MANIFEST'
            manifest.write_text(manifest.read_text().replace(old, new))

        result = _import(sealed, '--registry', _REGISTRY, tarball=_rewrite(sealed.tarball, tmp_path, edit=edit))

        assert result.returncode == 1
        assert 'MANIFEST signature does not verify' in result.stderr
        assert _pushes(sealed) == []

    def test_a_file_added(self, sealed: Sealed, tmp_path: pathlib.Path):
        def edit(top: pathlib.Path) -> None:
            (top / 'images' / 'igni-api' / 'image' / 'extra').write_text('unsigned')

        result = _import(sealed, '--registry', _REGISTRY, tarball=_rewrite(sealed.tarball, tmp_path, edit=edit))

        assert result.returncode == 1
        assert 'files in the bundle that the MANIFEST does not list: images/igni-api/image/extra' in result.stderr
        assert _pushes(sealed) == []

    def test_a_file_removed(self, sealed: Sealed, tmp_path: pathlib.Path):
        def edit(top: pathlib.Path) -> None:
            (top / 'charts' / 'igni-0.1.0.tgz').unlink()

        result = _import(sealed, '--registry', _REGISTRY, tarball=_rewrite(sealed.tarball, tmp_path, edit=edit))

        assert result.returncode == 1
        assert 'files the MANIFEST lists that are not in the bundle: charts/igni-0.1.0.tgz' in result.stderr
        assert _pushes(sealed) == []

    def test_the_signature_removed(self, sealed: Sealed, tmp_path: pathlib.Path):
        def edit(top: pathlib.Path) -> None:
            (top / 'MANIFEST.minisig').unlink()

        result = _import(sealed, '--verify-only', tarball=_rewrite(sealed.tarball, tmp_path, edit=edit))

        assert result.returncode == 1
        assert result.stderr.strip() == 'igni-import: REFUSED: no MANIFEST.minisig in the bundle'

    def test_keep_does_not_leave_a_refused_bundle_behind(self, sealed: Sealed, tmp_path: pathlib.Path):
        """A directory of unverified files named like a verified one is how
        somebody installs the chart from it anyway."""

        def edit(top: pathlib.Path) -> None:
            (top / 'charts' / 'igni-0.1.0.tgz').write_bytes(b'not the chart')

        kept = tmp_path / 'kept'
        result = _import(
            sealed, '--verify-only', '--keep', str(kept), tarball=_rewrite(sealed.tarball, tmp_path, edit=edit)
        )

        assert result.returncode == 1
        assert not kept.exists()


class TestTheWrongKeyOrVersionIsRefused:
    def test_a_different_public_key(self, sealed: Sealed):
        result = _run(
            [
                str(_IMPORT),
                '--bundle',
                str(sealed.tarball),
                '--public-key',
                str(sealed.other_key.public),
                '--registry',
                _REGISTRY,
            ],
            env=sealed.env,
        )

        assert result.returncode == 1
        assert result.stderr.strip() == (
            'igni-import: REFUSED: the MANIFEST signature does not verify with the supplied public key'
        )
        assert _pushes(sealed) == []

    def test_a_bundle_signed_by_somebody_else(self, sealed: Sealed, tmp_path: pathlib.Path):
        """The impostor re-signs a correct MANIFEST with their own key."""

        def edit(top: pathlib.Path) -> None:
            (top / 'MANIFEST.minisig').unlink()
            subprocess.run(
                [
                    'minisign',
                    '-S',
                    '-s',
                    str(sealed.other_key.secret),
                    '-m',
                    str(top / 'MANIFEST'),
                    '-t',
                    f'igni-bundle format=1 version={_VERSION}',
                ],
                input='',
                text=True,
                check=True,
                capture_output=True,
            )

        result = _import(sealed, '--registry', _REGISTRY, tarball=_rewrite(sealed.tarball, tmp_path, edit=edit))

        assert result.returncode == 1
        assert 'MANIFEST signature does not verify' in result.stderr
        assert _pushes(sealed) == []

    def test_an_unexpected_version(self, sealed: Sealed):
        result = _import(sealed, '--registry', _REGISTRY, '--expect-version', '0.7.0')

        assert result.returncode == 1
        assert result.stderr.strip() == (
            f'igni-import: REFUSED: this bundle is version {_VERSION}, not the expected 0.7.0'
        )
        assert _pushes(sealed) == []

    def test_a_signed_version_that_disagrees_with_the_manifest(self, sealed: Sealed, tmp_path: pathlib.Path):
        """The version is inside the signature, not only in the MANIFEST
        header: a MANIFEST signed as one release cannot be presented as
        another by anybody without the key — and nor can the release key
        be used to sign a mislabelled one by accident."""

        def edit(top: pathlib.Path) -> None:
            manifest = top / 'MANIFEST'
            manifest.write_text(manifest.read_text().replace(f'# version: {_VERSION}', '# version: 9.9.9'))
            (top / 'MANIFEST.minisig').unlink()
            subprocess.run(
                [
                    'minisign',
                    '-S',
                    '-s',
                    str(sealed.key.secret),
                    '-m',
                    str(manifest),
                    '-t',
                    f'igni-bundle format=1 version={_VERSION}',
                ],
                input='',
                text=True,
                check=True,
                capture_output=True,
            )

        result = _import(sealed, '--verify-only', tarball=_rewrite(sealed.tarball, tmp_path, edit=edit))

        assert result.returncode == 1
        assert 'does not match the MANIFEST (version 9.9.9)' in result.stderr


class TestAHostileArchiveWritesNothingOutside:
    def _hostile(self, sealed: Sealed, tmp_path: pathlib.Path, member: tarfile.TarInfo) -> pathlib.Path:
        path = tmp_path / 'hostile.tar'
        with tarfile.open(sealed.tarball) as good, tarfile.open(path, 'w') as bad:
            for info in good.getmembers():
                bad.addfile(info, good.extractfile(info) if info.isfile() else None)
            payload = b'owned'
            member.size = len(payload) if member.isfile() else 0
            bad.addfile(member, io.BytesIO(payload) if member.isfile() else None)
        return path

    def test_a_path_that_climbs_out(self, sealed: Sealed, tmp_path: pathlib.Path):
        target = tmp_path / 'escaped'
        member = tarfile.TarInfo(f'igni-bundle-{_VERSION}/../../escaped')

        result = _import(sealed, '--verify-only', tarball=self._hostile(sealed, tmp_path, member))

        assert result.returncode == 1
        assert 'climbs out of the bundle' in result.stderr
        assert not target.exists()

    def test_an_absolute_path(self, sealed: Sealed, tmp_path: pathlib.Path):
        member = tarfile.TarInfo(str(tmp_path / 'absolute'))

        result = _import(sealed, '--verify-only', tarball=self._hostile(sealed, tmp_path, member))

        assert result.returncode == 1
        assert 'is an absolute path' in result.stderr
        assert not (tmp_path / 'absolute').exists()

    def test_a_symlink(self, sealed: Sealed, tmp_path: pathlib.Path):
        member = tarfile.TarInfo(f'igni-bundle-{_VERSION}/charts/link')
        member.type = tarfile.SYMTYPE
        member.linkname = '/etc'

        result = _import(sealed, '--verify-only', tarball=self._hostile(sealed, tmp_path, member))

        assert result.returncode == 1
        assert 'is not a plain file or directory' in result.stderr


class TestTheRegistryMustStoreWhatWasSigned:
    def test_a_different_digest_is_refused(self, sealed: Sealed):
        """A registry or proxy that rewrites the manifest leaves an image
        whose cosign signature no longer applies, so it is not an import."""
        sealed.env['IGNI_TEST_PUSHED_DIGEST'] = 'sha256:' + 'f' * 64

        result = _import(sealed, '--registry', _REGISTRY)

        assert result.returncode == 1
        assert result.stderr.strip() == (
            f'igni-import: REFUSED: {_REGISTRY}/igni-api:{_VERSION} was stored as '
            f"'sha256:{'f' * 64}', not the signed {sealed.digests['igni-api']}"
        )
        assert len(_pushes(sealed)) == 1


class TestSealRefuses:
    def test_a_public_key_that_is_not_the_pair(self, sealed: Sealed, tmp_path: pathlib.Path):
        result = _run(
            [
                str(_BUNDLE),
                'seal',
                '--staging',
                str(sealed.staging),
                '--version',
                _VERSION,
                '--key',
                str(sealed.key.secret),
                '--public-key',
                str(sealed.other_key.public),
                '--out',
                str(tmp_path / 'again'),
            ],
            env=sealed.env,
        )

        assert result.returncode == 1
        assert 'the key pair does not match' in result.stderr
        assert not (tmp_path / 'again').exists()

    def test_a_link_in_the_staging_directory(self, sealed: Sealed, tmp_path: pathlib.Path):
        (sealed.staging / 'charts' / 'link').symlink_to('/etc/hosts')

        result = _run(
            [
                str(_BUNDLE),
                'seal',
                '--staging',
                str(sealed.staging),
                '--version',
                _VERSION,
                '--key',
                str(sealed.key.secret),
                '--out',
                str(tmp_path / 'again'),
            ],
            env=sealed.env,
        )

        assert result.returncode == 1
        assert 'holds links or special files' in result.stderr


# A registry for `export-image`: `inspect --raw` answers a multi-platform
# image index byte for byte, `inspect --format {{.Digest}}` answers what
# real skopeo does on a host whose platform the index lists (that entry's
# digest, not the index's), and on a Mac, where it lists none, fails.
# `copy` writes the `dir:` layout. No signatures (.sig, .att) exist.
_EXPORT_INDEX = (
    '{"schemaVersion":2,"mediaType":"application/vnd.oci.image.index.v1+json","manifests":['
    '{"mediaType":"application/vnd.oci.image.manifest.v1+json","size":1,'
    '"digest":"sha256:' + '1' * 64 + '","platform":{"architecture":"amd64","os":"linux"}}]}'
)
_EXPORT_SKOPEO_STUB = r"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$IGNI_TEST_SKOPEO_LOG"
case "$1 $2" in
    'inspect --raw')
        case "$3" in *.sig | *.att) exit 1 ;; esac
        printf '%s' "$IGNI_TEST_INDEX"
        ;;
    'inspect --format')
        [ "${IGNI_TEST_HOST_OS:-linux}" = linux ] || { echo 'no image found in image index for darwin' >&2; exit 1; }
        printf 'sha256:%s\n' "$(printf '1%.0s' $(seq 64))"
        ;;
    copy*)
        dest="${*: -1}"
        mkdir -p "${dest#dir:}"
        printf '%s' "$IGNI_TEST_INDEX" > "${dest#dir:}/manifest.json"
        ;;
esac
"""


class TestExportImageNamesTheDigestTheTagNames:
    """S41, ported from the air-gap rehearsal's fix (27c2026e on
    feat/organisations-are-created-not-given). `export-image` took the
    digest from `skopeo inspect --format {{.Digest}}`, which resolves an
    image index to the entry for the host's own platform: on Linux, a
    digest that is not the tag's, and on a Mac, which no index lists, a
    failure. It hashes the raw manifest now."""

    def _export(self, tmp_path: pathlib.Path, *, host_os: str) -> subprocess.CompletedProcess[str]:
        stub_bin = tmp_path / 'bin'
        stub_bin.mkdir()
        (stub_bin / 'skopeo').write_text(_EXPORT_SKOPEO_STUB)
        (stub_bin / 'skopeo').chmod(0o755)
        env = {
            **os.environ,
            'PATH': f'{stub_bin}{os.pathsep}{os.environ["PATH"]}',
            'IGNI_TEST_SKOPEO_LOG': str(tmp_path / 'skopeo.log'),
            'IGNI_TEST_INDEX': _EXPORT_INDEX,
            'IGNI_TEST_HOST_OS': host_os,
            'TMPDIR': str(tmp_path),
        }
        return _run(
            [
                str(_BUNDLE),
                'export-image',
                '--staging',
                str(tmp_path / 'staging'),
                '--name',
                'igni-api',
                '--repository',
                'ghcr.io/ignite-ember/igni-api',
                '--source-tag',
                _VERSION,
                '--tag',
                _VERSION,
            ],
            env=env,
        )

    @pytest.mark.parametrize('host_os', ['linux', 'darwin'])
    def test_it_records_the_index_digest_on_any_host(self, tmp_path: pathlib.Path, host_os: str):
        result = self._export(tmp_path, host_os=host_os)

        assert result.returncode == 0, result.stderr
        index_digest = 'sha256:' + hashlib.sha256(_EXPORT_INDEX.encode()).hexdigest()
        assert (tmp_path / 'staging/images.tsv').read_text() == f'igni-api\t{_VERSION}\t{index_digest}\n'
        copies = [line for line in (tmp_path / 'skopeo.log').read_text().splitlines() if line.startswith('copy')]
        assert copies and f'docker://ghcr.io/ignite-ember/igni-api@{index_digest}' in copies[0], copies

    def test_it_never_asks_for_the_host_platforms_entry(self, tmp_path: pathlib.Path):
        self._export(tmp_path, host_os='linux')

        assert '--format' not in (tmp_path / 'skopeo.log').read_text()


def containers_release_names() -> list[str]:
    """The names pinned in containers/release/{modules,artefacts}.tsv."""
    names = []
    for listing in ('modules.tsv', 'artefacts.tsv'):
        for line in (_ROOT / 'containers/release' / listing).read_text().splitlines():
            if line.strip() and not line.startswith('#'):
                names.append(line.split()[0])
    return names


class TestTheReleaseSignsWhatItShips:
    """The workflow half. Air-gapped customers verify with a public key and
    no network, so signing must be key-based and must not depend on a
    transparency log they cannot reach."""

    def _release(self) -> dict:
        return yaml.safe_load(_RELEASE.read_text())

    def _runs(self, job: dict) -> str:
        return '\n'.join(str(step.get('run') or '') for step in job.get('steps') or [])

    def test_every_job_that_pushes_an_image_signs_and_attests_it_with_the_key(self):
        release = self._release()
        pushing = {name: job for name, job in release['jobs'].items() if 'docker push' in self._runs(job)}

        # A `skopeo copy` into GHCR is a push too: the third-party mirror.
        pushing.update(
            {
                name: job
                for name, job in release['jobs'].items()
                if re.search(r'skopeo copy [^\n]*"docker://\$\{REGISTRY\}/', self._runs(job))
            }
        )

        assert pushing, 'no job in release.yml pushes an image; this test is stale'
        assert 'offline-bundle' in pushing, 'the third-party mirror is no longer seen as a push'
        for name, job in pushing.items():
            script = self._runs(job)
            assert re.search(r'cosign sign .*--key env://COSIGN_PRIVATE_KEY', script), f'{name} does not sign'
            assert re.search(r'cosign attest .*--type cyclonedx', script), f'{name} attests no SBOM'
            assert '--tlog-upload=false' in script, f'{name} uploads to a public transparency log'

    def test_signing_is_never_keyless(self):
        text = _RELEASE.read_text()

        assert 'COSIGN_EXPERIMENTAL' not in text
        for line in re.findall(r'cosign (?:sign|attest)\b[^\n]*', text):
            assert '--key' in line, line

    def test_a_release_from_main_is_sealed_into_a_bundle(self):
        release = self._release()
        job = release['jobs']['offline-bundle']
        script = self._runs(job)

        assert job.get('environment') == 'prod', 'the bundle is not sealed with the release keys'
        assert 'resolve' in job.get('needs', []), 'the bundle does not wait for the ref to be resolved to main'
        assert 'tools/release/igni-bundle seal' in script
        assert '--public-key' in script, 'the bundle job does not check its own signature'
        assert '--commit "${{ needs.resolve.outputs.sha }}"' in script, 'the MANIFEST does not name the commit built'
        uploads = [s for s in job['steps'] if str(s.get('uses', '')).startswith('actions/upload-artifact')]
        assert uploads and 'igni-bundle-' in str(uploads[0]['with']['path'])

    def test_only_a_commit_on_main_that_passed_the_tests_is_released(self):
        """The ref is a tag or a SHA a person types. A tag on a branch, or a
        commit main never had, would be released unreviewed; one whose
        tests never passed, untested (F74)."""
        release = self._release()
        resolve = release['jobs']['resolve']
        script = self._runs(resolve)

        triggers = release.get('on', release.get(True))  # PyYAML reads `on:` as True
        assert triggers['workflow_call']['inputs']['ref']['required'] is True
        checkout = resolve['steps'][0]
        assert str(checkout['uses']).startswith('actions/checkout@')
        assert checkout['with']['repository'] == 'ignite-ember/igni-server'
        assert checkout['with']['ref'] == '${{ inputs.ref }}'
        assert checkout['with']['fetch-depth'] == 0, 'without the history there is no origin/main to check against'
        assert 'git merge-base --is-ancestor "${sha}" origin/main' in script
        assert 'exit 1' in script
        assert 'pyproject.toml' in script
        assert 'actions/workflows/cd.yml/runs?head_sha=${SHA}&branch=main&event=push&status=success' in script
        assert set(resolve['outputs']) == {'sha', 'version'}

    def test_every_checkout_is_the_resolved_commit_of_the_public_repository(self):
        """In a called workflow a bare checkout is the caller's repository,
        igni__release, which holds no source; and `github.sha` is the
        caller's commit. Every job after `resolve` builds the SHA it
        resolved, never the ref, which can move."""
        release = self._release()
        text = _RELEASE.read_text()

        assert 'github.sha' not in text and 'GITHUB_SHA' not in text, 'the caller\'s commit is named'
        assert 'GITHUB_REPOSITORY' not in text and 'github.repository }}' not in text
        for name, job in release['jobs'].items():
            checkouts = [s for s in job.get('steps') or [] if str(s.get('uses', '')).startswith('actions/checkout@')]
            assert checkouts, f'{name} checks nothing out'
            for step in checkouts:
                assert step['with']['repository'] == 'ignite-ember/igni-server', name
                if name != 'resolve':
                    assert step['with']['ref'] == '${{ needs.resolve.outputs.sha }}', name

    #: The private release repository; the only one that releases (S27).
    #: In a called workflow `github.repository` is the caller's.
    _PRIVATE = "github.repository == 'ignite-ember/igni__release'"

    def test_bundles_and_images_are_made_in_the_private_repository_only(self):
        """The bundle carries the paid module images, and a public
        repository's CI artefacts are downloadable by any signed-in GitHub
        user. release.yml lives in this public repository and is called by
        the private one, so `resolve` names the private repository in its
        condition and every other job needs `resolve`: a fork, or this
        repository, calling it runs nothing (S27)."""
        release = self._release()
        jobs = release['jobs']
        releasing = {
            name: job
            for name, job in jobs.items()
            if any(
                marker in self._runs(job) or marker in str(job.get('steps'))
                for marker in (
                    'docker push',
                    'modules.tsv',
                    'artefacts.tsv',
                    'igni-bundle',
                    'oras ',
                    'upload-artifact',
                )
            )
        }
        assert {'build-backend', 'build-portal', 'offline-bundle'} <= set(releasing)

        triggers = release.get('on', release.get(True))  # PyYAML reads `on:` as True
        assert set(triggers) == {'workflow_call'}, 'release.yml must run only when called'
        guard = str(jobs['resolve'].get('if'))
        assert guard == self._PRIVATE, guard
        for name, job in jobs.items():
            if name == 'resolve':
                continue
            needs = job.get('needs') or []
            needs = [needs] if isinstance(needs, str) else needs
            assert 'resolve' in needs, f'{name} does not need resolve, so it runs wherever release.yml is called'
            # Any condition of its own could only widen where it runs
            # (`always()`, another repository); there is none.
            assert 'if' not in job, f'{name} has a condition of its own: {job.get("if")}'
        for name, job in jobs.items():
            condition = str(job.get('if', ''))
            assert 'igni-server' not in condition and 'igni__server' not in condition, name

    def test_the_public_repositorys_cd_pushes_nothing(self):
        """cd.yml runs in this public repository on every push to main. It
        runs the tests and nothing else: no image, no bundle, no artefact,
        no environment, no secret, and a token that cannot write a
        package."""
        cd = yaml.safe_load(_CD.read_text())
        text = _CD.read_text()

        assert cd['permissions'] == {'contents': 'read'}, cd['permissions']
        assert set(cd['jobs']) == {'test'}, sorted(cd['jobs'])
        assert cd['jobs']['test']['uses'] == './.github/workflows/ci.yml'
        assert 'secrets' not in cd['jobs']['test']
        for marker in (
            'environment:',
            'secrets.',
            'vars.',
            'docker push',
            'upload-artifact',
            'oras ',
            'skopeo',
            'cosign',
            'packages: write',
            'release.yml@',
        ):
            assert marker not in text, f'cd.yml mentions {marker}'
        # PyYAML reads the `on:` key as True.
        triggers = cd.get('on', cd.get(True))
        assert triggers == {'push': {'branches': ['main']}}, triggers

    def test_no_other_workflow_touches_the_paid_images(self):
        for workflow in sorted((_ROOT / '.github/workflows').glob('*.yml')):
            if workflow.name == 'release.yml':
                continue
            text = workflow.read_text()
            for marker in ('modules.tsv', 'artefacts.tsv', 'igni-bundle', 'export-image'):
                assert marker not in text, f'{workflow.name} mentions {marker}'
            for line in containers_release_names():
                assert f'ghcr.io/ignite-ember/{line}' not in text, f'{workflow.name} names {line}'

    def test_the_documented_secret_names_are_the_ones_used(self):
        text = _RELEASE.read_text()
        doc = (_ROOT / 'docs/OFFLINE_UPDATES.md').read_text()

        for name in ('COSIGN_PRIVATE_KEY', 'COSIGN_PASSWORD', 'MINISIGN_SECRET_KEY', 'MINISIGN_PASSWORD'):
            assert f'secrets.{name}' in text, name
            assert name in doc, f'{name} is used by release.yml but not documented'
        for name in ('COSIGN_PUBLIC_KEY', 'MINISIGN_PUBLIC_KEY'):
            assert f'vars.{name}' in text, name
            assert name in doc, f'{name} is used by release.yml but not documented'


# ── Signed releases that are not images ───────────────────────────────────
#
# Fleet ships binaries, not images: a release directory with SHA256SUMS
# over the files and their SBOMs, and a cosign signature over SHA256SUMS
# (scripts/release/{build,sign}.sh in Fleet's repository). `igni-bundle
# add-artefact` carries one into the bundle only if it is signed with the
# release key; `igni-import` holds it to its SHA256SUMS and, with
# --cosign-key, to that signature. cosign is the real one, with keys made
# here; it signs offline, as the release does.

if shutil.which('cosign') is None and os.environ.get('CI'):
    raise RuntimeError('cosign is not installed; the signed-artefact tests cannot run')

_needs_cosign = pytest.mark.skipif(shutil.which('cosign') is None, reason='cosign is not installed')


class CosignKeys(BaseModel):
    public: pathlib.Path
    secret: pathlib.Path
    other_public: pathlib.Path
    other_secret: pathlib.Path


def _cosign_pair(directory: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    directory.mkdir()
    subprocess.run(
        ['cosign', 'generate-key-pair'],
        cwd=directory,
        env={**os.environ, 'COSIGN_PASSWORD': _PASSWORD},
        check=True,
        capture_output=True,
    )
    return directory / 'cosign.pub', directory / 'cosign.key'


@pytest.fixture(scope='module')
def cosign_keys(tmp_path_factory: pytest.TempPathFactory) -> CosignKeys:
    if shutil.which('cosign') is None:
        pytest.skip('cosign is not installed')
    base = tmp_path_factory.mktemp('cosign')
    public, secret = _cosign_pair(base / 'release')
    other_public, other_secret = _cosign_pair(base / 'impostor')
    return CosignKeys(public=public, secret=secret, other_public=other_public, other_secret=other_secret)


def _cosign_v3() -> bool:
    """cosign 3 writes a bundle unless told not to, and refuses
    `--output-signature` without these flags. release.yml pins v2.4.1, which has
    neither; a developer's Homebrew cosign is 3."""
    version = subprocess.run(['cosign', 'version'], capture_output=True, text=True, check=False).stdout
    major = re.search(r'GitVersion:\s+v(\d+)', version)
    return major is not None and int(major.group(1)) >= 3


def _sign_blob(secret: pathlib.Path, blob: pathlib.Path) -> None:
    """As Fleet's sign.sh: key-based, nothing uploaded to a log."""
    legacy = ['--new-bundle-format=false', '--use-signing-config=false'] if _cosign_v3() else []
    subprocess.run(
        [
            'cosign',
            'sign-blob',
            '--yes',
            '--key',
            str(secret),
            '--tlog-upload=false',
            *legacy,
            '--output-signature',
            str(blob.parent / 'SHA256SUMS.sig'),
            str(blob),
        ],
        env={**os.environ, 'COSIGN_PASSWORD': _PASSWORD},
        check=True,
        capture_output=True,
    )


def _write_sums(release: pathlib.Path) -> None:
    files = sorted(p.name for p in release.iterdir() if p.name not in ('SHA256SUMS', 'SHA256SUMS.sig', 'UNSIGNED'))
    (release / 'SHA256SUMS').write_text(
        ''.join(f'{hashlib.sha256((release / f).read_bytes()).hexdigest()}  {f}\n' for f in files)
    )


def _fleet_release(directory: pathlib.Path, *, sign_with: pathlib.Path | None) -> pathlib.Path:
    """A release directory shaped like Fleet's build.sh output."""
    release = directory / 'fleet-release'
    release.mkdir()
    for binary in ('igni-gateway', 'igni-node-agent', 'cvm', 'igni-guest-agent'):
        (release / binary).write_bytes(os.urandom(2048))
        (release / f'{binary}.cdx.json').write_text(f'{{"bomFormat":"CycloneDX","metadata":"{binary}"}}')
    (release / 'VERSION').write_text('0.1.0\n')
    _write_sums(release)
    if sign_with is not None:
        _sign_blob(sign_with, release / 'SHA256SUMS')
    return release


def _add_artefact(
    staging: pathlib.Path, release: pathlib.Path, public: pathlib.Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return _run(
        [
            str(_BUNDLE),
            'add-artefact',
            '--staging',
            str(staging),
            '--name',
            'igni-fleet',
            '--version',
            '0.1.0',
            '--from',
            str(release),
            '--cosign-key',
            str(public),
        ],
        env=env,
    )


def _seal(
    staging: pathlib.Path, key: KeyPair, out: pathlib.Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return _run(
        [
            str(_BUNDLE),
            'seal',
            '--staging',
            str(staging),
            '--version',
            _VERSION,
            '--commit',
            'f043313',
            '--key',
            str(key.secret),
            '--public-key',
            str(key.public),
            '--out',
            str(out),
        ],
        env=env,
    )


@pytest.fixture
def with_fleet(sealed: Sealed, cosign_keys: CosignKeys, tmp_path: pathlib.Path) -> Sealed:
    """The `sealed` bundle again, with Fleet's signed release added."""
    release = _fleet_release(tmp_path, sign_with=cosign_keys.secret)
    added = _add_artefact(sealed.staging, release, cosign_keys.public, sealed.env)
    assert added.returncode == 0, added.stderr

    result = _seal(sealed.staging, sealed.key, tmp_path / 'with-fleet', sealed.env)
    assert result.returncode == 0, result.stderr
    return sealed.model_copy(update={'tarball': pathlib.Path(result.stdout.strip())})


@_needs_cosign
class TestASignedArtefactIsCarried:
    def test_it_is_in_the_bundle_under_the_manifest(self, with_fleet: Sealed):
        staging = with_fleet.staging
        manifest = (staging / 'MANIFEST').read_text()
        index = (staging / 'artefacts.tsv').read_text().splitlines()

        sums = staging / 'artefacts' / 'igni-fleet' / 'SHA256SUMS'
        assert index == [f'igni-fleet\t0.1.0\tsha256:{hashlib.sha256(sums.read_bytes()).hexdigest()}']
        for name in ('igni-gateway', 'SHA256SUMS', 'SHA256SUMS.sig', 'igni-gateway.cdx.json'):
            assert f'  artefacts/igni-fleet/{name}\n' in manifest, name
        assert '  artefacts.tsv\n' in manifest
        # Beside every other SBOM, where a customer looks for them.
        assert (staging / 'sboms' / 'igni-fleet-igni-gateway.cdx.json').is_file()

    def test_import_verifies_its_signature_and_keeps_it(
        self, with_fleet: Sealed, cosign_keys: CosignKeys, tmp_path: pathlib.Path
    ):
        kept = tmp_path / 'kept'
        result = _import(with_fleet, '--verify-only', '--cosign-key', str(cosign_keys.public), '--keep', str(kept))

        assert result.returncode == 0, result.stderr
        assert 'igni-fleet 0.1.0 (9 files, SHA256SUMS matches, cosign-verified)' in result.stdout
        assert (kept / f'igni-bundle-{_VERSION}' / 'artefacts' / 'igni-fleet' / 'igni-node-agent').is_file()

    def test_an_import_pushes_only_the_images(self, with_fleet: Sealed):
        result = _import(with_fleet, '--registry', _REGISTRY)

        assert result.returncode == 0, result.stderr
        assert 'Artefact:   artefacts/igni-fleet/ 0.1.0 (9 files, SHA256SUMS matches)' in result.stdout
        assert not [line for line in _pushes(with_fleet) if 'fleet' in line]

    def test_a_tampered_artefact_is_refused(self, with_fleet: Sealed, tmp_path: pathlib.Path):
        def edit(top: pathlib.Path) -> None:
            binary = top / 'artefacts' / 'igni-fleet' / 'igni-node-agent'
            binary.write_bytes(b'\x00' + binary.read_bytes()[1:])

        result = _import(with_fleet, '--registry', _REGISTRY, tarball=_rewrite(with_fleet.tarball, tmp_path, edit=edit))

        assert result.returncode == 1
        assert result.stderr.strip() == 'igni-import: REFUSED: hash mismatch: artefacts/igni-fleet/igni-node-agent'
        assert _pushes(with_fleet) == []

    def test_a_tampered_artefact_with_its_sums_rewritten_is_refused(self, with_fleet: Sealed, tmp_path: pathlib.Path):
        """SHA256SUMS and the MANIFEST both made to agree: the minisign
        signature is what is left to catch it."""

        def edit(top: pathlib.Path) -> None:
            release = top / 'artefacts' / 'igni-fleet'
            (release / 'cvm').write_bytes(b'replaced')
            _write_sums(release)

        result = _import(with_fleet, '--verify-only', tarball=_rewrite(with_fleet.tarball, tmp_path, edit=edit))

        assert result.returncode == 1
        assert 'hash mismatch: artefacts/igni-fleet/' in result.stderr


@_needs_cosign
class TestOnlyASignedArtefactIsCarried:
    def test_unsigned(self, sealed: Sealed, cosign_keys: CosignKeys, tmp_path: pathlib.Path):
        """sign.sh leaves UNSIGNED beside a release it could not sign."""
        release = _fleet_release(tmp_path, sign_with=None)
        (release / 'UNSIGNED').write_text('COSIGN_PRIVATE_KEY is not set')

        result = _add_artefact(sealed.staging, release, cosign_keys.public, sealed.env)

        assert result.returncode == 1
        assert 'has no SHA256SUMS.sig: it is not signed' in result.stderr
        assert not (sealed.staging / 'artefacts').exists()

    def test_signed_with_another_key(self, sealed: Sealed, cosign_keys: CosignKeys, tmp_path: pathlib.Path):
        release = _fleet_release(tmp_path, sign_with=cosign_keys.other_secret)

        result = _add_artefact(sealed.staging, release, cosign_keys.public, sealed.env)

        assert result.returncode == 1
        assert 'does not verify with the release key' in result.stderr
        assert not (sealed.staging / 'artefacts.tsv').exists()

    def test_a_file_changed_after_signing(self, sealed: Sealed, cosign_keys: CosignKeys, tmp_path: pathlib.Path):
        release = _fleet_release(tmp_path, sign_with=cosign_keys.secret)
        (release / 'igni-gateway').write_bytes(b'changed')

        result = _add_artefact(sealed.staging, release, cosign_keys.public, sealed.env)

        assert result.returncode == 1
        assert 'igni-fleet: hash mismatch: igni-gateway' in result.stderr

    def test_a_file_not_in_its_sums(self, sealed: Sealed, cosign_keys: CosignKeys, tmp_path: pathlib.Path):
        release = _fleet_release(tmp_path, sign_with=cosign_keys.secret)
        (release / 'extra').write_text('unsigned')

        result = _add_artefact(sealed.staging, release, cosign_keys.public, sealed.env)

        assert result.returncode == 1
        assert 'extra is not listed in SHA256SUMS' in result.stderr

    def test_no_sbom(self, sealed: Sealed, cosign_keys: CosignKeys, tmp_path: pathlib.Path):
        release = _fleet_release(tmp_path, sign_with=None)
        for sbom in release.glob('*.cdx.json'):
            sbom.unlink()
        _write_sums(release)
        _sign_blob(cosign_keys.secret, release / 'SHA256SUMS')

        result = _add_artefact(sealed.staging, release, cosign_keys.public, sealed.env)

        assert result.returncode == 1
        assert 'has no CycloneDX SBOM' in result.stderr

    def test_a_bad_signature_that_reached_a_bundle_is_refused_on_import(
        self, sealed: Sealed, cosign_keys: CosignKeys, tmp_path: pathlib.Path
    ):
        """Staged by hand, around add-artefact, and sealed with the real
        minisign key: the MANIFEST is good, so --cosign-key is the check
        that catches it."""
        release = _fleet_release(tmp_path, sign_with=cosign_keys.other_secret)
        target = sealed.staging / 'artefacts' / 'igni-fleet'
        shutil.copytree(release, target)
        digest = hashlib.sha256((target / 'SHA256SUMS').read_bytes()).hexdigest()
        (sealed.staging / 'artefacts.tsv').write_text(f'igni-fleet\t0.1.0\tsha256:{digest}\n')
        sealed_again = _seal(sealed.staging, sealed.key, tmp_path / 'resealed', sealed.env)
        assert sealed_again.returncode == 0, sealed_again.stderr
        tarball = pathlib.Path(sealed_again.stdout.strip())

        without_key = _import(sealed, '--verify-only', tarball=tarball)
        with_key = _import(sealed, '--verify-only', '--cosign-key', str(cosign_keys.public), tarball=tarball)

        assert without_key.returncode == 0, without_key.stderr
        assert with_key.returncode == 1
        assert with_key.stderr.strip() == (
            'igni-import: REFUSED: igni-fleet: its cosign signature over SHA256SUMS does not verify'
        )

    def test_seal_refuses_artefacts_with_no_index(self, sealed: Sealed, tmp_path: pathlib.Path):
        (sealed.staging / 'artefacts' / 'igni-fleet').mkdir(parents=True)
        (sealed.staging / 'artefacts' / 'igni-fleet' / 'cvm').write_bytes(b'x')

        result = _seal(sealed.staging, sealed.key, tmp_path / 'again', sealed.env)

        assert result.returncode == 1
        assert 'artefacts/ but no artefacts.tsv' in result.stderr


class TestCdCarriesSignedArtefacts:
    def test_the_bundle_job_adds_each_pinned_artefact_with_the_release_key(self):
        job = yaml.safe_load(_RELEASE.read_text())['jobs']['offline-bundle']
        script = '\n'.join(str(step.get('run') or '') for step in job['steps'])

        assert 'containers/release/artefacts.tsv' in script
        assert re.search(r'igni-bundle add-artefact .*--cosign-key', script.replace('\\\n', ' ')), script
        add = script.index('add-artefact')
        assert add < script.index('igni-bundle seal'), 'artefacts must be staged before the MANIFEST is signed'

    def test_the_manifest_of_artefacts_parses(self):
        lines = [
            line.split()
            for line in (_ROOT / 'containers/release/artefacts.tsv').read_text().splitlines()
            if line.strip() and not line.startswith('#')
        ]
        for fields in lines:
            assert len(fields) == 2, fields
            assert re.fullmatch(r'[a-z0-9][a-z0-9._-]*', fields[0]), fields


# ── Third-party images ────────────────────────────────────────────────────
#
# B38. The chart's backup CronJob runs Postgres from beside
# image.repository, so the bundle has to carry it, or an air-gapped install
# that turns backups on pulls from Docker Hub or fails.

_THIRD_PARTY = _ROOT / 'containers/release/third-party.tsv'


def _third_party() -> list[list[str]]:
    return [line.split() for line in _THIRD_PARTY.read_text().splitlines() if line.strip() and not line.startswith('#')]


class TestTheThirdPartyImagesAreCarried:
    def test_the_list_parses_and_pins_by_digest(self):
        rows = _third_party()

        assert rows, 'third-party.tsv lists nothing'
        for fields in rows:
            assert len(fields) == 3, fields
            name, upstream, tag = fields
            assert re.fullmatch(r'[a-z0-9][a-z0-9._-]*', name), fields
            # A tag can move; a digest is what was reviewed.
            assert re.fullmatch(r'[a-z0-9.-]+(?::\d+)?/[a-z0-9._/-]+@sha256:[0-9a-f]{64}', upstream), fields
            assert re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}', tag), fields

    def test_it_carries_the_postgres_the_backup_job_runs(self):
        backup = yaml.safe_load((_ROOT / 'charts/igni/values.yaml').read_text())['backup']['image']
        # A map (repository, tag), or the full reference it was before.
        tag = str(backup['tag']) if isinstance(backup, dict) else backup.rsplit(':', 1)[1]

        assert ['postgres', tag] in [
            [name, row_tag] for name, _, row_tag in _third_party()
        ], f'the chart runs postgres:{tag} for backups and the bundle does not carry it'

    def _script(self) -> str:
        job = yaml.safe_load(_RELEASE.read_text())['jobs']['offline-bundle']
        return '\n'.join(str(step.get('run') or '') for step in job['steps']).replace('\\\n', ' ')

    def test_each_is_mirrored_by_digest_and_signed_with_the_release_key(self):
        script = self._script()
        mirror = script[script.index('containers/release/third-party.tsv') :]

        assert 'skopeo copy --all --preserve-digests "docker://${upstream}"' in mirror
        assert 'is not ${digest}' in mirror, 'a GHCR copy under another digest is not refused'
        assert re.search(r'cosign sign --yes --key env://COSIGN_PRIVATE_KEY --tlog-upload=false "\$\{ref\}"', mirror)
        assert re.search(r'cosign attest .*--tlog-upload=false --type cyclonedx', mirror)

    def test_each_is_exported_with_the_release_key_before_the_seal(self):
        script = self._script()
        listed = [m.start() for m in re.finditer(r'containers/release/third-party\.tsv', script)]

        assert len(listed) == 2, 'the bundle job should read third-party.tsv to mirror and to export'
        export = script[listed[1] :]
        assert re.search(r'export_image --name "\$\{name\}" --repository "\$\{REGISTRY\}/\$\{name\}"', export)
        assert 'igni-bundle export-image --staging "${STAGING}" --cosign-key' in script
        assert listed[0] < script.index('igni-bundle export-image') < listed[1] < script.index('igni-bundle seal')

    def test_the_customer_documentation_names_them(self):
        doc = (_ROOT / 'docs/OFFLINE_UPDATES.md').read_text()

        for name, _, tag in _third_party():
            assert f'{name}:{tag}' in doc, f'OFFLINE_UPDATES.md does not say the bundle carries {name}:{tag}'


class TestAReleaseIsKept:
    """CRA Art. 13(9): each security update available for ten years. A CI
    artefact expires after 90 days; GHCR keeps what is pushed to it."""

    def _steps(self) -> list[dict]:
        return yaml.safe_load(_RELEASE.read_text())['jobs']['offline-bundle']['steps']

    def test_the_sealed_bundle_is_pushed_to_ghcr(self):
        steps = self._steps()
        names = [str(step.get('name') or '') for step in steps]
        keep = steps[names.index('Keep the release bundle')]
        script = str(keep['run'])

        assert names.index('Seal and sign the bundle') < names.index('Keep the release bundle')
        assert re.search(r'oras push "\$\{ref\}"', script), script
        assert 'ref="${REGISTRY}/igni-bundle:${VERSION}"' in script
        for pushed in ('igni-bundle-${VERSION}.tar:', 'igni-bundle-${VERSION}.tar.sha256:'):
            assert pushed in script, pushed
        # The resolved commit of igni-server, not GITHUB_SHA: in a called
        # workflow that is the caller's, igni__release's, commit.
        assert 'org.opencontainers.image.revision=${COMMIT}' in script
        assert keep['env']['COMMIT'] == '${{ needs.resolve.outputs.sha }}'
        assert 'org.opencontainers.image.source=${GITHUB_SERVER_URL}/ignite-ember/igni-server' in script

    def test_a_kept_release_is_never_overwritten(self):
        """Without the check, every later build of the same version on
        main would replace the release a tag and a customer point at."""
        script = str(next(s for s in self._steps() if s.get('name') == 'Keep the release bundle')['run'])

        fetch = script.index('oras manifest fetch "${ref}"')
        assert fetch < script.index('oras push'), 'the existing tag must be checked before the push'
        assert 'exit 0' in script[fetch : script.index('oras push')]

    def test_the_retention_policy_is_written_down(self):
        doc = ' '.join((_ROOT / 'docs/OFFLINE_UPDATES.md').read_text().split())

        assert '## How long releases are kept' in (_ROOT / 'docs/OFFLINE_UPDATES.md').read_text()
        assert 'ten years' in doc
        assert 'nobody deletes a release tag or a release bundle' in doc
        assert 'igni-bundle:<version>' in doc
