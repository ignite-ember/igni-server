"""Chat usage counts what the prompt cache served and stored

Anthropic's Messages API reports the prompt in three parts: read fresh,
read from the prompt cache, and written to it. `input_tokens` stays the
whole prompt; these two say how much of it the cache read and wrote,
which is most of what a cloud bills differently. Null before t0031, and
for a backend that does not report them.

Revision ID: t0031
Revises: t0030
Create Date: 2026-11-14

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 't0031'
down_revision: Union[str, Sequence[str], None] = 't0030'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('chat_usage', sa.Column('cache_read_tokens', sa.Integer(), nullable=True))
    op.add_column('chat_usage', sa.Column('cache_write_tokens', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('chat_usage', 'cache_write_tokens')
    op.drop_column('chat_usage', 'cache_read_tokens')
