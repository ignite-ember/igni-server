"""The settings checks that are about secrets and keys.

Part of the startup pass in ``app/security/startup_checks.py``, which
interleaves them with its own checks so findings keep one fixed order.
"""

from __future__ import annotations

from cryptography.fernet import Fernet

from app.config import Settings, settings as app_settings
from app.security.deployment_findings import DeploymentFindings
from app.security.super_admin import looks_like_bcrypt

#: The floor for a shared secret presented in a header. There is no
#: attempt limit on the comparison, so length is all there is.
_MIN_SHARED_SECRET_LENGTH = 32


class SecretChecks:
    """The encryption key, its predecessors, shared secrets and the break-glass key."""

    def __init__(self, *, settings: Settings = app_settings) -> None:
        self._settings = settings

    def encryption_key(self, findings: DeploymentFindings) -> None:
        """The key that protects the signing key.

        The server signs its tokens with a key pair it generates itself
        and keeps in Postgres, encrypted under ``SECRET_ENCRYPTION_KEY``
        (app/services/identity/key_ring.py). Without that key it cannot
        store a signing key, so it cannot sign anybody in — fatal in every
        environment, dev included, because there is no insecure fallback
        to allow: the alternative is a key in plaintext in the database.
        """
        encryption_key = self._settings.secret_encryption_key
        if not encryption_key:
            findings.refuse(
                'SECRET_ENCRYPTION_KEY is not set. The server keeps its token-signing key '
                'encrypted under it, so without it nobody can sign in.',
                "generate one with `python -c 'from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())'` and set SECRET_ENCRYPTION_KEY.",
            )
            return
        try:
            Fernet(encryption_key.encode())
        except (ValueError, TypeError):
            findings.refuse(
                'SECRET_ENCRYPTION_KEY is not a valid Fernet key (32 url-safe base64 bytes), '
                'so the token-signing key cannot be stored or read.',
                'check for a truncated paste, or generate a new one.',
            )

    def shared_secret_lengths(self, findings: DeploymentFindings) -> None:
        """Shared secrets that are set but too short.

        Compared with ``compare_digest`` and answered 401 on a mismatch,
        which is right — but with no attempt limit, because it is not a
        user-facing login. So its whole strength is its length, and nothing
        checked it: ``NODE_REGISTRATION_TOKEN=test`` authenticated a GPU
        node registration when tried.

        Warned, not fatal, and only when *set*: leaving it unset is a
        supported state that disables the surface it guards — the node API
        answers 404 rather than advertising a disabled endpoint.
        """
        for name, value, guards in (
            ('NODE_REGISTRATION_TOKEN', self._settings.node_registration_token, 'GPU node registration'),
        ):
            if value and len(value) < _MIN_SHARED_SECRET_LENGTH:
                findings.warn(
                    f'{name} is only {len(value)} characters. It guards {guards}, it is '
                    f'compared without any attempt limit, so its length is the whole of '
                    f'its strength. Use at least {_MIN_SHARED_SECRET_LENGTH}.'
                )

    def break_glass_key(self, findings: DeploymentFindings) -> None:
        """A break-glass key that is set but can never work.

        A warning rather than fatal: an install with no break-glass key is
        a supported state (super admins are accounts; the key only reaches
        deployment-wide settings when nobody can sign in). What is not
        supported is a value that is set and unusable — and the natural
        thing to paste into a field named ``..._PASSWORD_HASH`` is a
        password, which bcrypt rejects with ``Invalid salt`` on every
        attempt. It fails closed, so this is about diagnosis.
        """
        password_hash = self._settings.super_admin_password_hash
        if password_hash and not looks_like_bcrypt(password_hash):
            findings.warn(
                'SUPER_ADMIN_PASSWORD_HASH is set but is not a bcrypt hash, so the '
                'break-glass key will never work. It wants the hash, not the password: '
                'python -c "import bcrypt; '
                "print(bcrypt.hashpw(b'YOUR-PASSWORD', bcrypt.gensalt()).decode())\""
            )

    def unfinished_rotation(self, findings: DeploymentFindings) -> None:
        """A rotation left half-finished, or an old key that cannot be read.

        While `SECRET_ENCRYPTION_KEYS_PREVIOUS` is set, the old key still
        decrypts everything on this deployment — which is most of what
        rotating it was for. It has to be set during a rotation and removed
        after, and the removal is the step people skip, so it is said on
        every boot rather than once. A malformed old key is worth catching
        here rather than on the first credential read, which may be days
        later.
        """
        previous_keys = [
            k.strip() for k in (self._settings.secret_encryption_keys_previous or '').split(',') if k.strip()
        ]
        if self._settings.secret_encryption_keys_previous:
            count = len(previous_keys)
            findings.warn(
                f'SECRET_ENCRYPTION_KEYS_PREVIOUS holds {count} key(s), so {count} superseded '
                f"key(s) still decrypt this deployment's stored credentials. Finish the rotation: "
                f'`python tools/rotate_secret_key.py --apply`, then unset it.'
            )
        for index, previous in enumerate(previous_keys):
            try:
                Fernet(previous.encode())
            except (ValueError, TypeError):
                findings.warn(
                    f'SECRET_ENCRYPTION_KEYS_PREVIOUS entry {index + 1} is not a valid Fernet key, so '
                    f'anything encrypted under it cannot be read. Check for a truncated paste.'
                )
