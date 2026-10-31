"""Deployment-held secrets.

One place for the encryption a self-hosted install needs when it stores
a credential belonging to someone else's system.
"""

from app.services.secrets.cipher import (
    decrypt_secret,
    encrypt_secret,
    previous_keys,
    primary_cipher,
    SecretCipherUnavailable,
    SecretDecryptError,
)

__all__ = [
    'SecretCipherUnavailable',
    'SecretDecryptError',
    'decrypt_secret',
    'encrypt_secret',
    'previous_keys',
    'primary_cipher',
]
