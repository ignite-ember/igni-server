"""Signing keys and registered clients

igni signs its tokens with a key pair it generates and keeps here, and
publishes the public half. Services and apps register their own public
keys and, once a super admin approves them, obtain tokens with them.
See `docs/service-identity.md`.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-24

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'signing_keys',
        sa.Column('kid', sa.Text(), nullable=False),
        sa.Column('algorithm', sa.Text(), nullable=False),
        sa.Column('encrypted_private_key', sa.Text(), nullable=False),
        sa.Column('public_jwk', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('activates_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('kid'),
    )
    op.create_index('idx_signing_keys_activates_at', 'signing_keys', ['activates_at'], unique=False)
    op.create_table(
        'registered_clients',
        sa.Column('client_id', sa.Text(), nullable=False),
        sa.Column('kind', sa.Text(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('base_url', sa.Text(), nullable=False),
        sa.Column('redirect_uris', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('jwks', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('thumbprint', sa.Text(), nullable=False),
        sa.Column('state', sa.Text(), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('decided_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('decided_by_user_id', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['decided_by_user_id'], ['users.user_id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('client_id'),
    )
    op.create_index('idx_registered_clients_thumbprint', 'registered_clients', ['thumbprint'], unique=True)
    op.create_index('idx_registered_clients_state', 'registered_clients', ['state'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_registered_clients_state', table_name='registered_clients')
    op.drop_index('idx_registered_clients_thumbprint', table_name='registered_clients')
    op.drop_table('registered_clients')
    op.drop_index('idx_signing_keys_activates_at', table_name='signing_keys')
    op.drop_table('signing_keys')
