"""Signing a licence: what Ignite Ember's issuing tool and the tests do.

Holds no key. The caller hands over the private key it loaded, from a
file or the environment on the issuing machine
(`tools/licence/issue_licence.py`), or the test key in the tests.
"""

from __future__ import annotations

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .format import LicenceEnvelope, LicencePayload
from .verifier import Base64Url


class LicenceSigner:
    """Turns a payload into a licence file, signed with one key."""

    def __init__(self, private_key: Ed25519PrivateKey, *, key_id: str) -> None:
        self._key = private_key
        self._key_id = key_id

    def sign(self, payload: LicencePayload) -> bytes:
        """The licence file's bytes. The payload is validated again on the way out."""
        payload_bytes = LicencePayload.model_validate(payload.model_dump()).model_dump_json().encode('utf-8')
        envelope = LicenceEnvelope(
            igni_licence=1,
            key_id=self._key_id,
            payload=Base64Url.encode(payload_bytes),
            signature=Base64Url.encode(self._key.sign(payload_bytes)),
        )
        return envelope.model_dump_json(indent=2).encode('utf-8') + b'\n'
