"""The producer feed says when a producer was removed, not only that it is inactive

A VM's volume and keys are erased only when its producer is removed by
its owner — a space deleted, an app deleted, an account deleted or
erased, an organisation deleted — never because it is inactive. Only a
removed row is purged, counted from when the gateway was handed it
(`seen_removed_at`). See `docs/service-identity.md` §11.3.

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-27

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0017'
down_revision: Union[str, Sequence[str], None] = '0016'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'producer_feed', sa.Column('removed', sa.Boolean(), server_default=sa.text('false'), nullable=False)
    )
    op.alter_column('producer_feed', 'seen_inactive_at', new_column_name='seen_removed_at')


def downgrade() -> None:
    op.alter_column('producer_feed', 'seen_removed_at', new_column_name='seen_inactive_at')
    op.drop_column('producer_feed', 'removed')
