"""The public keys a licence may be signed with. Shipped in the release.

Only public halves live here. Ignite Ember's private signing key never
enters any repository, or any machine: it is in Cloud KMS, and
`tools/licence/issue_licence.py` asks KMS to sign.

**Release keys.** `ie-2026` is that key, created 2026-11-07 (KMS key
`licence-ie-2026`, version 1, in project `ignite-ember-signing`). The
same entry is in every copy of this file and in
`igni__cloudvm/crates/licence/src/keys.rs`. A release trusts these and
nothing else, whatever its environment or settings say.

**The test key** is not in this file, and not in any image. Its private
half is in the repository (`tests/fixtures/licence/test-signing-key.pem`),
so anybody can sign with it; its public half is beside it in
`tests/fixtures/licence/dev-keys.json`. The test suite loads that file
itself. A source checkout run for local development trusts it too, and
only when all three hold:

* the environment is exactly `dev`;
* the process runs from a source checkout: the file is under the nearest
  directory above this package that has a `pyproject.toml`. No image has
  either: `tests/` and `pyproject.toml` are never copied into one;
* this is not a release. The image build writes `RELEASE` beside this
  file (`containers/be/Dockerfile`, as root, before the image drops to
  its own user). While it is there no development key is read, wherever
  one is mounted and whatever the environment says.

To rotate: add the new key under a new id, release, then issue new
licences with it. Remove the old key only once no licence signed with
it is still in its term or its read-only period.
"""

from __future__ import annotations

import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .verifier import Base64Url

#: Ignite Ember's licence signing keys: key id → raw Ed25519 public key, base64url.
RELEASE_KEYS: dict[str, str] = {'ie-2026': '0VYq6jXE1VdFxmb6U1xmUPCoUxhDQ92pyUhLDQJYyTg'}

#: This package's directory: where the release marker is, and where the search for a checkout starts.
PACKAGE_DIR = Path(__file__).resolve().parent
#: Written by the image build. While it exists, no development key is ever trusted.
RELEASE_MARKER = PACKAGE_DIR / 'RELEASE'
#: Where a source checkout keeps the development keys, relative to the checkout's root.
DEV_KEYS_FILE = Path('tests') / 'fixtures' / 'licence' / 'dev-keys.json'


class TrustedKeys:
    """The keys a process accepts licences from."""

    @staticmethod
    def parse(keys: dict[str, str]) -> dict[str, Ed25519PublicKey]:
        return {key_id: Ed25519PublicKey.from_public_bytes(Base64Url.decode(raw)) for key_id, raw in keys.items()}

    @staticmethod
    def is_release() -> bool:
        return RELEASE_MARKER.exists()

    @staticmethod
    def dev_keys_file() -> Path | None:
        """The source checkout's development keys file, or None outside a checkout."""
        for directory in PACKAGE_DIR.parents:
            if (directory / 'pyproject.toml').is_file():
                candidate = directory / DEV_KEYS_FILE
                return candidate if candidate.is_file() else None
        return None

    @classmethod
    def development(cls) -> dict[str, str]:
        """The checkout's development keys: none in a release, none outside a checkout, never a release id."""
        if cls.is_release():
            return {}
        path = cls.dev_keys_file()
        if path is None:
            return {}
        keys: dict[str, str] = json.loads(path.read_text(encoding='utf-8'))
        return {key_id: raw for key_id, raw in keys.items() if key_id not in RELEASE_KEYS}

    @classmethod
    def for_environment(cls, app_env: str) -> dict[str, Ed25519PublicKey]:
        """The release keys; and a source checkout's development keys too, in development only."""
        keys = dict(RELEASE_KEYS)
        if app_env == 'dev':
            keys.update(cls.development())
        return cls.parse(keys)
