"""VM profiles, and the one each group and app has

`docs/service-identity.md` §12. A null `vm_profile_id` is the
organisation's default profile; deleting a profile puts its groups and
apps back on it.

Revision ID: t0006
Revises: t0005
Create Date: 2026-10-26

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = 't0006'
down_revision: Union[str, Sequence[str], None] = 't0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'vm_profiles',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('is_default', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('memory_mib', sa.Integer(), nullable=False),
        sa.Column('vcpus', sa.Integer(), nullable=False),
        sa.Column('idle_before_suspend_s', sa.Integer(), nullable=True),
        sa.Column('drop_caches_before_suspend', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('packages', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('setup_script', sa.Text(), nullable=True),
        sa.Column('created_by_user_id', sa.Text(), nullable=True),
        sa.Column('updated_by_user_id', sa.Text(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_vm_profiles_name'),
    )
    op.create_index(
        'uq_vm_profiles_one_default', 'vm_profiles', ['is_default'], unique=True, postgresql_where=sa.text('is_default')
    )
    for table in ('org_groups', 'org_apps'):
        op.add_column(table, sa.Column('vm_profile_id', sa.Text(), nullable=True))
        op.create_foreign_key(
            f'{table}_vm_profile_id_fkey', table, 'vm_profiles', ['vm_profile_id'], ['id'], ondelete='SET NULL'
        )


def downgrade() -> None:
    for table in ('org_apps', 'org_groups'):
        op.drop_constraint(f'{table}_vm_profile_id_fkey', table, type_='foreignkey')
        op.drop_column(table, 'vm_profile_id')
    op.drop_index('uq_vm_profiles_one_default', table_name='vm_profiles')
    op.drop_table('vm_profiles')
