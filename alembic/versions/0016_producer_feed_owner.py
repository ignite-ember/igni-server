"""A space's row in the producer feed names its owning app

So the gateway provisions a space with its owner before any of its
tokens connects. See `docs/service-identity.md` §11.3.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-27

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0016'
down_revision: Union[str, Sequence[str], None] = '0015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('producer_feed', sa.Column('owner_app_id', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('producer_feed', 'owner_app_id')
