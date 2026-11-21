"""The sealed licence-integrity report a super admin exports for the vendor.

On request, igni server gathers what it knows about the deployment's
licence integrity — the current mark, the file's write history and any
tamper events, the licence id and the per-module usage the licence allows
— and seals it so that *only Ignite Ember* can open it. The customer can
hand the blob over without reading it; the vendor opens it with the owner
key in Cloud KMS (`tools/licence/open_report.py`).

**How it is sealed (hybrid).** A fresh random AES-256-GCM key encrypts the
report JSON. That key is wrapped with the vendor's RSA public key using
RSA-OAEP(SHA-256). The vendor's *private* half lives only in Cloud KMS
(project `ignite-ember-signing`, key ring `igni-release`, an
`asymmetric-decryption` key), so opening the report needs
`cloudkms.cryptoKeyDecrypter` on that key — it is a different key from the
Ed25519 licence *signing* key, and knowing one tells you nothing about the
other. The RSA public key is compiled into the release the same way the
licence verification key is (`INTEGRITY_REPORT_PUBLIC_KEY` / the
`INTEGRITY_REPORT_PUBLIC_KEY_PEM` setting); see docs/licence-file.md,
"The sealed report", and igni__business-ops/ops/key-ceremony.md for the
one-time ceremony that creates the KMS key.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import os

from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from pydantic import BaseModel

from app.services.deployment_licence.integrity_state import IntegrityState
from app.services.licence import ModuleStatus

#: The report format, and the hybrid scheme. The vendor tool checks both before it tries to open a blob.
REPORT_FORMAT = 'igni-integrity-report/1'
SEAL_ALG = 'RSA-OAEP-256+AES-256-GCM'

#: Ignite Ember's integrity-report public key, PEM (SubjectPublicKeyInfo). Filled in by the key ceremony
#: (igni__business-ops/ops/key-ceremony.md). Empty until then; the export says so rather than sealing to nobody.
INTEGRITY_REPORT_PUBLIC_KEY = ''


class SealedReport(BaseModel):
    """What the export returns: an opaque, vendor-sealed blob a customer can hand over safely."""

    format: str = REPORT_FORMAT
    alg: str = SEAL_ALG
    #: The KMS key the vendor opens it with (resource name), for the vendor's convenience. Not a secret.
    key_hint: str | None = None
    wrapped_key: str
    nonce: str
    ciphertext: str


class ReportUnavailable(Exception):
    """No integrity-report public key is configured, so nothing can be sealed."""


def build_report(
    *,
    licence_id: str | None,
    customer: str | None,
    mark: datetime | None,
    clock_warning: str | None,
    state: IntegrityState,
    modules: list[ModuleStatus],
) -> dict[str, object]:
    """The report's cleartext, before it is sealed. No personal data: a licensee is an organisation (LG-5)."""
    return {
        'format': REPORT_FORMAT,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'licence_id': licence_id,
        'customer': customer,
        'install_id': state.install_id or None,
        'mark': mark.astimezone(timezone.utc).isoformat() if mark else None,
        'clock_warning': clock_warning,
        'tampered': state.tampered or bool(state.recent_tamper()),
        'write_count': state.seq,
        'events': [event.as_json() for event in state.events],
        'modules': [
            {
                'module': status.module.value,
                'state': status.state.value,
                'reason': status.reason.value if status.reason else None,
                'users': status.users,
                'expires': status.expires.isoformat() if status.expires else None,
            }
            for status in modules
        ],
    }


def seal(report: dict[str, object], *, public_key_pem: str, key_hint: str | None = None) -> SealedReport:
    """Encrypt `report` to the vendor's RSA public key (hybrid). The matching private key is only in KMS."""
    pem = (public_key_pem or '').strip()
    if not pem:
        raise ReportUnavailable('no integrity-report public key is configured for this release')
    public_key = load_pem_public_key(pem.encode('utf-8'))
    if not isinstance(public_key, RSAPublicKey):
        raise ReportUnavailable('the integrity-report public key is not an RSA key')
    return seal_to_key(report, public_key, key_hint=key_hint)


def seal_to_key(report: dict[str, object], public_key: RSAPublicKey, *, key_hint: str | None = None) -> SealedReport:
    aes_key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    body = AESGCM(aes_key).encrypt(nonce, json.dumps(report, sort_keys=True).encode('utf-8'), None)
    wrapped = public_key.encrypt(
        aes_key,
        padding.OAEP(mgf=padding.MGF1(algorithm=SHA256()), algorithm=SHA256(), label=None),
    )
    return SealedReport(
        key_hint=key_hint,
        wrapped_key=_b64(wrapped),
        nonce=_b64(nonce),
        ciphertext=_b64(body),
    )


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode('ascii')
