"""GPU nodes' API keys are encrypted at rest

`inference_nodes.api_key` held each portal-added node's proxy key as
given, so a backup or a read-only SQL session carried a working
credential (F150). It is now Fernet-encrypted under
`SECRET_ENCRYPTION_KEY`, like the signing key beside it, and listed in
`tools/rotate_secret_key.py`. This encrypts the rows already there; the
downgrade decrypts them back.

Both directions skip a row already in the target form, so a run that
stopped partway can be run again. A database with no stored key needs
no `SECRET_ENCRYPTION_KEY` to migrate.

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-31

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

from app.services.secrets import decrypt_secret, encrypt_secret, SecretDecryptError

revision: str = '0027'
down_revision: Union[str, Sequence[str], None] = '0026'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _stored() -> list[tuple[str, str]]:
    rows = op.get_bind().execute(sa.text('SELECT id, api_key FROM inference_nodes WHERE api_key IS NOT NULL'))
    return [(str(row.id), str(row.api_key)) for row in rows]


def _write(row_id: str, value: str) -> None:
    op.get_bind().execute(
        sa.text('UPDATE inference_nodes SET api_key = :value WHERE id = :id'), {'value': value, 'id': row_id}
    )


def _is_ciphertext(value: str) -> bool:
    try:
        decrypt_secret(value)
        return True
    except SecretDecryptError:
        return False


def upgrade() -> None:
    # SecretCipherUnavailable propagates when there is a key to encrypt
    # and no SECRET_ENCRYPTION_KEY: the migration fails, naming the
    # setting, rather than leaving a plaintext key the code now reads as
    # ciphertext.
    for row_id, value in _stored():
        if not _is_ciphertext(value):
            _write(row_id, encrypt_secret(value))


def downgrade() -> None:
    for row_id, value in _stored():
        if _is_ciphertext(value):
            _write(row_id, decrypt_secret(value))
