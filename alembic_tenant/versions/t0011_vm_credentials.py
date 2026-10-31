"""Each producer's VM holds a credential of its own

The platform never mints tokens for producers. A VM credential (audience
`igni-vm`) rotates on every use, a reused one revokes its family, and it
stops after 90 days unused (`docs/service-identity.md` §16).

Revision ID: t0011
Revises: t0010
Create Date: 2026-10-26

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = 't0011'
down_revision: Union[str, Sequence[str], None] = 't0010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'vm_credentials',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('producer_kind', sa.Text(), nullable=False),
        sa.Column('producer_id', sa.Text(), nullable=False),
        sa.Column('audience', sa.Text(), nullable=False),
        sa.Column('generation', sa.Integer(), nullable=False),
        sa.Column('secret_hash', sa.Text(), nullable=False),
        sa.Column('previous_hashes', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('pending_pickup', sa.Text(), nullable=True),
        sa.Column('picked_up_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('picked_up_by', sa.Text(), nullable=True),
        sa.Column('created_by_kind', sa.Text(), nullable=False),
        sa.Column('created_by_id', sa.Text(), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_used_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('revoked_reason', sa.Text(), nullable=True),
        sa.Column('revoked_by_id', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'uq_vm_credentials_one_active',
        'vm_credentials',
        ['producer_kind', 'producer_id'],
        unique=True,
        postgresql_where=sa.text('revoked_at IS NULL'),
    )


def downgrade() -> None:
    op.drop_index('uq_vm_credentials_one_active', table_name='vm_credentials')
    op.drop_table('vm_credentials')
