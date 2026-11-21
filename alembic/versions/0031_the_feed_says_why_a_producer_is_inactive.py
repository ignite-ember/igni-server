"""The feed says why a producer is inactive

A person whose group runs igni only on their computers is listed
inactive, and the gateway refuses to wake their VM with a reason the
desktop can show (`group_local_only`). Null on every existing row: they
are active, or inactive for the reasons the feed always had.

Revision ID: 0031
Revises: 0030
Create Date: 2026-11-20

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0031'
down_revision: Union[str, Sequence[str], None] = '0030'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('producer_feed', sa.Column('inactive_reason', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('producer_feed', 'inactive_reason')
