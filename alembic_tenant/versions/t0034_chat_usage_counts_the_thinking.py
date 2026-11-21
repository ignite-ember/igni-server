"""Chat usage counts the thinking within the output

`output_tokens` is everything the model generated, its thinking included,
as every provider bills it — Vertex AI's Gemini reports thinking outside
`completion_tokens`, and the server now adds it back. This says how much
of the output was thinking, where the backend reports it. Null before
t0034, and for a backend that does not report it.

Revision ID: t0034
Revises: t0033
Create Date: 2026-11-21

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 't0034'
down_revision: Union[str, Sequence[str], None] = 't0033'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('chat_usage', sa.Column('reasoning_tokens', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('chat_usage', 'reasoning_tokens')
