"""Idle leads to a pause: the platform's cap is `max_idle_before_pause_s`

`docs/service-identity.md` §12.

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-28

"""

from typing import Sequence, Union

from alembic import op

revision: str = '0024'
down_revision: Union[str, Sequence[str], None] = '0023'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('platform_vm_caps', 'max_idle_before_suspend_s', new_column_name='max_idle_before_pause_s')


def downgrade() -> None:
    op.alter_column('platform_vm_caps', 'max_idle_before_pause_s', new_column_name='max_idle_before_suspend_s')
