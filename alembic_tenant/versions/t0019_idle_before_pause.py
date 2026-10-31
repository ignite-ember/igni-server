"""Idle leads to a pause: a profile's `idle_before_pause_s`, 10 s for the Standard one

`docs/service-identity.md` §12.

Revision ID: t0019
Revises: t0018
Create Date: 2026-10-28

"""

from typing import Sequence, Union

from alembic import op

revision: str = 't0019'
down_revision: Union[str, Sequence[str], None] = 't0018'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('vm_profiles', 'idle_before_suspend_s', new_column_name='idle_before_pause_s')
    # The default profile still on the old default moves to the new one.
    op.execute('UPDATE vm_profiles SET idle_before_pause_s = 10 WHERE is_default AND idle_before_pause_s = 5')


def downgrade() -> None:
    op.alter_column('vm_profiles', 'idle_before_pause_s', new_column_name='idle_before_suspend_s')
