"""Inference nodes serve chat only: the pools column goes

igni server routed two pools, ``chat`` and ``codeindex``, over one list of
nodes, and each node said which it served. Nothing in igni server ever
took a ``codeindex`` lease: CodeIndex runs its own nodes from its own
configuration. So a node is just a node, and ``inference_nodes.pools`` is
dropped. The downgrade puts it back with every node serving ``chat``.

Revision ID: 0029
Revises: 0028
Create Date: 2026-11-07

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0029'
down_revision: Union[str, Sequence[str], None] = '0028'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('inference_nodes', 'pools')


def downgrade() -> None:
    op.add_column(
        'inference_nodes',
        sa.Column('pools', sa.Text(), nullable=False, server_default=sa.text("'chat'")),
    )
