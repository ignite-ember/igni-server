"""A profile's erasure of an app's VM after inactivity

`erase_after_idle_days` (null: never): an app's or a space's VM not woken
for that many days has its state and volume erased; the app stays and its
next wake starts fresh. A person's VM is never erased this way.
`docs/service-identity.md` §12.2.

Revision ID: t0023
Revises: t0022
Create Date: 2026-11-01

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 't0023'
down_revision: Union[str, Sequence[str], None] = 't0022'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('vm_profiles', sa.Column('erase_after_idle_days', sa.Integer(), nullable=True))
    op.create_check_constraint(
        'ck_vm_profiles_idle_days', 'vm_profiles', 'erase_after_idle_days IS NULL OR erase_after_idle_days >= 1'
    )


def downgrade() -> None:
    op.drop_constraint('ck_vm_profiles_idle_days', 'vm_profiles', type_='check')
    op.drop_column('vm_profiles', 'erase_after_idle_days')
