"""Chat usage names the backend that served it, and its kind

With cloud model providers next to the customer's own nodes, the same
model can be served by a GPU node or by a cloud, and only one of those is
billed by the cloud. Null for every row written before.

Revision ID: t0030
Revises: t0029
Create Date: 2026-11-13

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 't0030'
down_revision: Union[str, Sequence[str], None] = 't0029'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('chat_usage', sa.Column('backend_id', sa.String(255), nullable=True))
    op.add_column('chat_usage', sa.Column('kind', sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column('chat_usage', 'kind')
    op.drop_column('chat_usage', 'backend_id')
