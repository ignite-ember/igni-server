"""The platform's caps on VM profiles, and each organisation's quota of awake VM time

See `docs/service-identity.md` §12. The quota is stored, not enforced yet.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-26

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0012'
down_revision: Union[str, Sequence[str], None] = '0011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'platform_vm_caps',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('max_memory_mib', sa.Integer(), nullable=False),
        sa.Column('max_vcpus', sa.Integer(), nullable=False),
        sa.Column('max_idle_before_suspend_s', sa.Integer(), nullable=True),
        sa.Column('updated_by_user_id', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint('id = 1', name='ck_platform_vm_caps_one_row'),
        sa.ForeignKeyConstraint(['updated_by_user_id'], ['users.user_id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'org_vm_quotas',
        sa.Column('org_id', sa.Text(), nullable=False),
        sa.Column('awake_vm_seconds_per_month', sa.BigInteger(), nullable=True),
        sa.Column('updated_by_user_id', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['updated_by_user_id'], ['users.user_id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('org_id'),
    )


def downgrade() -> None:
    op.drop_table('org_vm_quotas')
    op.drop_table('platform_vm_caps')
