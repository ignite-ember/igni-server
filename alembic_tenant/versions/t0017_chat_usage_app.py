"""Chat usage of an app's or a space's VM names the app

`docs/service-identity.md` §11, §17.

Revision ID: t0017
Revises: t0016
Create Date: 2026-10-28

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 't0017'
down_revision: Union[str, Sequence[str], None] = 't0016'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('chat_usage', sa.Column('app_id', sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column('chat_usage', 'app_id')
