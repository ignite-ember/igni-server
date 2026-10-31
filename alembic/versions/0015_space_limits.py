"""The platform's caps on an app's spaces, and each organisation's own limits

See `docs/service-identity.md` §17.4. `null` is no limit.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-27

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0015'
down_revision: Union[str, Sequence[str], None] = '0014'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'platform_space_caps',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('max_spaces_per_app', sa.Integer(), nullable=True),
        sa.Column('max_spaces_per_org', sa.Integer(), nullable=True),
        sa.Column('updated_by_user_id', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint('id = 1', name='ck_platform_space_caps_one_row'),
        sa.ForeignKeyConstraint(['updated_by_user_id'], ['users.user_id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'org_space_quotas',
        sa.Column('org_id', sa.Text(), nullable=False),
        sa.Column('max_spaces_per_app', sa.Integer(), nullable=True),
        sa.Column('max_spaces', sa.Integer(), nullable=True),
        sa.Column('updated_by_user_id', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['updated_by_user_id'], ['users.user_id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('org_id'),
    )


def downgrade() -> None:
    op.drop_table('org_space_quotas')
    op.drop_table('platform_space_caps')
