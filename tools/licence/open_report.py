#!/usr/bin/env python
"""Open a sealed licence-integrity report. Ignite Ember's tool; customers have no use for it.

A customer's super admin exports a sealed report from the portal
(`POST /v1/portal/licence/integrity-report`) and hands Ignite Ember the
blob. It is encrypted to Ignite Ember's integrity-report key, whose
private half lives only in Cloud KMS, so only someone with
`cloudkms.cryptoKeyDecrypter` on that key can open it.

    # unwrap the content key with the KMS decrypt key, then read the report
    KEY=projects/ignite-ember-signing/locations/europe-west4/keyRings/igni-release
    CLOUDSDK_ACTIVE_CONFIG_NAME=ignite-ember \\
    IGNI_REPORT_KMS_KEY=$KEY/cryptoKeys/licence-integrity-2026/cryptoKeyVersions/1 \\
    python tools/licence/open_report.py report.sealed.json

The scheme is hybrid (see igni server
`app/services/deployment_licence/sealed_report.py`): a random AES-256-GCM
key encrypts the report, and that key is wrapped with RSA-OAEP(SHA-256)
to the KMS key's public half. `gcloud kms asymmetric-decrypt` unwraps it
as the account `gcloud` is logged in as; only that account may. For tests
and for a locally generated key, `--key-file` names an RSA private key in
PEM instead.
"""

from __future__ import annotations

import argparse
import base64
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.serialization import load_pem_private_key

REPORT_FORMAT = 'igni-integrity-report/1'
SEAL_ALG = 'RSA-OAEP-256+AES-256-GCM'

_OAEP = padding.OAEP(mgf=padding.MGF1(algorithm=SHA256()), algorithm=SHA256(), label=None)
_VERSION = re.compile(r'projects/([^/]+)/locations/([^/]+)/keyRings/([^/]+)/cryptoKeys/([^/]+)/cryptoKeyVersions/(\d+)')


class OpenRefused(Exception):
    """The report cannot be opened."""


def _unwrap_with_kms(wrapped: bytes, kms_key: str) -> bytes:
    match = _VERSION.fullmatch(kms_key)
    if match is None:
        raise OpenRefused(f'{kms_key!r} is not a KMS key version (projects/…/cryptoKeyVersions/N)')
    if shutil.which('gcloud') is None:
        raise OpenRefused('gcloud is not installed; it decrypts with the KMS key')
    project, location, ring, key, version = match.groups()
    with tempfile.TemporaryDirectory() as directory:
        ciphertext = pathlib.Path(directory, 'wrapped')
        plaintext = pathlib.Path(directory, 'key')
        ciphertext.write_bytes(wrapped)
        done = subprocess.run(
            [
                'gcloud',
                'kms',
                'asymmetric-decrypt',
                f'--project={project}',
                f'--location={location}',
                f'--keyring={ring}',
                f'--key={key}',
                f'--version={version}',
                f'--ciphertext-file={ciphertext}',
                f'--plaintext-file={plaintext}',
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if done.returncode != 0:
            raise OpenRefused(f'gcloud kms asymmetric-decrypt failed: {done.stderr.strip()}')
        return plaintext.read_bytes()


def _unwrap_with_file(wrapped: bytes, key_file: str) -> bytes:
    key = load_pem_private_key(pathlib.Path(key_file).read_bytes(), password=None)
    if not isinstance(key, RSAPrivateKey):
        raise OpenRefused(f'{key_file} is not an RSA private key')
    return key.decrypt(wrapped, _OAEP)


def open_report(blob: dict[str, object], *, kms_key: str | None, key_file: str | None) -> dict[str, object]:
    if blob.get('format') != REPORT_FORMAT:
        raise OpenRefused(f'not an {REPORT_FORMAT} report (format={blob.get("format")!r})')
    if blob.get('alg') != SEAL_ALG:
        raise OpenRefused(f'unexpected seal algorithm {blob.get("alg")!r}')
    wrapped = base64.b64decode(str(blob['wrapped_key']))
    nonce = base64.b64decode(str(blob['nonce']))
    ciphertext = base64.b64decode(str(blob['ciphertext']))
    if key_file:
        aes_key = _unwrap_with_file(wrapped, key_file)
    else:
        key = kms_key or _env_kms_key()
        if not key:
            raise OpenRefused('no key: pass --kms-key or --key-file, or set IGNI_REPORT_KMS_KEY')
        aes_key = _unwrap_with_kms(wrapped, key)
    plain = AESGCM(aes_key).decrypt(nonce, ciphertext, None)
    result = json.loads(plain)
    assert isinstance(result, dict)
    return result


def _env_kms_key() -> str | None:
    import os

    return os.environ.get('IGNI_REPORT_KMS_KEY')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Open a sealed licence-integrity report (Ignite Ember only).')
    parser.add_argument('blob', help='the sealed report file (JSON)')
    parser.add_argument('--kms-key', help='the KMS key version that decrypts (or IGNI_REPORT_KMS_KEY)')
    parser.add_argument('--key-file', help='an RSA private key in PEM, for tests or a local key')
    args = parser.parse_args(argv)
    try:
        blob = json.loads(pathlib.Path(args.blob).read_text())
        report = open_report(blob, kms_key=args.kms_key, key_file=args.key_file)
    except OpenRefused as exc:
        print(f'cannot open the report: {exc}', file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
