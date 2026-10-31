"""Checking a licence file: Ignite Ember's signature, then the payload's rules.

Entirely offline. The public keys come with the release (`keys.py`);
nothing here opens a socket, and nothing asks anybody whether a licence
is still good. A file that verifies is good until its dates say
otherwise (`status.py`).
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import ValidationError

from .format import LicenceEnvelope, LicencePayload


class LicenceError(Exception):
    """The file is not a licence this release accepts. The class name is the reason."""

    reason = 'malformed'


class LicenceMalformed(LicenceError):
    """Not a licence file at all, or a payload that breaks the format's rules."""

    reason = 'malformed'


class LicenceUnknownKey(LicenceError):
    """Signed with a key this release does not trust (another vendor, a test key, a retired key)."""

    reason = 'unknown_key'


class LicenceSignatureInvalid(LicenceError):
    """The signature does not match: the payload was changed after signing, or signed by another key."""

    reason = 'invalid_signature'


class Base64Url:
    """Unpadded base64url, the encoding of a licence's payload and signature."""

    @staticmethod
    def encode(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')

    @staticmethod
    def decode(text: str) -> bytes:
        try:
            return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))
        except (binascii.Error, ValueError) as exc:
            raise LicenceMalformed('not base64url') from exc


class LicenceVerifier:
    """Verifies licence files against a fixed set of trusted public keys."""

    def __init__(self, trusted_keys: Mapping[str, Ed25519PublicKey]) -> None:
        self._keys = dict(trusted_keys)

    def verify(self, raw: bytes) -> LicencePayload:
        """The payload of a genuine licence, or a `LicenceError` naming what is wrong."""
        envelope = self._envelope(raw)
        key = self._keys.get(envelope.key_id)
        if key is None:
            raise LicenceUnknownKey(f'signed with key {envelope.key_id!r}, which this release does not trust')
        payload_bytes = Base64Url.decode(envelope.payload)
        try:
            key.verify(Base64Url.decode(envelope.signature), payload_bytes)
        except InvalidSignature as exc:
            raise LicenceSignatureInvalid('the signature does not match the licence') from exc
        try:
            return LicencePayload.model_validate_json(payload_bytes)
        except ValidationError as exc:
            raise LicenceMalformed(f'the signed licence breaks the format: {exc.errors()[0]["msg"]}') from exc

    @staticmethod
    def _envelope(raw: bytes) -> LicenceEnvelope:
        try:
            return LicenceEnvelope.model_validate_json(raw)
        except ValidationError as exc:
            raise LicenceMalformed('not a licence file') from exc
