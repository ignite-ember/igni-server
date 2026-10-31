"""Revoked grants

A client signing a person out revokes their refresh token (RFC 7009),
which stops every refresh token of that sign-in. Refresh tokens are not
stored, so the revoked sign-ins are, until the last of their tokens
would have expired. See `docs/service-identity.md` §3.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-24

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'revoked_grants',
        sa.Column('grant_id', sa.Text(), nullable=False),
        sa.Column('client_id', sa.Text(), nullable=False),
        sa.Column('revoked_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('expires_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['client_id'], ['registered_clients.client_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('grant_id'),
    )
    op.create_index('idx_revoked_grants_expires_at', 'revoked_grants', ['expires_at'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_revoked_grants_expires_at', table_name='revoked_grants')
    op.drop_table('revoked_grants')
