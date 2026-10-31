"""The public keys a licence may be signed with. Shipped in the release.

Only public halves live here. Ignite Ember's private signing key never
enters any repository: `tools/licence/issue_licence.py` reads it from a
file or the environment on the machine that issues licences.

**Release keys.** `RELEASE_KEYS` is empty until Ignite Ember generates
its signing key (`issue_licence.py keygen`) and pastes the public half
here, in every copy of this file and in
`igni__cloudvm/crates/licence/src/keys.rs`. Until then no deployment
outside development accepts any licence, so every paid module stays
off. That is deliberate: a placeholder key would be a key somebody holds.

**The test key** is in the repository, private half included
(`tests/fixtures/licence/test-signing-key.pem`), so anybody can sign
with it. It is trusted only when `APP_ENV=dev`. It must never be added
to `RELEASE_KEYS`.

To rotate: add the new key under a new id, release, then issue new
licences with it. Remove the old key only once no licence signed with
it is still in its term or its read-only period.
"""

from __future__ import annotations

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .verifier import Base64Url

#: Ignite Ember's licence signing keys: key id → raw Ed25519 public key, base64url.
RELEASE_KEYS: dict[str, str] = {}

#: The development and test key. Trusted only with `APP_ENV=dev`.
TEST_KEY_ID = 'igni-test-1'
TEST_KEYS: dict[str, str] = {TEST_KEY_ID: 'PqqOsztrzFjfcds-klnVq2nMhFG3SS8L6F5pdnk3lOk'}


class TrustedKeys:
    """The keys a process accepts licences from."""

    @staticmethod
    def parse(keys: dict[str, str]) -> dict[str, Ed25519PublicKey]:
        return {key_id: Ed25519PublicKey.from_public_bytes(Base64Url.decode(raw)) for key_id, raw in keys.items()}

    @classmethod
    def for_environment(cls, app_env: str) -> dict[str, Ed25519PublicKey]:
        """The release keys; and the test key too, in development only."""
        keys = dict(RELEASE_KEYS)
        if app_env == 'dev':
            keys.update(TEST_KEYS)
        return cls.parse(keys)
