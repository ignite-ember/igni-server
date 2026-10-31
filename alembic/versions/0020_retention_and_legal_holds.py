"""A removed member is erased after the organisation's retention, unless a legal hold stops it

`docs/service-identity.md` §18, §11.3.

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-27

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0020'
down_revision: Union[str, Sequence[str], None] = '0019'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('producer_feed', sa.Column('removed_at', sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column('producer_feed', sa.Column('erase_after_days', sa.Integer(), nullable=True))
    op.add_column('producer_feed', sa.Column('erase_at', sa.TIMESTAMP(timezone=True), nullable=True))
    op.create_table(
        'platform_retention',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('max_removed_member_retention_days', sa.Integer(), server_default=sa.text('365'), nullable=False),
        sa.Column('updated_by_user_id', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint('id = 1', name='ck_platform_retention_one_row'),
        sa.ForeignKeyConstraint(['updated_by_user_id'], ['users.user_id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'org_retention',
        sa.Column('org_id', sa.Text(), nullable=False),
        sa.Column('removed_member_retention_days', sa.Integer(), server_default=sa.text('30'), nullable=False),
        sa.Column('updated_by_user_id', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['updated_by_user_id'], ['users.user_id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('org_id'),
    )
    op.create_table(
        'legal_holds',
        sa.Column('org_id', sa.Text(), nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('placed_by_user_id', sa.Text(), nullable=False),
        sa.Column('placed_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('org_id', 'user_id', name='pk_legal_holds'),
    )


def downgrade() -> None:
    op.drop_table('legal_holds')
    op.drop_table('org_retention')
    op.drop_table('platform_retention')
    op.drop_column('producer_feed', 'erase_at')
    op.drop_column('producer_feed', 'erase_after_days')
    op.drop_column('producer_feed', 'removed_at')
