"""Revocations count in milliseconds, so a token issued just after a removal in the same second stands

Tokens carry `iat_ms`; a revocation's `not_before_ms` is compared with
it (`docs/service-identity.md` §21).

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-28

"""

from typing import Sequence, Union

from alembic import op

revision: str = '0023'
down_revision: Union[str, Sequence[str], None] = '0022'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute('UPDATE revocations SET not_before = not_before * 1000')
    op.alter_column('revocations', 'not_before', new_column_name='not_before_ms')


def downgrade() -> None:
    op.alter_column('revocations', 'not_before_ms', new_column_name='not_before')
    op.execute('UPDATE revocations SET not_before = not_before / 1000')
