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
    """A bundle as CD produces it, and what is needed to import it."""

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
        """How CD signs: the secret key is encrypted and its password is a
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


class TestCdSignsWhatItShips:
    """The workflow half. Air-gapped customers verify with a public key and
    no network, so signing must be key-based and must not depend on a
    transparency log they cannot reach."""

    def _cd(self) -> dict:
        return yaml.safe_load(_CD.read_text())

    def _runs(self, job: dict) -> str:
        return '\n'.join(str(step.get('run') or '') for step in job.get('steps') or [])

    def test_every_job_that_pushes_an_image_signs_and_attests_it_with_the_key(self):
        cd = self._cd()
        pushing = {name: job for name, job in cd['jobs'].items() if 'docker push' in self._runs(job)}

        assert pushing, 'no job in cd.yml pushes an image; this test is stale'
        for name, job in pushing.items():
            script = self._runs(job)
            assert re.search(r'cosign sign .*--key env://COSIGN_PRIVATE_KEY', script), f'{name} does not sign'
            assert re.search(r'cosign attest .*--type cyclonedx', script), f'{name} attests no SBOM'
            assert '--tlog-upload=false' in script, f'{name} uploads to a public transparency log'

    def test_signing_is_never_keyless(self):
        text = _CD.read_text()

        assert 'COSIGN_EXPERIMENTAL' not in text
        for line in re.findall(r'cosign (?:sign|attest)\b[^\n]*', text):
            assert '--key' in line, line

    def test_a_release_on_main_is_sealed_into_a_bundle(self):
        cd = self._cd()
        job = cd['jobs']['offline-bundle']
        script = self._runs(job)

        assert "needs.resolve-env.outputs.env == 'prod'" in str(job.get('if'))
        assert 'tools/release/igni-bundle seal' in script
        assert '--public-key' in script, 'the bundle job does not check its own signature'
        uploads = [s for s in job['steps'] if str(s.get('uses', '')).startswith('actions/upload-artifact')]
        assert uploads and 'igni-bundle-' in str(uploads[0]['with']['path'])

    def test_the_documented_secret_names_are_the_ones_used(self):
        text = _CD.read_text()
        doc = (_ROOT / 'docs/OFFLINE_UPDATES.md').read_text()

        for name in ('COSIGN_PRIVATE_KEY', 'COSIGN_PASSWORD', 'MINISIGN_SECRET_KEY', 'MINISIGN_PASSWORD'):
            assert f'secrets.{name}' in text, name
            assert name in doc, f'{name} is used by cd.yml but not documented'
        for name in ('COSIGN_PUBLIC_KEY', 'MINISIGN_PUBLIC_KEY'):
            assert f'vars.{name}' in text, name
            assert name in doc, f'{name} is used by cd.yml but not documented'
