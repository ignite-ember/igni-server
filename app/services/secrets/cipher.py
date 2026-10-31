"""Symmetric encryption for provider secrets held at rest.

A self-hosted deployment holds credentials that must not survive a
database snapshot or a stray log dump in usable form: the private half of
its own token-signing key (`signing_keys`), the API keys of the GPU
nodes an admin added (`inference_nodes`), and each app's first VM
credential until the node agent picks it up (`vm_credentials`, in the
organisation's own database). The git-provider secrets this once covered
moved to CodeIndex with the providers.

Every column holding a ciphertext is marked ``info={'encrypted': True}``
on its model and listed in ``tools/rotate_secret_key.py``; tests keep the
two, and every caller of :func:`encrypt_secret`, in step.

The same cipher seals the few secrets the server keeps in Redis for
minutes — pending sign-in codes, handoff tokens, OIDC state, registered
nodes' API keys — through ``app/db/redis/sealed.py`` (F151). They expire
within their TTL, so the rotation tool does not walk Redis.

So they are Fernet-encrypted with ``settings.secret_encryption_key``,
which the customer supplies and we never generate. One key for the
deployment rather than one per provider: the threat is the same in every
case, and a second key would be a second thing to rotate, back up and
lose.

Mint one with::

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

## Rotation

Two settings, because one list would be ambiguous about which end is
current:

* ``SECRET_ENCRYPTION_KEY`` — the key every new write uses.
* ``SECRET_ENCRYPTION_KEYS_PREVIOUS`` — comma-separated keys that are
  accepted for *decryption only*.

Backed by ``MultiFernet``, which decrypts with any key it holds and
encrypts with the first. So a rotation is:

1. mint a new key;
2. move the current value into ``SECRET_ENCRYPTION_KEYS_PREVIOUS`` and
   put the new one in ``SECRET_ENCRYPTION_KEY``;
3. restart — everything still decrypts, new writes use the new key;
4. run ``python tools/rotate_secret_key.py --apply`` to re-encrypt the
   stored rows;
5. remove ``SECRET_ENCRYPTION_KEYS_PREVIOUS`` and restart again.

Step 5 is the one people skip, and skipping it means the old key is still
a working key for this deployment — which is most of what rotating was
for. The startup checks say so on every boot while it is set.

**Lazy migration is deliberately not offered.** `MultiFernet` makes it
easy: decrypt on read, re-encrypt on write, done. But these rows are
written when an administrator registers an App or pastes a token, which
may be never again — so "lazily" means "possibly not at all", and the old
key stays load-bearing indefinitely with nothing saying so. An explicit
pass that reports what it changed is the honest version.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.config import settings


class SecretCipherUnavailable(RuntimeError):
    """``settings.secret_encryption_key`` is missing or not a valid key.

    Distinct from a decrypt failure so callers can answer "this server is
    not configured" rather than "your secret is corrupt" — two different
    problems for two different people.
    """


class SecretDecryptError(RuntimeError):
    """The stored ciphertext will not decrypt with any configured key.

    Either the key was rotated without keeping the old one in
    ``SECRET_ENCRYPTION_KEYS_PREVIOUS``, or the row is corrupt. Nothing
    recovers this state; the credential has to be supplied again.
    """


def previous_keys() -> list[str]:
    """The decrypt-only keys, in the order given.

    Comma-separated rather than a JSON list: this value is typed into an
    env file or a Kubernetes Secret by a person mid-rotation, and
    `a,b` is harder to get wrong than `["a","b"]`. Blanks and stray
    whitespace are dropped, because a trailing comma is the obvious
    typo and refusing to start over it would be theatre.
    """
    raw = settings.secret_encryption_keys_previous or ''
    return [key.strip() for key in raw.split(',') if key.strip()]


def _build(key: str) -> Fernet:
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:
        raise SecretCipherUnavailable(f'not a valid Fernet key: {exc}') from exc


def primary_cipher() -> Fernet:
    """The key new writes use, on its own.

    Used by the rotation tool to ask "is this row already on the current
    key", which `_cipher()` cannot answer because it accepts the old
    ones too.
    """
    key = settings.secret_encryption_key
    if not key:
        raise SecretCipherUnavailable(
            'secret_encryption_key is not configured; set it before storing any provider credential'
        )
    return _build(key)


def _cipher() -> MultiFernet:
    """Every key this deployment accepts, current first.

    `MultiFernet` encrypts with the first and decrypts with any, which is
    the whole mechanism: during a rotation both keys work, and after the
    re-encrypt pass the old one can be removed.
    """
    keys = [primary_cipher()]

    for index, previous in enumerate(previous_keys()):
        try:
            keys.append(_build(previous))
        except SecretCipherUnavailable as exc:
            # Named by position, because the value is a comma-separated
            # list and "one of your old keys is malformed" is not
            # actionable.
            raise SecretCipherUnavailable(
                f'secret_encryption_keys_previous entry {index + 1} is not a valid Fernet key: {exc}'
            ) from exc

    return MultiFernet(keys)


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a credential for at-rest storage.

    Always yields URL-safe base64, so the result drops into a ``Text``
    column without further encoding.
    """
    if not plaintext:
        raise ValueError('cannot encrypt an empty secret')
    return _cipher().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt a stored ciphertext back to the raw credential."""
    try:
        return _cipher().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise SecretDecryptError(
            'failed to decrypt a stored secret with any configured key — if you rotated '
            'SECRET_ENCRYPTION_KEY, put the old value in SECRET_ENCRYPTION_KEYS_PREVIOUS '
            'and run `python tools/rotate_secret_key.py --apply`'
        ) from exc
