"""Secrets this server holds in Redis, kept useless to a copy of Redis. F151.

Postgres keeps every stored credential encrypted under
``SECRET_ENCRYPTION_KEY`` (DP-1). Redis held a few in plaintext, each for
minutes: one-time sign-in codes, the codes that hand a fresh token to the
browser (F107), OAuth authorization codes, the OIDC state with its PKCE
verifier, and a self-registered GPU node's API key. Short-lived is not
the same as unexposed: an RDB snapshot, an AOF file, a replica, ``MONITOR``
or a read on a shared Redis carries whatever was live at that moment.

Two tools, chosen by what the server needs back:

* **A code it only has to recognise** is stored under a digest of itself.
  Every such code here is ``secrets.token_urlsafe(32)`` — 256 bits — so a
  plain SHA-256 cannot be walked back, and needs no key. A short code
  would need a keyed HMAC instead; none is used as a key.
* **A value it has to hand back** — the token behind a handoff code, the
  OIDC verifier, a node's API key, and the six-digit sign-in code, which
  is *resent* rather than rotated while it is pending (see
  ``OTPService.generate_code``) — is encrypted with the deployment's
  cipher, the same ``MultiFernet`` the database uses. A six-digit code is
  a million candidates; encrypted, nothing in Redis narrows them without
  the key, which Redis never holds.

## Rotation

The rotation tool does not walk Redis and need not: every entry sealed
here expires within its TTL — ten minutes at the longest, a node's
registration three. During a rotation the previous key still decrypts
them; once ``SECRET_ENCRYPTION_KEYS_PREVIOUS`` is cleared, anything still
sealed under it fails to decrypt and is treated as absent (a code asks
for a new one, a node re-registers on its next heartbeat). Waiting ten
minutes between the restart with both keys and clearing the old one
avoids even that.
"""

from __future__ import annotations

import hashlib

from app.services.secrets import decrypt_secret, encrypt_secret

__all__ = ['digest', 'seal', 'unseal']


def digest(code: str) -> str:
    """What a high-entropy code is stored under, instead of the code.

    SHA-256, unkeyed, which is enough only because every caller's code
    carries 256 bits of randomness. Do not use it for anything a person
    could type.
    """
    return hashlib.sha256(code.encode()).hexdigest()


def seal(plaintext: str) -> str:
    """Encrypt a value for Redis. See the module docstring for rotation."""
    return encrypt_secret(plaintext)


def unseal(sealed: str) -> str:
    """Decrypt a sealed value. Raises ``SecretDecryptError`` if no configured key opens it."""
    return decrypt_secret(sealed)
