"""Inference nodes have a kind: the customer's own, or a cloud's

A node was always an OpenAI-compatible URL with an optional Bearer key.
A cloud model provider is addressed and signed its own way, so a node now
says which it is (`kind`, `'openai'` for every existing row), where a
Vertex model runs (`region`, `project`), the provider's own name for each
model (`model_ids`), how it authenticates (`auth`, and the method's
non-secret `auth_settings`), and the method's secret where it has one
(`credential`, Fernet-encrypted like `api_key`).

Revision ID: 0030
Revises: 0029
Create Date: 2026-11-13

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = '0030'
down_revision: Union[str, Sequence[str], None] = '0029'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('inference_nodes', sa.Column('kind', sa.Text(), nullable=False, server_default=sa.text("'openai'")))
    op.add_column('inference_nodes', sa.Column('region', sa.Text(), nullable=True))
    op.add_column('inference_nodes', sa.Column('project', sa.Text(), nullable=True))
    op.add_column('inference_nodes', sa.Column('auth', sa.Text(), nullable=True))
    # Every row there is was a GPU node: its key, if it has one, is its method.
    op.execute("UPDATE inference_nodes SET auth = CASE WHEN api_key IS NULL THEN 'none' ELSE 'api_key' END")
    op.alter_column('inference_nodes', 'auth', nullable=False)
    op.add_column(
        'inference_nodes',
        sa.Column('auth_settings', postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column('inference_nodes', sa.Column('credential', sa.Text(), nullable=True))
    op.add_column(
        'inference_nodes',
        sa.Column('model_ids', postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )


def downgrade() -> None:
    # A cloud node cannot be expressed without its kind: it would be read
    # back as an OpenAI-compatible URL and sent the wrong credentials.
    op.execute("DELETE FROM inference_nodes WHERE kind <> 'openai'")
    for column in ('model_ids', 'credential', 'auth_settings', 'auth', 'project', 'region', 'kind'):
        op.drop_column('inference_nodes', column)
