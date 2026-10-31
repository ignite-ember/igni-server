"""Volumes: the platform's cap on a profile's largest, and each feed row's size

See `docs/service-identity.md` §12, §12.1 and §11.3.

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-27

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0019'
down_revision: Union[str, Sequence[str], None] = '0018'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'platform_vm_caps',
        sa.Column('max_volume_gib', sa.Integer(), server_default=sa.text('200'), nullable=False),
    )
    op.add_column('producer_feed', sa.Column('volume_gib', sa.Integer(), server_default=sa.text('10'), nullable=False))
    op.alter_column('producer_feed', 'volume_gib', server_default=None)


def downgrade() -> None:
    op.drop_column('producer_feed', 'volume_gib')
    op.drop_column('platform_vm_caps', 'max_volume_gib')
