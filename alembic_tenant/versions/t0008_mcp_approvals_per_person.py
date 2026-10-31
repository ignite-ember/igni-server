"""A person's approvals of MCP servers

`docs/service-identity.md` §14.

Revision ID: t0008
Revises: t0007
Create Date: 2026-10-26

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 't0008'
down_revision: Union[str, Sequence[str], None] = 't0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'person_mcp_approvals',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('server_name', sa.Text(), nullable=False),
        sa.Column('source', sa.Text(), nullable=False),
        sa.Column('approved_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'server_name', 'source', name='uq_person_mcp_approvals_user_server_source'),
    )
    op.create_index('idx_person_mcp_approvals_user', 'person_mcp_approvals', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_person_mcp_approvals_user', table_name='person_mcp_approvals')
    op.drop_table('person_mcp_approvals')
